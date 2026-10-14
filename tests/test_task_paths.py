"""A task id names a directory inside the run, so it is checked as one.

The per-task trace lives at ``tasks/<task_id>/trace.jsonl`` inside the run
directory, which makes the id a destination rather than a label. The id is
input: an adapter reading a harness produces it, and a task set written by a
model produces it. Reproduced before this check, an intake file naming its task
``../../../escaped`` had its per-task trace written outside the run directory
the report then described -- the run exited zero and every artifact looked
normal, so nothing downstream could notice.

These tests pin the two halves that matter. The id is refused before it is used
as a path, and a legitimate namespaced id still works, because the generated
task set names its tasks ``generated/<id>`` and a rule that refused every
separator would break the documented naming instead of the escape.
"""

from __future__ import annotations

import json
import os

import pytest

from harness_tuner.config import Config
from harness_tuner.conformance import load_fixtures, suite_root
from harness_tuner.protocol import normalize_trace
from harness_tuner.protocol.artifacts import (
    RunDir,
    TaskIdError,
    check_task_id,
    check_task_ids,
)
from harness_tuner.run.budget import Governor
from harness_tuner.run.driver import DriveError, drive
from harness_tuner.run.observer import IntakeError, collect
from harness_tuner.runner import execute

CFG = Config(
    {
        "harness": {"id": "t", "adapter_level": "L1"},
        "mode": {"subject": "harness", "execution": "observe"},
        "models": {"harness": "m"},
        "report": {"min_tasks_for_fingerprint": 1, "alpha": 0.05},
    },
    source="<test>",
)

#: Ids that would send the per-task trace somewhere other than this run.
ESCAPING = (
    "../escaped",
    "../../escaped",
    "../../../escaped",
    "a/../../escaped",
    "..",
    ".",
    "",
    "/etc/passwd",
    "a/b/../../../escaped",
)

#: Ids that are fine. The namespaced one is not a nicety: the generated task set
#: names its tasks exactly this way, so refusing every separator would refuse
#: the documented task set instead of the escape.
LEGAL = (
    "plain",
    "l0_minimal",
    "generated/locate-the-caller",
    "a/b/c",
    "with spaces and <angles>",
)


def one_step_trace():
    return normalize_trace([{"kind": "tool_call", "tool": "read_file", "args": {"path": "a"}}])


def record(task_id: str) -> dict:
    return {
        "task_id": task_id,
        "tags": [],
        "task": {},
        "steps": one_step_trace(),
        "source": "test",
    }


# ---------------------------------------------------------------------------
# The rule itself
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("task_id", LEGAL)
def test_a_legal_id_is_returned_unchanged(task_id):
    assert check_task_id(task_id) == task_id


@pytest.mark.parametrize("task_id", ESCAPING)
def test_an_escaping_id_is_refused(task_id):
    with pytest.raises(TaskIdError):
        check_task_id(task_id)


@pytest.mark.parametrize("task_id", ESCAPING)
def test_the_refusal_says_what_the_id_is_used_for(task_id):
    """The reason has to be actionable, because the id came from a file."""
    with pytest.raises(TaskIdError) as caught:
        check_task_id(task_id)
    message = str(caught.value)
    assert "tasks/<task_id>/trace.jsonl" in message
    assert "tasks/" in message


@pytest.mark.parametrize("task_id", [None, 5, b"bytes", ["a"]])
def test_an_id_that_is_not_a_non_empty_string_is_refused(task_id):
    with pytest.raises(TaskIdError):
        check_task_id(task_id)


def test_a_whole_set_is_checked_and_every_offender_is_named():
    with pytest.raises(TaskIdError) as caught:
        check_task_ids(["fine", "../one", "also/fine", "../two"])
    message = str(caught.value)
    assert "2 task id(s)" in message
    assert "'../one'" in message and "'../two'" in message


def test_a_clean_set_passes():
    check_task_ids(["a", "generated/b", "c/d/e"])


def test_the_shipped_fixture_ids_are_all_legal():
    """A rule that refused the shipped conformance fixtures would be wrong."""
    for fixture in load_fixtures(suite_root()):
        assert check_task_id(fixture["name"]) == fixture["name"]


# ---------------------------------------------------------------------------
# The run directory refuses to be written outside itself
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("task_id", ESCAPING)
def test_the_run_directory_refuses_an_escaping_id(tmp_path, task_id):
    rundir = RunDir(str(tmp_path / "run"))
    rundir.ensure()
    with pytest.raises(TaskIdError):
        rundir.task_dir(task_id)
    with pytest.raises(TaskIdError):
        rundir.task_path(task_id, "trace")


def test_a_legal_namespaced_id_still_lands_under_tasks(tmp_path):
    """The control: the generated task set's own naming must keep working."""
    rundir = RunDir(str(tmp_path / "run"))
    # normpath on both sides: the id keeps whatever separator it was written
    # with, and the two are the same directory on Windows as on POSIX.
    assert os.path.normpath(rundir.task_dir("generated/x")) == os.path.normpath(
        os.path.join(rundir.root, "tasks", "generated", "x")
    )


