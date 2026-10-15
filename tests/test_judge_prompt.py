"""The delegated judge must be shown the prompt.

``task_success`` comes from a deterministic check or a judge verdict, and the
judge is asked whether the harness accomplished *what the prompt asked*. The
prompt is therefore the one field the judge cannot work without, and it is also
the one field no trace records: a trace says what the harness did, never what it
was told to do.

Reproduced before this was fixed, ``judge-request.json`` carried
``"prompt": null`` for every task, in both modes, for two different reasons:

* drive mode had the prompt in hand, having just substituted it into the
  harness argv, and dropped it when it built the record's task object;
* observe mode read the adapter's file and ignored a top-level ``prompt``,
  keeping only the ``task`` block.

So the engine asked the judge to score an outcome against a prompt it had never
been shown, and the judge could only answer ``null`` (leaving ``task_success``
unavailable) or guess from the trace. These tests pin the prompt through to the
request in both modes, because the request is the whole delegated contract.
"""

from __future__ import annotations

import json
import os
import sys
import textwrap

import pytest

from harness_tuner.config import Config
from harness_tuner.run.budget import Governor
from harness_tuner.run.driver import drive
from harness_tuner.run.observer import IntakeError, collect
from harness_tuner.runner import execute

PROMPT = "Read INPUT.txt and report its first line verbatim."

#: A harness that writes a trace and nothing else, so no task has a
#: deterministic outcome and every task needs a judge verdict.
SILENT_OUTCOME_HARNESS = textwrap.dedent(
    '''
    import json, sys

    task_id, out_path, seed = sys.argv[1], sys.argv[2], sys.argv[3]
    trace = [
        {"kind": "tool_call", "tool": "read_file", "args": {"path": "INPUT.txt"},
         "result_kind": "ok", "started_ms": 0, "duration_ms": 10},
    ]
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump({"task_id": task_id, "trace": trace}, fh)
    '''
).strip()


def drive_config(tmp_path, script: str) -> Config:
    return Config(
        {
            "harness": {
                "id": "fake",
                "adapter": "fake-adapter",
                "adapter_level": "L2",
                "invoke": [sys.executable, str(script), "{task_id}", "{out}", "{seed}"],
                "timeout_s": 60,
                "workspace": str(tmp_path),
            },
            "mode": {"subject": "harness", "execution": "drive"},
            "models": {"harness": "fake-model", "judge": "judge-model"},
            "containment": {
                "mechanism": "throwaway directory under the system temp path",
                "declared_by": "agent",
                "reach": "one empty temp directory, no network",
                "approved": True,
            },
            "cost": {"cassette": False, "budget": {"enabled": False}},
            "tasks": {"seed": 7},
            "report": {"min_tasks_for_fingerprint": 1},
        },
        source="<test>",
    )


def observe_config() -> Config:
    return Config(
        {
            "harness": {"id": "t", "adapter_level": "L1"},
            "mode": {"subject": "harness", "execution": "observe"},
            "models": {"harness": "m", "judge": "judge-model"},
            "report": {"min_tasks_for_fingerprint": 1, "alpha": 0.05},
        },
        source="<test>",
    )


def request_tasks(rundir) -> list[dict]:
    path = os.path.join(rundir.root, "judge-request.json")
    assert os.path.isfile(path), "a task with no outcome must produce a judge request"
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)["tasks"]


def prompts(rundir) -> dict[str, object]:
    return {entry["task_id"]: entry["prompt"] for entry in request_tasks(rundir)}


