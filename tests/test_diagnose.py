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
    # Adapter gaps are expected: this fixture has no tokens or cost. The
    # undefined bucket is expected too: nothing errored, so there is no
    # recovery to rate. Neither is a proposal for a change.
    assert kinds <= {"measurement", "undefined_measurement"}
    for kind in ("measurement", "undefined_measurement"):
        proposals = P.build([f for f in found if f["kind"] == kind])
        assert proposals, f"{kind} findings must still be reported"
        assert all(p["prediction"] is None for p in proposals)


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
# Undefined is not the same as unobserved.
#
# recovery_rate over zero errors is unavailable because the question does not
# apply, not because the adapter failed to report something. Before this was
# split out, a run with a fully-capable adapter and a fully-declared task set
# was told to "raise the adapter level" for a metric that no adapter level can
# produce, and the count of fixable metrics was inflated by one.
# ---------------------------------------------------------------------------


def test_a_fully_observed_run_reports_no_adapter_gaps():
    """The adapter missed nothing, so there is nothing for it to go and fix."""
    steps = normalize_trace(
        [
            {"kind": "model_call", "result_kind": "ok", "started_ms": 0, "duration_ms": 10,
             "tokens": {"input": 100, "output": 20, "cache_read": 10, "cache_write": 5},
             "cost_usd": 0.001, "model": "m", "context_tokens": 120},
            {"kind": "tool_call", "tool": "read_file", "args": {"p": "a"}, "result_kind": "ok",
             "started_ms": 10, "duration_ms": 5,
             "tokens": {"input": 5, "output": 1, "cache_read": 0, "cache_write": 0},
             "cost_usd": 0.0001, "context_tokens": 130},
            {"kind": "tool_call", "tool": "write", "args": {"p": "a"}, "result_kind": "ok",
             "started_ms": 15, "duration_ms": 5,
             "tokens": {"input": 5, "output": 1, "cache_read": 0, "cache_write": 0},
             "cost_usd": 0.0001, "context_tokens": 140},
        ]
    )
    metrics = compute(steps, {"optimal_steps": 2, "success": True})
    assert metrics["recovery_rate"]["status"] == "unavailable", "the premise: no errors occurred"

    agg = aggregate([metrics])
    found = F.analyze(agg, [{"task_id": "t1", "tags": [], "metrics": metrics, "steps": steps}], {})

    kinds = {f["kind"] for f in found}
    assert "measurement" not in kinds, (
        "every field was supplied; there is no adapter gap to report. "
        f"findings: {[f['id'] for f in found]}"
    )
    undefined = next(f for f in found if f["kind"] == "undefined_measurement")
    assert [e["metric"] for e in undefined["evidence"]] == ["recovery_rate"]
    assert undefined["measured"]["unavailable_metrics"] == 0


def test_recovery_rate_is_never_counted_as_an_adapter_gap():
    """The split, stated as the invariant: it is undefined, and it says so."""
    runs = [make_task("t1", [("read_file", "a"), ("read_file", "b")])]
    found, agg = analyze(runs)
    assert agg["recovery_rate"]["status"] == "unavailable"

    gap = next(f for f in found if f["kind"] == "measurement")
    assert "recovery_rate" not in [e["metric"] for e in gap["evidence"]]
    assert gap["measured"]["undefined_metrics"] == 1

    undefined = next(f for f in found if f["kind"] == "undefined_measurement")
    assert "undefined" in undefined["evidence"][0]["reason"]


def test_the_undefined_finding_carries_a_remedy_that_is_not_an_adapter_change():
    """A gap gets told to raise the adapter level. This must not be."""
    runs = [make_task("t1", [("read_file", "a"), ("read_file", "b")])]
    found, _ = analyze(runs)

    undefined = next(f for f in found if f["kind"] == "undefined_measurement")
    proposal = P.build([undefined])[0]
    assert "adapter level" not in proposal["change"]
    assert proposal["change"].startswith("Nothing to change")
    assert proposal["prediction"] is None
    # ... while the real gap keeps its actionable advice.
    gap = next(f for f in found if f["kind"] == "measurement")
    assert "Raise the adapter level" in P.build([gap])[0]["change"]


def test_a_run_with_errors_still_gets_a_measured_recovery_rate():
    """The split must not swallow the case the metric exists for."""
    steps = normalize_trace(
        [
            {"kind": "tool_call", "tool": "run", "args": {"a": 1}, "result_kind": "error",
             "started_ms": 0, "duration_ms": 10},
            {"kind": "tool_call", "tool": "run", "args": {"a": 1}, "result_kind": "ok",
             "started_ms": 10, "duration_ms": 10},
        ]
    )
    metrics = compute(steps, {})
    agg = aggregate([metrics])
    found = F.analyze(agg, [{"task_id": "t1", "tags": [], "metrics": metrics, "steps": steps}], {})
    assert not [f for f in found if f["kind"] == "undefined_measurement"], (
        "the run did contain a failure, so the rate is defined and measured"
    )


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
