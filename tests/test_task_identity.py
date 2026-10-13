"""One task id, one task.

A run holds one trace per task id, at ``tasks/<task_id>/trace.jsonl``. Two
records sharing an id therefore cannot both be kept: the second write destroys
the first, so the run's own per-task trace stops differing from the merged one
by nothing but ``x_task_id``, while the run-level metrics go on counting a task
whose trace is gone.

That is not a cosmetic inconsistency. It was measured before this: two intake
files named ``same`` produced a run claiming ``step_count`` 3 and 2 for two
records, a per-task trace file holding only the second record, a ``verify`` that
paired the run as one task, and a diagnosis that found nothing where the metric
said a three-call loop had happened.

``compare`` already refused duplicate ids, with the reason that duplicate
pairing would invent evidence. These tests pin the same refusal at the run that
would hold the duplicates, and at ``verify``, which pairs them.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import pytest

from harness_tuner.config import Config
from harness_tuner.protocol import normalize_trace, write_json
from harness_tuner.run.observer import collect
from harness_tuner.runner import TaskSetError, execute, load_task_set, main
from harness_tuner.verify import VerifyError, run_verify
from test_verify import build_run

CFG = Config(
    {
        "harness": {"id": "t", "adapter_level": "L1"},
        "mode": {"subject": "harness", "execution": "observe"},
        "models": {"harness": "m"},
        "report": {"min_tasks_for_fingerprint": 1, "alpha": 0.05},
    },
    source="<test>",
)

OBSERVE_TOML = """
[harness]
id = "t"
adapter_level = "L1"

[mode]
subject = "harness"
execution = "observe"

