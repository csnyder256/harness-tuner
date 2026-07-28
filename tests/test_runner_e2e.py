"""End to end: intake in, eight files on disk.

This suite asserts observable side effects rather than "the function returned".
A pipeline whose report renderer silently wrote nothing would satisfy a test
that only checked for an absence of exceptions, and the run would look
successful right up until someone opened the directory.

Counts here are exact. "At least 40 records" is satisfied by a merge that
duplicated every trace, which is a real bug shape and one that reads as success
in every other respect.
"""

from __future__ import annotations

import json
import os

import pytest

from harness_tuner.config import Config
from harness_tuner.conformance import suite_root
from harness_tuner.protocol import ARTIFACT_NAMES, read_trace
from harness_tuner.protocol.artifacts import SIDE_FILES
from harness_tuner.run.observer import IntakeError, collect
from harness_tuner.runner import execute

FIXTURES = os.path.join(suite_root(), "fixtures")

#: Hand-totalled from the fixture files:
#: l0_minimal 4, l1_full 6, looping 6, rework_reads 5, errors_recovered 4,
#: errors_unrecovered 5, context_growth 4, cache_cold 3, human_in_loop 5,
#: redacted_args 5, bad_optimal_steps 3.
EXPECTED_TASKS = 11
EXPECTED_STEPS = 50
PER_TASK_STEPS = {
    "l0_minimal": 4,
    "l1_full": 6,
    "looping": 6,
    "rework_reads": 5,
    "errors_recovered": 4,
    "errors_unrecovered": 5,
    "context_growth": 4,
    "cache_cold": 3,
    "human_in_loop": 5,
    "redacted_args": 5,
    "bad_optimal_steps": 3,
}


@pytest.fixture(scope="module")
def rundir(tmp_path_factory):
    out = tmp_path_factory.mktemp("runs")
    cfg = Config(
        {
            "harness": {"id": "fixture-harness", "adapter_level": "L1"},
            "mode": {"subject": "harness", "execution": "observe"},
            "models": {"harness": "reference-model"},
            "report": {"min_tasks_for_fingerprint": 3},
        },
        source="<test>",
    )
    records = collect(FIXTURES)
    return execute(cfg, records, str(out))


def test_sanity_the_fixture_totals_are_what_the_files_say():
    """Guard the hand-totalled constants above against fixture edits."""
    records = collect(FIXTURES)
    assert len(records) == EXPECTED_TASKS
    actual = {r["task_id"]: len(r["steps"]) for r in records}
    assert actual == PER_TASK_STEPS
    assert sum(actual.values()) == EXPECTED_STEPS


def test_all_eight_artifacts_exist_by_exact_name(rundir):
    """Every artifact present, and nothing undeclared beside them.

    Both halves matter. A missing artifact is an obvious break; an extra file
    nobody declared is how a run directory slowly stops being a contract.
    """
    on_disk = {n for n in os.listdir(rundir.root) if os.path.isfile(os.path.join(rundir.root, n))}

    assert set(ARTIFACT_NAMES) <= on_disk, f"missing artifact(s): {set(ARTIFACT_NAMES) - on_disk}"
    undeclared = on_disk - set(ARTIFACT_NAMES) - set(SIDE_FILES)
    assert not undeclared, (
        f"undeclared file(s) in a run directory: {undeclared}. Either they are artifacts "
        "and belong in ARTIFACTS, or they are working files and belong in SIDE_FILES."
    )

    for name in ARTIFACT_NAMES:
        assert os.path.getsize(os.path.join(rundir.root, name)) > 0, f"{name} is empty"
    assert rundir.missing() == []
    assert len(ARTIFACT_NAMES) == 8


def test_merged_trace_has_exactly_one_record_per_step(rundir):
    """Exact, not minimum. A merge bug that emitted every record twice would
    pass 'at least 47' while doubling every count in the report."""
    with open(rundir.path("trace"), "r", encoding="utf-8") as handle:
        lines = [ln for ln in handle if ln.strip()]
    assert len(lines) == EXPECTED_STEPS

    records = [json.loads(ln) for ln in lines]
    assert len(records) == EXPECTED_STEPS

    by_task: dict[str, int] = {}
    for record in records:
        assert "x_task_id" in record, "a merged record lost its task attribution"
        by_task[record["x_task_id"]] = by_task.get(record["x_task_id"], 0) + 1
    assert by_task == PER_TASK_STEPS


