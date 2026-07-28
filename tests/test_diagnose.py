"""Diagnosis and proposals.

The load-bearing property here is that findings are arithmetic, not opinion. A
finding must point at specific steps, must not appear on a clean run, and must
be identical every time for the same input. The tests also pin the thing that
makes the loop close: a proposal's prediction has to arrive in the manifest in
the shape verify expects, or the whole propose-then-prove story is decoration.
"""

from __future__ import annotations

import json
import os

from harness_tuner.config import Config
from harness_tuner.diagnose import build_request, merge_response, render_markdown
from harness_tuner.diagnose import findings as F
from harness_tuner.diagnose import proposals as P
from harness_tuner.protocol import aggregate, compute, normalize_trace, write_json
from harness_tuner.runner import execute
from harness_tuner.verify import run_verify

CFG = Config(
    {
        "harness": {"id": "t", "adapter_level": "L1"},
        "mode": {"subject": "harness", "execution": "observe"},
        "models": {"harness": "m", "diagnostician": "big-model"},
        "report": {"min_tasks_for_fingerprint": 1, "alpha": 0.05},
    },
    source="<test>",
)


def make_task(task_id: str, tools: list[tuple[str, str]], **step_extra):
    steps = []
    for i, (tool, path) in enumerate(tools):
        steps.append(
            {
                "kind": "tool_call",
                "tool": tool,
                "args": {"path": path},
                "result_kind": "ok",
                "started_ms": i * 10,
                "duration_ms": 10,
                **step_extra,
            }
        )
    normalized = normalize_trace(steps)
    return {
        "task_id": task_id,
        "tags": [],
        "metrics": compute(normalized, {}),
        "steps": normalized,
    }


def analyze(task_runs, run_doc=None):
    agg = aggregate([t["metrics"] for t in task_runs])
    return F.analyze(agg, task_runs, run_doc or {}), agg


# ---------------------------------------------------------------------------


def test_a_clean_run_produces_no_change_proposals():
    """A detector that fires on everything is worth nothing."""
    clean = [
        make_task("t1", [("read_file", "a.py"), ("read_file", "b.py"), ("edit_file", "a.py")]),
        make_task("t2", [("read_file", "c.py"), ("edit_file", "c.py")]),
    ]
    found, _ = analyze(clean)
    kinds = {f["kind"] for f in found}
    assert "loop" not in kinds
    assert "rework" not in kinds
    # measurement gaps are expected: this fixture has no tokens or cost
    assert kinds <= {"measurement"}
    assert P.build([f for f in found if f["kind"] == "measurement"])[0]["prediction"] is None


def test_a_loop_is_found_and_points_at_the_exact_steps():
    looping = [make_task("t1", [("read_file", "cfg.py")] * 5 + [("submit", "x")])]
    found, _ = analyze(looping)
    loop = next(f for f in found if f["kind"] == "loop")

    assert loop["severity"] == F.CRITICAL
    assert loop["measured"]["consecutive_calls"] == 5
    evidence = loop["evidence"][0]
    assert evidence["steps"] == [1, 2, 3, 4, 5], "a finding must point at the steps it came from"
    assert evidence["tool"] == "read_file"
    assert "t1" in loop["tasks"]


def test_rework_is_found_without_a_loop():
    """Re-reads that are never consecutive still cost the same tokens."""
    runs = [make_task("t1", [("read_file", p) for p in ("a", "b", "a", "c", "b", "a")])]
    found, _ = analyze(runs)
    kinds = {f["kind"] for f in found}
    assert "rework" in kinds
    assert "loop" not in kinds

    rework = next(f for f in found if f["kind"] == "rework")
    # a seen twice more, b seen once more -> 3 repeats of 6 reads
    assert rework["measured"]["repeated_reads"] == 3


def test_findings_are_deterministic():
    runs = [make_task("t1", [("read_file", "cfg.py")] * 4)]
    first, _ = analyze(runs)
    second, _ = analyze(runs)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_severity_tracks_the_share_of_the_run():
    small = [make_task("t1", [("read_file", "a")] * 3 + [("other", str(i)) for i in range(40)])]
    big = [make_task("t1", [("read_file", "a")] * 8)]
    small_loop = next(f for f in analyze(small)[0] if f["kind"] == "loop")
    big_loop = next(f for f in analyze(big)[0] if f["kind"] == "loop")
    assert F._ORDER[big_loop["severity"]] < F._ORDER[small_loop["severity"]]


def test_a_budget_ceiling_that_cannot_fire_is_a_finding():
    runs = [make_task("t1", [("read_file", "a")])]
    run_doc = {"budget": {"blind_spots": ["a spend ceiling of 5.0 USD cannot fire"]}}
    found, _ = analyze(runs, run_doc)
    budget = next(f for f in found if f["kind"] == "budget")
    assert budget["severity"] == F.HIGH
    assert "cannot fire" in budget["evidence"][0]["problem"]


# ---------------------------------------------------------------------------
# Proposals
# ---------------------------------------------------------------------------