[cost]
cassette = false
"""


def toml_string(value: str) -> str:
    """A TOML basic string, escaped.

    Not decoration: a Windows path is full of backslashes, and a backslash
    inside a TOML basic string is an escape sequence, so a path pasted straight
    into the config is a parse error on that platform and only on that platform.
    ``json.dumps`` escaping is valid TOML for exactly the characters a path
    contains.
    """
    return json.dumps(value)


def drive_config(tmp_path) -> str:
    """A drive config whose invoke would leave a marker if it ever ran.

    The marker is what makes the refusal test mean something: the guard has to
    fire before the harness is invoked, and a file that only appears when the
    harness runs is how that is observed rather than assumed.
    """
    script = "import pathlib,sys; pathlib.Path(sys.argv[1]).write_text('ran')"
    return (
        "[harness]\n"
        'id = "t"\n'
        'adapter_level = "L2"\n'
        f"invoke = [{toml_string(sys.executable)}, \"-c\", {toml_string(script)}, "
        f"{toml_string(str(tmp_path / 'INVOKED'))}]\n"
        f"workspace = {toml_string(str(tmp_path))}\n"
        "\n[mode]\n"
        'subject = "harness"\n'
        'execution = "drive"\n'
        "\n[containment]\n"
        "approved = true\n"
        'reach = "a throwaway directory"\n'
        'mechanism = "temp dir"\n'
        "\n[cost]\n"
        "cassette = false\n"
    )


def intake(task_id: str, tool: str = "read_file") -> dict:
    """One adapter intake file, in the shape the contract specifies."""
    return {
        "task_id": task_id,
        "trace": [
            {"kind": "model_call", "result_kind": "ok", "duration_ms": 10},
            {"kind": "tool_call", "tool": tool, "args": {"p": "a"}, "result_kind": "ok"},
        ],
    }


def write_intake(directory, name: str, task_id: str, tool: str = "read_file") -> str:
    path = directory / name
    path.write_text(json.dumps(intake(task_id, tool=tool)), encoding="utf-8")
    return os.path.abspath(str(path))


def records_from(directory) -> list[dict]:
    return collect(str(directory))


# ---------------------------------------------------------------------------
# The driven task set: refused before the harness is invoked.
# ---------------------------------------------------------------------------


def test_a_task_set_naming_one_id_twice_is_refused(tmp_path):
    path = tmp_path / "tasks.json"
    path.write_text(
        json.dumps([{"task_id": "same", "prompt": "a"}, {"task_id": "same", "prompt": "b"}]),
        encoding="utf-8",
    )
    with pytest.raises(TaskSetError, match="unique within a run"):
        load_task_set(str(path))


def test_the_refusal_names_the_task_set_file_and_the_colliding_records(tmp_path):
    path = tmp_path / "tasks.json"
    path.write_text(
        json.dumps(
            [
                {"task_id": "ok", "prompt": "a"},
                {"task_id": "same", "prompt": "b"},
                {"task_id": "same", "prompt": "c"},
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(TaskSetError) as caught:
        load_task_set(str(path))
    message = str(caught.value)
    assert str(path) in message
    assert "'same' appears 2 times" in message
    assert "record 2 and record 3" in message


def test_a_distinct_task_set_still_loads(tmp_path):
    path = tmp_path / "tasks.json"
    path.write_text(
        json.dumps([{"task_id": "a", "prompt": "a"}, {"task_id": "b", "prompt": "b"}]),
        encoding="utf-8",
    )
    assert [t["task_id"] for t in load_task_set(str(path))] == ["a", "b"]


def test_the_run_command_refuses_a_duplicate_task_set_and_spends_nothing(tmp_path, capsys):
    """The guard has to fire before the harness is driven, not after.

    Every task in a driven run costs money. A collision discovered while the
    artifacts were being written would have been discovered after the whole task
    set had already been paid for.
    """
    config = tmp_path / "harness-tuner.toml"
    config.write_text(drive_config(tmp_path), encoding="utf-8")
    task_set = tmp_path / "tasks.json"
    task_set.write_text(
        json.dumps([{"task_id": "same", "prompt": "a"}, {"task_id": "same", "prompt": "b"}]),
        encoding="utf-8",
    )
    out = tmp_path / "results"

    code = main(
        argparse.Namespace(
            source=str(task_set),
            config=str(config),
            out=str(out),
            capabilities=None,
            no_cassette=True,
        )
    )

    assert code == 2
    assert "unique within a run" in capsys.readouterr().err
    assert not (tmp_path / "INVOKED").exists(), "the harness was invoked for a refused task set"
    assert not out.exists(), "a refused task set still produced output"


# ---------------------------------------------------------------------------
# The intake directory: refused before a single artifact is written.
# ---------------------------------------------------------------------------


def test_two_intake_files_sharing_a_task_id_are_refused(tmp_path):
    intake_dir = tmp_path / "intake"
    intake_dir.mkdir()
    first = write_intake(intake_dir, "a.json", "same")
    second = write_intake(intake_dir, "b.json", "same")

    with pytest.raises(TaskSetError, match="unique within a run") as caught:
        execute(CFG, records_from(intake_dir), str(tmp_path / "results"))

    message = str(caught.value)
    assert first in message and second in message, "the refusal has to name both files"
    assert not (tmp_path / "results").exists()


def test_a_refused_run_writes_no_partial_run(tmp_path):
    """Nothing on disk, because a half-written run reads as a real one."""
    intake_dir = tmp_path / "intake"
    intake_dir.mkdir()
    write_intake(intake_dir, "a.json", "same")
    write_intake(intake_dir, "b.json", "same")
    out = tmp_path / "results"

    with pytest.raises(TaskSetError):
        execute(CFG, records_from(intake_dir), str(out))

    assert not out.exists()


def test_the_run_command_reports_a_duplicate_intake_without_a_traceback(tmp_path, capsys):
    intake_dir = tmp_path / "intake"
    intake_dir.mkdir()
    write_intake(intake_dir, "a.json", "same")
    write_intake(intake_dir, "b.json", "same")
    config = tmp_path / "harness-tuner.toml"
    config.write_text(OBSERVE_TOML, encoding="utf-8")

    code = main(
        argparse.Namespace(
            source=str(intake_dir),
            config=str(config),
            out=str(tmp_path / "results"),
            capabilities=None,
            no_cassette=True,
        )
    )

    assert code == 2
    assert "run failed" in capsys.readouterr().err
    assert not (tmp_path / "results").exists()


def test_distinct_task_ids_are_left_alone(tmp_path):
    """The guard must not touch a run that was fine before it existed."""
    intake_dir = tmp_path / "intake"
    intake_dir.mkdir()
    write_intake(intake_dir, "a.json", "one", tool="read_file")
    write_intake(intake_dir, "b.json", "two", tool="write_file")

    rundir = execute(CFG, records_from(intake_dir), str(tmp_path / "results"))

    with open(rundir.path("metrics"), "r", encoding="utf-8") as handle:
        per_task = json.load(handle)["per_task"]
    assert [entry["task_id"] for entry in per_task] == ["one", "two"]

    # And the invariant the collision used to break: each task's own trace is
    # exactly its records from the merged file, less the attribution field.
    merged: dict[str, list] = {}
    with open(rundir.path("trace"), "r", encoding="utf-8") as handle:
        for line in handle:
            record = json.loads(line)
            merged.setdefault(record.pop("x_task_id"), []).append(record)
    for task_id in ("one", "two"):
        with open(rundir.task_path(task_id, "trace"), "r", encoding="utf-8") as handle:
            own = [json.loads(line) for line in handle]
        assert own == merged[task_id]


# ---------------------------------------------------------------------------
# verify: the command that pairs tasks by id.
# ---------------------------------------------------------------------------


def test_verify_refuses_a_run_that_lists_one_id_twice(tmp_path):
    baseline = build_run(tmp_path, "base", [("a", ["x"], 0.01)])
    variant = build_run(tmp_path, "var", [("a", ["x"], 0.01)])
    with open(variant.path("metrics"), "r", encoding="utf-8") as handle:
        metrics = json.load(handle)
    metrics["per_task"].append(metrics["per_task"][0])
    write_json(variant.path("metrics"), metrics)

    with pytest.raises(VerifyError, match="more than once"):
        run_verify(baseline.root, variant.root)


def test_verify_still_pairs_a_run_whose_ids_are_distinct(tmp_path):
    baseline = build_run(tmp_path, "base", [(f"t{i}", ["a", "a"], 0.01) for i in range(6)])
    variant = build_run(tmp_path, "var", [(f"t{i}", ["a", "b"], 0.01) for i in range(6)])

    doc = run_verify(baseline.root, variant.root)

    assert doc["tasks_paired"] == 6


def test_normalized_steps_are_unaffected_by_the_guard():
    """The guard is about task identity, not about trace shape."""
    assert normalize_trace(intake("x")["trace"])[1]["tool"] == "read_file"