def write_intake(directory, task_id: str, **top_level):
    payload = {
        "task_id": task_id,
        "trace": [
            {"kind": "tool_call", "tool": "read_file", "args": {"path": "a.txt"},
             "result_kind": "ok", "started_ms": 0, "duration_ms": 10}
        ],
    }
    payload.update(top_level)
    path = os.path.join(str(directory), f"{task_id}.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
    return path


# ---------------------------------------------------------------------------
# Drive mode: the engine holds the prompt and must not discard it.
# ---------------------------------------------------------------------------


def test_drive_mode_shows_the_judge_the_prompt_it_was_given(tmp_path):
    script = tmp_path / "harness.py"
    script.write_text(SILENT_OUTCOME_HARNESS, encoding="utf-8")
    cfg = drive_config(tmp_path, script)

    tasks = [
        {"task_id": "t1", "prompt": PROMPT},
        {"task_id": "t2", "prompt": "Count the lines in NOTES.txt."},
    ]
    records, halt = drive(
        cfg, tasks, workspace=str(tmp_path), cassette=None,
        governor=Governor.from_config(cfg), seed=7,
    )
    assert halt is None
    rundir = execute(cfg, records, str(tmp_path / "out"))

    assert prompts(rundir) == {"t1": PROMPT, "t2": "Count the lines in NOTES.txt."}


def test_drive_mode_without_a_prompt_still_writes_a_request(tmp_path):
    """A task set may omit the prompt; the request is still written, with null."""
    script = tmp_path / "harness.py"
    script.write_text(SILENT_OUTCOME_HARNESS, encoding="utf-8")
    cfg = drive_config(tmp_path, script)

    records, _halt = drive(
        cfg, [{"task_id": "t1"}], workspace=str(tmp_path), cassette=None,
        governor=Governor.from_config(cfg), seed=7,
    )
    rundir = execute(cfg, records, str(tmp_path / "out"))

    assert prompts(rundir) == {"t1": None}


# ---------------------------------------------------------------------------
# Observe mode: a prompt the adapter named must not be silently dropped.
# ---------------------------------------------------------------------------


def test_observe_mode_keeps_a_top_level_prompt(tmp_path):
    intake = tmp_path / "intake"
    intake.mkdir()
    write_intake(intake, "t1", prompt=PROMPT)

    rundir = execute(observe_config(), collect(str(intake)), str(tmp_path / "out"))
    assert prompts(rundir) == {"t1": PROMPT}


def test_an_explicit_task_prompt_wins_over_the_top_level_one(tmp_path):
    """The nested field is the more specific statement, so it is the one used."""
    intake = tmp_path / "intake"
    intake.mkdir()
    write_intake(intake, "t1", prompt="the top-level prompt",
                 task={"prompt": "the nested prompt", "optimal_steps": 1})

    rundir = execute(observe_config(), collect(str(intake)), str(tmp_path / "out"))
    assert prompts(rundir) == {"t1": "the nested prompt"}


def test_observe_mode_without_a_prompt_leaves_it_null(tmp_path):
    intake = tmp_path / "intake"
    intake.mkdir()
    write_intake(intake, "t1", task={"optimal_steps": 1})

    rundir = execute(observe_config(), collect(str(intake)), str(tmp_path / "out"))
    assert prompts(rundir) == {"t1": None}


def test_collect_carries_the_prompt_into_the_record(tmp_path):
    """The request reads the record's task object, so that is where it must be."""
    intake = tmp_path / "intake"
    intake.mkdir()
    write_intake(intake, "t1", prompt=PROMPT)

    records = collect(str(intake))
    assert records[0]["task"]["prompt"] == PROMPT


# ---------------------------------------------------------------------------
# The record's task object is an object.
# ---------------------------------------------------------------------------


def test_a_non_object_task_block_is_refused(tmp_path):
    """A JSON list has no declared fields to read, and used to crash later."""
    intake = tmp_path / "intake"
    intake.mkdir()
    path = os.path.join(str(intake), "t1.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"task_id": "t1", "task": ["prompt"], "trace": []}, handle)

    with pytest.raises(IntakeError, match="'task' must be an object"):
        collect(str(intake))


def test_the_fixtures_still_parse_and_carry_no_invented_prompt():
    """The shipped conformance fixtures declare no prompt, so none appears."""
    from harness_tuner.conformance import suite_root

    records = collect(os.path.join(suite_root(), "fixtures"))
    assert records, "no fixtures were read"
    assert all(r["task"].get("prompt") is None for r in records)
