"""verify: the command that makes a proposal falsifiable.

The tests below build two real runs on disk and compare them, because the thing
worth checking is not that the e-process arithmetic works (test_eprocess.py
covers that) but that a genuine improvement, a genuine regression, and a
genuine coin flip each reach the right verdict through the whole pipeline.
"""

from __future__ import annotations

import json

import pytest

from harness_tuner.config import Config
from harness_tuner.protocol import write_json
from harness_tuner.runner import execute
from harness_tuner.stats import eprocess as E
from harness_tuner.verify import VerifyError, run_verify

CFG = Config(
    {
        "harness": {"id": "t", "adapter_level": "L1"},
        "mode": {"subject": "harness", "execution": "observe"},
        "models": {"harness": "m"},
        "report": {"min_tasks_for_fingerprint": 1, "alpha": 0.05},
    },
    source="<test>",
)


def task_record(task_id: str, *, reads: list[str], cost: float):
    """A trace whose rework_ratio and cost are set by construction."""
    steps = [
        {
            "kind": "model_call",
            "result_kind": "ok",
            "started_ms": 0,
            "duration_ms": 100,
            "tokens": {"input": 100, "output": 10, "cache_read": 0, "cache_write": 0},
            "cost_usd": cost,
            "model": "m",
            "context_tokens": 110,
        }
    ]
    for i, path in enumerate(reads):
        steps.append(
            {
                "kind": "tool_call",
                "tool": "read_file",
                "args": {"path": path},
                "result_kind": "ok",
                "started_ms": 100 + i * 10,
                "duration_ms": 10,
            }
        )
    from harness_tuner.protocol import normalize_trace

    return {
        "task_id": task_id,
        "tags": [],
        "task": {"success": True},
        "steps": normalize_trace(steps),
        "source": "test",
    }


def build_run(tmp_path, name: str, specs: list[tuple[str, list[str], float]]):
    records = [task_record(tid, reads=reads, cost=cost) for tid, reads, cost in specs]
    return execute(CFG, records, str(tmp_path / name))


def add_prediction(rundir, metric: str, direction: str, proposal_id: str = "P1"):
    with open(rundir.path("manifest"), "r", encoding="utf-8") as fh:
        manifest = json.load(fh)
    manifest["predictions"] = [
        {
            "proposal_id": proposal_id,
            "metric": metric,
            "direction": direction,
            "note": "cache the file index instead of re-reading it",
        }
    ]
    write_json(rundir.path("manifest"), manifest)


# ---------------------------------------------------------------------------


def test_a_real_improvement_is_confirmed(tmp_path):
    """Every task re-reads in the baseline and none does in the variant."""
    wasteful = [(f"t{i}", ["a.py", "b.py", "a.py", "b.py"], 0.01) for i in range(12)]
    clean = [(f"t{i}", ["a.py", "b.py", "c.py", "d.py"], 0.01) for i in range(12)]

    baseline = build_run(tmp_path, "base", wasteful)
    variant = build_run(tmp_path, "var", clean)
    add_prediction(baseline, "rework_ratio", "improve")

    doc = run_verify(baseline.root, variant.root)
    assert doc["tasks_paired"] == 12
    assert doc["tasks_unpaired"] == []

    comparison = next(c for c in doc["comparisons"] if c["metric"] == "rework_ratio")
    assert comparison["mean_baseline"] == 0.5
    assert comparison["mean_variant"] == 0.0
    assert comparison["delta"] == -0.5
    assert comparison["evidence"]["verdict"] == E.IMPROVEMENT
    assert comparison["evidence"]["wins"] == 12
    assert comparison["evidence"]["losses"] == 0

    checked = doc["predictions_checked"]
    assert len(checked) == 1
    assert checked[0]["outcome"] == "confirmed"
    assert checked[0]["proposal_id"] == "P1"


