"""Drive mode: containment, the budget ceiling, and cassette replay.

These tests run a real subprocess. A fake harness is written to disk and
invoked exactly the way a real one would be, because the interesting failures
in this module are all at the process boundary: an argv template that does not
substitute, a harness that exits without writing anything, a ceiling that never
fires.
"""

from __future__ import annotations

import json
import os
import textwrap

import pytest

from harness_tuner.config import Config
from harness_tuner.run.budget import BudgetExceeded, Governor
from harness_tuner.run.cassette import Cassette
from harness_tuner.run.driver import (
    ContainmentError,
    DriveError,
    check_containment,
    drive,
    render_argv,
)
from harness_tuner.runner import execute

FAKE_HARNESS = textwrap.dedent(
    '''
    """A deliberately simple harness. Writes one intake file and exits."""
    import json, sys

    task_id, out_path, seed = sys.argv[1], sys.argv[2], sys.argv[3]
    cost = float(sys.argv[4]) if len(sys.argv) > 4 else 0.0

    trace = [
        {"kind": "model_call", "result_kind": "ok", "started_ms": 0, "duration_ms": 100,
         "tokens": {"input": 100, "output": 20, "cache_read": 0, "cache_write": 0},
         "cost_usd": cost, "model": "fake", "context_tokens": 120},
        {"kind": "tool_call", "tool": "read_file", "args": {"path": task_id + ".txt"},
         "result_kind": "ok", "started_ms": 100, "duration_ms": 10},
        {"kind": "tool_call", "tool": "submit", "args": {"seed": seed},
         "result_kind": "ok", "started_ms": 110, "duration_ms": 5},
    ]
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump({"task_id": task_id, "task": {"success": True}, "trace": trace}, fh)
    '''
).strip()


@pytest.fixture()
def harness_script(tmp_path):
    path = tmp_path / "fake_harness.py"
    path.write_text(FAKE_HARNESS, encoding="utf-8")
    return str(path)


def make_config(harness_script, tmp_path, *, task_cost="0.0", **overrides):
    import sys

    data = {
        "harness": {
            "id": "fake",
            "adapter": "fake-adapter",
            "adapter_level": "L2",
            "invoke": [sys.executable, harness_script, "{task_id}", "{out}", "{seed}", task_cost],
            "timeout_s": 60,
            "workspace": str(tmp_path),
        },
        "mode": {"subject": "harness", "execution": "drive"},
        "models": {"harness": "fake-model"},
        "containment": {
            "mechanism": "throwaway directory under the system temp path",
            "declared_by": "agent",
            "reach": "one empty temp directory, no network",
            "approved": True,
        },
        "cost": {"cassette": False, "budget": {"enabled": False}},
        "tasks": {"seed": 7},
        "report": {"min_tasks_for_fingerprint": 1},
    }
    for section, values in overrides.items():
        data.setdefault(section, {}).update(values)
    return Config(data, source="<test>")


def tasks(n: int):
    return [{"task_id": f"t{i}", "prompt": f"do thing {i}"} for i in range(1, n + 1)]


# ---------------------------------------------------------------------------
# Containment
# ---------------------------------------------------------------------------


def test_driving_without_approval_is_refused(harness_script, tmp_path):
    cfg = make_config(harness_script, tmp_path, containment={"approved": False})
    with pytest.raises(ContainmentError, match="containment.approved is false"):
        check_containment(cfg)


def test_approval_without_a_recorded_reach_is_refused(harness_script, tmp_path):
    cfg = make_config(harness_script, tmp_path, containment={"approved": True, "reach": ""})
    with pytest.raises(ContainmentError, match="containment.reach is empty"):
        check_containment(cfg)


def test_config_validation_also_blocks_driving_without_approval(harness_script, tmp_path):
    cfg = make_config(harness_script, tmp_path, containment={"approved": False})
    problems = cfg.validate()
    assert any("containment.approved is false" in p for p in problems)


def test_driving_at_l1_is_refused_by_validation(harness_script, tmp_path):
    cfg = make_config(harness_script, tmp_path, harness={"adapter_level": "L1"})
    problems = cfg.validate()
    assert any("Driving needs L2 or above" in p for p in problems)


def test_containment_answer_reaches_every_artifact(harness_script, tmp_path):
    cfg = make_config(harness_script, tmp_path)
    records, halt = drive(
        cfg, tasks(1), workspace=str(tmp_path), cassette=None,
        governor=Governor.from_config(cfg), seed=7,
    )
    rundir = execute(cfg, records, str(tmp_path / "out"))
    with open(rundir.path("run"), "r", encoding="utf-8") as fh:
        run = json.load(fh)
    assert run["config"]["containment"]["reach"] == "one empty temp directory, no network"
    assert run["config"]["containment"]["approved"] is True
    with open(rundir.path("summary"), "r", encoding="utf-8") as fh:
        assert "one empty temp directory, no network" in fh.read()