def test_per_task_traces_are_written_and_reparse(rundir):
    for task_id, count in PER_TASK_STEPS.items():
        path = rundir.task_path(task_id, "trace")
        assert os.path.isfile(path), f"no per-task trace for {task_id}"
        steps = read_trace(path)
        assert len(steps) == count
        assert [s["step"] for s in steps] == list(range(1, count + 1))


def test_merged_and_per_task_records_differ_only_by_attribution(rundir):
    """The protocol promises exactly this, so it is worth pinning."""
    merged: dict[str, list[dict]] = {}
    with open(rundir.path("trace"), "r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rec = json.loads(line)
                merged.setdefault(rec["x_task_id"], []).append(rec)

    for task_id, records in merged.items():
        with open(rundir.task_path(task_id, "trace"), "r", encoding="utf-8") as handle:
            per_task = [json.loads(ln) for ln in handle if ln.strip()]
        assert len(per_task) == len(records)
        for a, b in zip(records, per_task):
            stripped = {k: v for k, v in a.items() if k != "x_task_id"}
            assert stripped == b


def test_run_json_counts_match_the_traces(rundir):
    with open(rundir.path("run"), "r", encoding="utf-8") as handle:
        run = json.load(handle)
    assert run["task_count"] == EXPECTED_TASKS
    assert len(run["tasks"]) == EXPECTED_TASKS
    assert sum(t["step_count"] for t in run["tasks"]) == EXPECTED_STEPS
    assert run["htp_version"] == "1"
    assert run["engine"]["name"] == "harness-tuner"
    # the run is self-describing about what produced it
    assert run["config"]["models"]["harness"] == "reference-model"
    assert run["config"]["harness"]["id"] == "fixture-harness"


def test_metrics_json_carries_every_task_and_the_registry(rundir):
    from harness_tuner.protocol import REGISTRY

    with open(rundir.path("metrics"), "r", encoding="utf-8") as handle:
        doc = json.load(handle)
    assert len(doc["per_task"]) == EXPECTED_TASKS
    assert set(doc["aggregate"]) == set(REGISTRY)
    for task in doc["per_task"]:
        assert set(task["metrics"]) == set(REGISTRY), f"{task['task_id']} is missing metrics"

    # step_count sums to the hand-totalled figure
    assert doc["aggregate"]["step_count"]["value"] == EXPECTED_STEPS


def test_capabilities_says_it_was_not_scanned_when_absent(rundir):
    """Silence about capabilities would imply a scan happened and found nothing."""
    with open(rundir.path("capabilities"), "r", encoding="utf-8") as handle:
        caps = json.load(handle)
    assert caps["status"] == "not_scanned"
    assert "no capabilities file was supplied" in caps["reason"]
    assert caps["capabilities"] == {}


def test_manifest_can_reproduce_the_task_set(rundir):
    with open(rundir.path("manifest"), "r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    assert len(manifest["task_set"]) == EXPECTED_TASKS
    assert manifest["config_digest"].startswith("sha256:")
    assert manifest["task_set_digest"].startswith("sha256:")
    assert manifest["engine_version"]


def test_report_html_is_self_contained(rundir):
    with open(rundir.path("report"), "r", encoding="utf-8") as handle:
        html = handle.read()
    assert html.startswith("<!doctype html>")
    assert "</html>" in html
    # no dependency the machine might not have
    for forbidden in ("<script src=", "http://", "https://", "cdn."):
        assert forbidden not in html, f"report.html reaches for {forbidden}"


def test_summary_lists_the_blind_spots_before_the_numbers(rundir):
    with open(rundir.path("summary"), "r", encoding="utf-8") as handle:
        text = handle.read()
    assert text.index("could not measure") < text.index("## Measured")


def test_intake_rejects_a_file_with_no_trace(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"task_id": "x"}), encoding="utf-8")
    with pytest.raises(IntakeError, match="no 'trace' key"):
        collect(str(bad))


def test_intake_rejects_an_invalid_step(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"trace": [{"kind": "nope"}]}), encoding="utf-8")
    with pytest.raises(IntakeError, match="kind must be one of"):
        collect(str(bad))


def test_intake_is_ordered_so_runs_are_reproducible(tmp_path):
    for name in ("c", "a", "b"):
        (tmp_path / f"{name}.json").write_text(
            json.dumps({"trace": [{"kind": "message"}]}), encoding="utf-8"
        )
    assert [r["task_id"] for r in collect(str(tmp_path))] == ["a", "b", "c"]