def test_execute_refuses_a_record_that_would_escape_the_run(tmp_path):
    """The reproduction, end to end, with nothing written outside the run.

    ``../../../escaped`` resolves from ``<out>/<run_id>/tasks`` to the parent of
    the output root, so this is the case where the per-task trace used to leave
    the run behind entirely.
    """
    out = tmp_path / "out"
    with pytest.raises(TaskIdError):
        execute(CFG, [record("../../../escaped")], str(out))
    # Nothing named by the escaping id exists anywhere the run could have
    # reached: neither beside the output root nor above it.
    assert not (tmp_path / "escaped").exists()
    assert not (out / "escaped").exists()
    assert [d for d in os.listdir(tmp_path) if d != "out"] == []


def test_execute_still_writes_a_legal_run(tmp_path):
    """The guard must not cost a normal run its artifacts."""
    rundir = execute(CFG, [record("generated/ok")], str(tmp_path / "out"))
    assert rundir.missing() == []
    assert os.path.isfile(rundir.task_path("generated/ok", "trace"))


# ---------------------------------------------------------------------------
# The two boundaries the id crosses before it becomes a path
# ---------------------------------------------------------------------------


def test_observe_mode_refuses_the_intake_file_that_named_it(tmp_path):
    """The refusal names the file, because that is the thing to go and fix."""
    intake = tmp_path / "evil.json"
    intake.write_text(
        json.dumps({"task_id": "../escaped", "trace": [{"kind": "message"}]}), encoding="utf-8"
    )
    with pytest.raises(IntakeError) as caught:
        collect(str(intake))
    message = str(caught.value)
    assert str(intake) in message
    assert "tasks/<task_id>/trace.jsonl" in message
    assert not (tmp_path / "escaped").exists()


def test_a_harness_named_task_id_is_refused_too(tmp_path):
    """The fallback id is a file name, and a file name cannot escape."""
    intake = tmp_path / "by-name.json"
    intake.write_text(
        json.dumps({"name": "../../escaped", "trace": [{"kind": "message"}]}), encoding="utf-8"
    )
    with pytest.raises(IntakeError, match="would not stay inside"):
        collect(str(intake))


def test_drive_mode_refuses_the_task_set_before_invoking_anything(tmp_path):
    """Refused before the harness runs, so nothing is driven and paid for."""
    cfg = Config(
        {
            "harness": {
                "id": "fake",
                "adapter_level": "L2",
                "invoke": ["definitely-not-a-real-binary-xyz", "{task_id}"],
                "timeout_s": 5,
                "workspace": str(tmp_path),
            },
            "mode": {"subject": "harness", "execution": "drive"},
            "models": {"harness": "m"},
            "containment": {
                "mechanism": "none",
                "declared_by": "agent",
                "reach": "nothing",
                "approved": True,
            },
            "cost": {"cassette": False, "budget": {"enabled": False}},
        },
        source="<test>",
    )
    with pytest.raises(DriveError) as caught:
        drive(
            cfg,
            [{"task_id": "ok"}, {"task_id": "../escaped"}],
            workspace=str(tmp_path),
            cassette=None,
            governor=Governor.from_config(cfg),
            seed=0,
        )
    message = str(caught.value)
    assert "would not stay inside" in message
    assert "'../escaped'" in message
    # It never reached the invocation, which is the whole point of refusing here.
    assert "cannot execute" not in message


def test_drive_mode_still_drives_a_legal_namespaced_id(tmp_path):
    """The control for the test above: a legal set is not refused."""
    script = tmp_path / "harness.py"
    script.write_text(
        "import json,sys\n"
        "task_id, out = sys.argv[1], sys.argv[2]\n"
        "json.dump({'task_id': task_id, 'task': {'success': True},"
        " 'trace': [{'kind': 'message'}]}, open(out, 'w'))\n",
        encoding="utf-8",
    )
    import sys

    cfg = Config(
        {
            "harness": {
                "id": "fake",
                "adapter_level": "L2",
                "invoke": [sys.executable, str(script), "{task_id}", "{out}"],
                "timeout_s": 60,
                "workspace": str(tmp_path),
            },
            "mode": {"subject": "harness", "execution": "drive"},
            "models": {"harness": "m"},
            "containment": {
                "mechanism": "none",
                "declared_by": "agent",
                "reach": "nothing",
                "approved": True,
            },
            "cost": {"cassette": False, "budget": {"enabled": False}},
            "report": {"min_tasks_for_fingerprint": 1},
        },
        source="<test>",
    )
    records, halt = drive(
        cfg,
        [{"task_id": "generated/ok"}],
        workspace=str(tmp_path),
        cassette=None,
        governor=Governor.from_config(cfg),
        seed=0,
    )
    assert halt is None
    assert [r["task_id"] for r in records] == ["generated/ok"]
    rundir = execute(cfg, records, str(tmp_path / "out"))
    assert os.path.isfile(rundir.task_path("generated/ok", "trace"))