# ---------------------------------------------------------------------------
# The argv template
# ---------------------------------------------------------------------------


def test_render_argv_substitutes_every_placeholder():
    argv = render_argv(
        ["run", "--task", "{task_id}", "--out", "{out}", "--seed", "{seed}"],
        {"task_id": "a", "out": "/tmp/x", "seed": "7", "prompt": "p", "workspace": "/w"},
    )
    assert argv == ["run", "--task", "a", "--out", "/tmp/x", "--seed", "7"]


def test_unknown_placeholder_fails_loudly():
    with pytest.raises(DriveError, match="not a known placeholder"):
        render_argv(["run", "{outdir}"], {"task_id": "a", "out": "x", "seed": "1",
                                          "prompt": "", "workspace": ""})


def test_empty_invoke_fails_loudly():
    with pytest.raises(DriveError, match="nothing to run"):
        render_argv([], {})


def test_harness_that_writes_nothing_is_reported(tmp_path):
    import sys

    silent = tmp_path / "silent.py"
    silent.write_text("pass", encoding="utf-8")
    cfg = make_config(str(silent), tmp_path)
    cfg.data["harness"]["invoke"] = [sys.executable, str(silent), "{out}"]
    with pytest.raises(DriveError, match="exited without writing an intake file"):
        drive(cfg, tasks(1), workspace=str(tmp_path), cassette=None,
              governor=Governor.from_config(cfg), seed=7)


# ---------------------------------------------------------------------------
# The budget ceiling. It has to actually halt, at an exact point.
# ---------------------------------------------------------------------------


def test_governor_halts_at_the_exact_task():
    gov = Governor(enabled=True, max_usd=2.5, max_tokens=0)
    gov.charge(cost_usd=1.0, tokens=10, task_id="t1")
    gov.charge(cost_usd=1.0, tokens=10, task_id="t2")
    with pytest.raises(BudgetExceeded, match="2.500000"):
        gov.charge(cost_usd=1.0, tokens=10, task_id="t3")
    assert gov.halted_at == "t3"
    # the tripping charge is included, so the record does not understate spend
    assert gov.spent_usd == 3.0


def test_a_driven_run_stops_and_still_writes_artifacts(harness_script, tmp_path):
    """Exactly three of five tasks, not 'fewer than five'."""
    cfg = make_config(
        harness_script, tmp_path, task_cost="1.0",
        cost={"cassette": False, "budget": {"enabled": True, "max_usd": 2.5, "max_tokens": 0}},
    )
    gov = Governor.from_config(cfg)
    records, halt = drive(
        cfg, tasks(5), workspace=str(tmp_path), cassette=None, governor=gov, seed=7
    )

    assert len(records) == 3, "the ceiling must stop the run, not merely be noted"
    assert halt is not None
    assert halt["at_task"] == "t3"
    assert halt["tasks_completed"] == 3
    assert halt["tasks_planned"] == 5

    rundir = execute(cfg, records, str(tmp_path / "out"), extra_run_fields={"halt": halt})
    assert rundir.missing() == [], "a halted run still writes all eight artifacts"
    with open(rundir.path("run"), "r", encoding="utf-8") as fh:
        run = json.load(fh)
    assert run["halt"]["at_task"] == "t3"
    assert run["task_count"] == 3


def test_a_ceiling_that_cannot_fire_is_reported(harness_script, tmp_path):
    """A dollar ceiling on a harness that reports no cost is a broken promise."""
    cfg = make_config(
        harness_script, tmp_path,
        cost={"cassette": False, "budget": {"enabled": True, "max_usd": 5.0, "max_tokens": 0}},
    )
    gov = Governor.from_config(cfg)
    # charge only tokens, never a cost
    gov.charge(cost_usd=None, tokens=100, task_id="t1")
    blind = gov.blind_spots()
    assert any("cannot fire" in b for b in blind)
    assert any("Supply per-step cost" in b for b in blind)


def test_enabled_governor_with_no_ceilings_says_so():
    gov = Governor(enabled=True, max_usd=0, max_tokens=0)
    gov.charge(cost_usd=1.0, tokens=1, task_id="t")
    assert any("nothing is capped" in b for b in gov.blind_spots())


def test_disabled_governor_never_halts():
    gov = Governor(enabled=False, max_usd=0.001, max_tokens=1)
    for i in range(5):
        gov.charge(cost_usd=10.0, tokens=10_000, task_id=f"t{i}")
    assert gov.halted_at is None
    assert gov.blind_spots() == []