def test_a_change_that_made_things_worse_is_refuted(tmp_path):
    """The result worth acting on. A tool that could not produce this verdict
    would confirm every proposal it ever made."""
    cheap = [(f"t{i}", ["a.py"], 0.01) for i in range(12)]
    dear = [(f"t{i}", ["a.py"], 0.05) for i in range(12)]

    baseline = build_run(tmp_path, "base", cheap)
    variant = build_run(tmp_path, "var", dear)
    add_prediction(baseline, "cost_usd", "improve")

    doc = run_verify(baseline.root, variant.root)
    comparison = next(c for c in doc["comparisons"] if c["metric"] == "cost_usd")
    assert comparison["evidence"]["verdict"] == E.REGRESSION
    assert comparison["mean_variant"] > comparison["mean_baseline"]

    checked = doc["predictions_checked"][0]
    assert checked["outcome"] == "refuted"
    assert "went the other way" in checked["note"]


def test_no_real_difference_is_left_unproven(tmp_path):
    """Alternating wins and losses must not be called in either direction."""
    base_specs = []
    var_specs = []
    for i in range(12):
        if i % 2 == 0:
            base_specs.append((f"t{i}", ["a.py"], 0.01))
            var_specs.append((f"t{i}", ["a.py"], 0.02))
        else:
            base_specs.append((f"t{i}", ["a.py"], 0.02))
            var_specs.append((f"t{i}", ["a.py"], 0.01))

    baseline = build_run(tmp_path, "base", base_specs)
    variant = build_run(tmp_path, "var", var_specs)
    add_prediction(baseline, "cost_usd", "improve")

    doc = run_verify(baseline.root, variant.root)
    comparison = next(c for c in doc["comparisons"] if c["metric"] == "cost_usd")
    assert comparison["evidence"]["verdict"] == E.UNDECIDED
    assert comparison["evidence"]["wins"] == 6
    assert comparison["evidence"]["losses"] == 6

    checked = doc["predictions_checked"][0]
    assert checked["outcome"] == "unproven"
    assert "not a failed prediction" in checked["note"]


def test_unpaired_tasks_are_dropped_and_counted(tmp_path):
    """Silently matching different tasks would invent evidence."""
    baseline = build_run(tmp_path, "base", [(f"t{i}", ["a.py"], 0.01) for i in range(5)])
    variant = build_run(tmp_path, "var", [(f"t{i}", ["a.py"], 0.01) for i in range(2, 8)])

    doc = run_verify(baseline.root, variant.root)
    assert doc["tasks_paired"] == 3
    assert set(doc["tasks_unpaired"]) == {"t0", "t1", "t5", "t6", "t7"}
    comparison = doc["comparisons"][0]
    assert comparison["tasks_shared"] == 3


def test_runs_with_no_shared_tasks_are_refused(tmp_path):
    baseline = build_run(tmp_path, "base", [("a", ["x.py"], 0.01)])
    variant = build_run(tmp_path, "var", [("b", ["x.py"], 0.01)])
    with pytest.raises(VerifyError, match="share no task ids"):
        run_verify(baseline.root, variant.root)


def test_an_incomplete_run_directory_is_refused(tmp_path):
    import os

    baseline = build_run(tmp_path, "base", [("a", ["x.py"], 0.01)])
    variant = build_run(tmp_path, "var", [("a", ["x.py"], 0.01)])
    os.remove(variant.path("metrics"))
    with pytest.raises(VerifyError, match="not a complete run"):
        run_verify(baseline.root, variant.root)


def test_a_metric_neither_run_measured_is_not_comparable(tmp_path):
    baseline = build_run(tmp_path, "base", [(f"t{i}", ["a.py"], 0.01) for i in range(6)])
    variant = build_run(tmp_path, "var", [(f"t{i}", ["a.py"], 0.01) for i in range(6)])

    doc = run_verify(baseline.root, variant.root, metrics=["recovery_rate"])
    comparison = doc["comparisons"][0]
    assert comparison["tasks_comparable"] == 0
    assert comparison["evidence"]["verdict"] == E.UNDECIDED
    assert comparison["mean_baseline"] is None


def test_verify_without_a_prediction_still_compares(tmp_path):
    baseline = build_run(tmp_path, "base", [(f"t{i}", ["a.py", "a.py"], 0.01) for i in range(8)])
    variant = build_run(tmp_path, "var", [(f"t{i}", ["a.py", "b.py"], 0.01) for i in range(8)])

    doc = run_verify(baseline.root, variant.root)
    assert doc["predictions_checked"] == []
    assert any(c["metric"] == "rework_ratio" for c in doc["comparisons"])
