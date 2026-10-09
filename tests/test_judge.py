"""The delegated judge: the engine asks, the agent answers, the CLI folds it in.

The whole reason the delegated path exists is that the engine holds no
credentials: it writes ``judge-request.json``, the user's agent answers it, and
the verdicts come back as a file. That promise is only kept if the one
user-facing entry point can actually read the answer back, so these tests drive
``runner.main`` end to end rather than calling ``apply_response`` directly.

Two failures are pinned here. Before ``--judge-response`` existed, the engine
wrote the request and had no way to fold the answer in, so ``task_success``
reported unavailable for every task a pack could not settle deterministically,
while the guide told the operator the engine reads the answer back. And a
mistyped path was indistinguishable from "no response supplied", which would
run the whole pipeline and quietly leave the verdicts out.
"""

from __future__ import annotations

import argparse
import json
import os

from harness_tuner.runner import main


def _intake(directory, task_id, *, success=None):
    """One observe-mode intake file. ``success`` omitted means unjudged."""
    task = {"optimal_steps": 1}
    if success is not None:
        task["success"] = success
    payload = {
        "task_id": task_id,
        "tags": [],
        "task": task,
        "trace": [
            {"kind": "tool_call", "tool": "read_file", "args": {"path": "a.py"},
             "result_kind": "ok", "started_ms": 0, "duration_ms": 10}
        ],
    }
    path = directory / f"{task_id}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _args(source, out, judge_response=None):
    return argparse.Namespace(
        source=str(source),
        config=None,
        out=str(out),
        capabilities=None,
        no_cassette=False,
        judge_response=judge_response,
    )


def _metrics(out):
    run_root = os.path.join(str(out), os.listdir(str(out))[0])
    with open(os.path.join(run_root, "metrics.json"), "r", encoding="utf-8") as handle:
        return json.load(handle), run_root


def test_an_unanswered_task_stays_unavailable_and_asks_for_a_verdict(tmp_path, capsys):
    """The request is written and the run says so; it never assumes a pass."""
    intake = tmp_path / "intake"
    intake.mkdir()
    _intake(intake, "t1")

    assert main(_args(intake, tmp_path / "results")) == 0
    metrics, run_root = _metrics(tmp_path / "results")

    entry = metrics["aggregate"]["task_success"]
    assert entry["status"] == "unavailable"
    assert entry["value"] is None
    assert os.path.isfile(os.path.join(run_root, "judge-request.json"))

    printed = capsys.readouterr().out
    assert "--judge-response" in printed
    assert "judge-request.json" in printed


def test_a_judge_response_is_folded_into_task_success(tmp_path):
    """The documented delegated path, driven the way the guide describes it."""
    intake = tmp_path / "intake"
    intake.mkdir()
    _intake(intake, "t1")
    _intake(intake, "t2")

    response = tmp_path / "judge-response.json"
    response.write_text(
        json.dumps(
            {
                "verdicts": [
                    {"task_id": "t1", "success": True, "why": "did the job"},
                    {"task_id": "t2", "success": False, "why": "stopped early"},
                ]
            }
        ),
        encoding="utf-8",
    )

    assert main(_args(intake, tmp_path / "results", judge_response=response)) == 0
    metrics, run_root = _metrics(tmp_path / "results")

    entry = metrics["aggregate"]["task_success"]
    assert entry["status"] == "measured"
    assert entry["value"] == 0.5
    by_task = {t["task_id"]: t["metrics"]["task_success"]["value"] for t in metrics["per_task"]}
    assert by_task == {"t1": True, "t2": False}

    # Every task was answered, so there is nothing left to request.
    assert not os.path.isfile(os.path.join(run_root, "judge-request.json"))
    with open(os.path.join(run_root, "run.json"), "r", encoding="utf-8") as handle:
        judging = json.load(handle)["judging"]
    assert judging["applied"] == 2
    assert judging["pending"] == 0


def test_an_unanswered_task_is_left_unavailable_not_assumed(tmp_path):
    """A partial answer judges what it covers and leaves the rest alone."""
    intake = tmp_path / "intake"
    intake.mkdir()
    _intake(intake, "t1")
    _intake(intake, "t2")

    response = tmp_path / "judge-response.json"
    response.write_text(
        json.dumps({"verdicts": [{"task_id": "t1", "success": True, "why": "done"}]}),
        encoding="utf-8",
    )

    assert main(_args(intake, tmp_path / "results", judge_response=response)) == 0
    metrics, run_root = _metrics(tmp_path / "results")

    by_task = {t["task_id"]: t["metrics"]["task_success"] for t in metrics["per_task"]}
    assert by_task["t1"]["value"] is True
    assert by_task["t2"]["status"] == "unavailable"
    # The rate is over the one task that was actually judged.
    assert metrics["aggregate"]["task_success"]["value"] == 1.0
    assert os.path.isfile(os.path.join(run_root, "judge-request.json"))


def test_a_deterministic_outcome_needs_no_judging(tmp_path, capsys):
    """A task the pack settled is not asked about, so nothing is requested."""
    intake = tmp_path / "intake"
    intake.mkdir()
    _intake(intake, "t1", success=True)

    assert main(_args(intake, tmp_path / "results")) == 0
    metrics, run_root = _metrics(tmp_path / "results")

    assert metrics["aggregate"]["task_success"]["value"] == 1.0
    assert not os.path.isfile(os.path.join(run_root, "judge-request.json"))
    assert "--judge-response" not in capsys.readouterr().out


def test_a_missing_judge_response_file_is_refused_before_anything_runs(tmp_path, capsys):
    """An explicit path that is not there is a mistake, not 'no response'."""
    intake = tmp_path / "intake"
    intake.mkdir()
    _intake(intake, "t1")
    out = tmp_path / "results"

    assert main(_args(intake, out, judge_response=tmp_path / "nope.json")) == 2
    assert "not a file" in capsys.readouterr().err
    assert not os.path.exists(out), "a refused run must not leave a half-written directory"