# ---------------------------------------------------------------------------
# Cassette replay. The property that makes the tool usable more than once.
# ---------------------------------------------------------------------------


def test_replay_reproduces_the_trace_byte_for_byte(harness_script, tmp_path):
    cassette_path = str(tmp_path / "rec.jsonl")
    cfg = make_config(harness_script, tmp_path, task_cost="0.002",
                      cost={"cassette": True, "budget": {"enabled": False}})

    first = Cassette(cassette_path)
    records_a, _ = drive(cfg, tasks(3), workspace=str(tmp_path), cassette=first,
                         governor=Governor.from_config(cfg), seed=7)
    assert first.hits == 0 and first.writes == 3
    run_a = execute(cfg, records_a, str(tmp_path / "a"))

    # Second pass: nothing is invoked, everything comes off the recording.
    second = Cassette(cassette_path)
    records_b, _ = drive(cfg, tasks(3), workspace=str(tmp_path), cassette=second,
                         governor=Governor.from_config(cfg), seed=7)
    assert second.hits == 3, "every task should have replayed"
    assert second.misses == 0
    assert all(r["source"] == "cassette" for r in records_b)
    run_b = execute(cfg, records_b, str(tmp_path / "b"))

    with open(run_a.path("trace"), "rb") as fh:
        trace_a = fh.read()
    with open(run_b.path("trace"), "rb") as fh:
        trace_b = fh.read()
    assert trace_a == trace_b, "a replayed run must reproduce the trace byte for byte"

    with open(run_a.path("metrics"), "r", encoding="utf-8") as fh:
        metrics_a = json.load(fh)
    with open(run_b.path("metrics"), "r", encoding="utf-8") as fh:
        metrics_b = json.load(fh)
    assert metrics_a["aggregate"] == metrics_b["aggregate"]
    assert metrics_a["per_task"] == metrics_b["per_task"]


def test_replay_costs_the_budget_nothing(harness_script, tmp_path):
    """A replayed task is free, so the ceiling must not be charged for it.

    Both arms are measured, because the claim is comparative. With no
    recording, a 2.5 ceiling stops a run of five 1.0-cost tasks at the third.
    With the first two already recorded, the same ceiling lets the run reach
    the fifth, having charged only the three tasks that actually executed.
    """
    cfg = make_config(harness_script, tmp_path, task_cost="1.0",
                      cost={"cassette": True, "budget": {"enabled": True, "max_usd": 2.5}})

    # Arm one: no recording at all.
    cold = Governor.from_config(cfg)
    cold_records, cold_halt = drive(cfg, tasks(5), workspace=str(tmp_path), cassette=None,
                                    governor=cold, seed=7)
    assert cold_halt["at_task"] == "t3"
    assert len(cold_records) == 3
    assert cold.spent_usd == 3.0

    # Record the first two tasks.
    rec = Cassette(str(tmp_path / "rec.jsonl"))
    warm_up, halt = drive(cfg, tasks(2), workspace=str(tmp_path), cassette=rec,
                          governor=Governor.from_config(cfg), seed=7)
    assert halt is None and len(warm_up) == 2

    # Arm two: same ceiling, same tasks, two of them now free.
    gov = Governor.from_config(cfg)
    replay = Cassette(str(tmp_path / "rec.jsonl"))
    records2, halt2 = drive(cfg, tasks(5), workspace=str(tmp_path), cassette=replay,
                            governor=gov, seed=7)
    assert replay.hits == 2, "t1 and t2 should have come off the recording"
    assert gov.tasks_charged == 3, "only the driven tasks may be charged"
    assert gov.spent_usd == 3.0
    assert halt2["at_task"] == "t5", "the ceiling now falls two tasks later"
    assert len(records2) == 5


def test_cassette_key_changes_when_the_model_changes(harness_script, tmp_path):
    a = Cassette.key("t1", 7, "adapter|model-a")
    b = Cassette.key("t1", 7, "adapter|model-b")
    assert a != b, "a recording made under a different model is a different experiment"
    assert Cassette.key("t1", 7, "x") == Cassette.key("t1", 7, "x")


def test_cassette_survives_a_round_trip(tmp_path):
    path = str(tmp_path / "c.jsonl")
    one = Cassette(path)
    one.put("k1", {"steps": [{"kind": "message"}]})
    one.put("k2", {"steps": []})
    one.save()

    two = Cassette(path)
    assert set(two.entries) == {"k1", "k2"}
    assert two.content_digest() == one.content_digest()


def test_corrupt_cassette_is_reported_not_ignored(tmp_path):
    path = tmp_path / "c.jsonl"
    path.write_text('{"key": "a", "payload": {}}\nnot json\n', encoding="utf-8")
    with pytest.raises(ValueError, match="corrupt cassette"):
        Cassette(str(path))