def test_a_proposal_never_forecasts_a_number():
    """Direction is falsifiable. '+12 percent' is not, and would not survive verify."""
    runs = [make_task("t1", [("read_file", "cfg.py")] * 5)]
    found, _ = analyze(runs)
    props = P.build(found)
    loop = next(p for p in props if p["finding_id"].startswith("loop"))

    assert loop["prediction"]["direction"] == "improve"
    assert loop["prediction"]["metric"] == "loop_max_run"
    assert "expected_gain" not in loop
    assert "percent" not in json.dumps(loop["prediction"])
    # the bound is arithmetic on what was observed, and says so
    assert "arithmetic on the observed trace" in loop["bound"]
    assert loop["applied_by"].startswith("a human")


def test_every_proposal_names_the_command_that_would_refute_it():
    runs = [make_task("t1", [("read_file", "cfg.py")] * 5)]
    props = P.build(analyze(runs)[0])
    for proposal in props:
        assert "harness-tuner verify" in proposal["falsified_by"] or not proposal["prediction"]


def test_the_diagnostician_is_told_not_to_invent_findings():
    runs = [make_task("t1", [("read_file", "cfg.py")] * 5)]
    found, _ = analyze(runs)
    request = build_request({"config": {"harness": {"id": "h"}}}, found, "big-model")
    assert "do not add findings of your own" in request["instructions"]
    assert "Do NOT re-judge" in request["instructions"]
    assert request["model_requested"] == "big-model"


def test_a_diagnostician_response_is_merged_but_not_required(tmp_path):
    runs = [make_task("t1", [("read_file", "cfg.py")] * 5)]
    props = P.build(analyze(runs)[0])
    target = props[0]

    assert merge_response(props, None) == 0
    assert "harness_specific" not in target

    path = tmp_path / "diagnosis-response.json"
    path.write_text(
        json.dumps(
            {"findings": [{"finding_id": target["finding_id"], "where": "prompts/system.md",
                           "how": "add a repeat guard", "confidence": "high"}]}
        ),
        encoding="utf-8",
    )
    assert merge_response(props, str(path)) == 1
    assert target["harness_specific"]["where"] == "prompts/system.md"
    assert target["harness_specific"]["confidence"] == "high"

    markdown = render_markdown({"run_id": "r"}, analyze(runs)[0], props)
    assert "prompts/system.md" in markdown
    assert "high confidence" in markdown


# ---------------------------------------------------------------------------
# The loop closes: a prediction written here must be readable by verify.
# ---------------------------------------------------------------------------


def test_predictions_reach_verify_in_the_shape_it_expects(tmp_path):
    def build(name, reads):
        records = [
            {
                "task_id": f"t{i}",
                "tags": [],
                "task": {},
                "steps": normalize_trace(
                    [
                        {"kind": "tool_call", "tool": "read_file", "args": {"path": p},
                         "result_kind": "ok", "started_ms": j * 10, "duration_ms": 10}
                        for j, p in enumerate(reads)
                    ]
                ),
                "source": "test",
            }
            for i in range(10)
        ]
        return execute(CFG, records, str(tmp_path / name))

    baseline = build("base", ["a", "b", "a", "b"])
    variant = build("var", ["a", "b", "c", "d"])

    # what diagnose writes
    task_runs = [
        make_task(f"t{i}", [("read_file", p) for p in ("a", "b", "a", "b")]) for i in range(10)
    ]
    props = P.build(analyze(task_runs)[0])
    preds = P.predictions(props)
    assert preds, "the rework finding must produce a prediction"

    with open(baseline.path("manifest"), "r", encoding="utf-8") as fh:
        manifest = json.load(fh)
    manifest["predictions"] = preds
    write_json(baseline.path("manifest"), manifest)

    doc = run_verify(baseline.root, variant.root)
    checked = {c["metric"]: c["outcome"] for c in doc["predictions_checked"]}
    assert checked.get("rework_ratio") == "confirmed"


def test_diagnose_writes_its_files(tmp_path):
    from harness_tuner.diagnose import DIAGNOSIS_FILE, REQUEST_FILE, main
    import argparse

    records = [
        {
            "task_id": f"t{i}",
            "tags": [],
            "task": {},
            "steps": normalize_trace(
                [
                    {"kind": "tool_call", "tool": "read_file", "args": {"path": "cfg.py"},
                     "result_kind": "ok", "started_ms": j * 10, "duration_ms": 10}
                    for j in range(5)
                ]
            ),
            "source": "test",
        }
        for i in range(3)
    ]
    rundir = execute(CFG, records, str(tmp_path / "run"))
    code = main(argparse.Namespace(run=rundir.root, response=None, no_request=False))
    assert code == 0

    for name in (DIAGNOSIS_FILE, "diagnosis.md", REQUEST_FILE):
        path = os.path.join(rundir.root, name)
        assert os.path.isfile(path), f"{name} was not written"
        assert os.path.getsize(path) > 0

    with open(rundir.path("manifest"), "r", encoding="utf-8") as fh:
        assert json.load(fh)["predictions"], "predictions must be recorded for verify"
