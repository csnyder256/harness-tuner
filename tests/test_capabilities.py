"""A named input file that does not exist is not the same as none named.

Four commands take an optional path to a file the operator's own agent wrote:
the capability record (``run``, ``tasks``, ``doctor``) and the diagnostician's
answer (``diagnose``). Every one of them read a mistyped path as "nothing was
supplied", which is the opposite of what was meant.

The capability case is the worst of the four, because the run's own
``capabilities.json`` then records "no capabilities file was supplied" as the
reason no benchmark was gated. That sentence is false. A file was named, the
flag was ignored in silence, and every task the scan would have skipped was run
and scored as though the harness had failed at work it was never asked whether
it could do.

These tests pin the boundary in both directions. A path that was named has to
exist and has to parse. Naming no path stays exactly what it was: an explicit
"not scanned", with nothing skipped and nothing failed on capability grounds.
"""

from __future__ import annotations

import json
import os

import pytest

from harness_tuner import capabilities as C
from harness_tuner import diagnose, doctor, runner, tasks
from harness_tuner.config import Config
from harness_tuner.protocol import normalize_trace
from harness_tuner.runner import execute

# ---------------------------------------------------------------------------
# The resolver, on its own.
# ---------------------------------------------------------------------------


def test_naming_no_path_is_not_an_error(tmp_path):
    """Omitting the flag is a legitimate request for no capability gate."""
    assert C.resolve(None) is None
    assert C.resolve("") is None


def test_a_named_path_that_is_not_a_file_is_refused(tmp_path):
    with pytest.raises(C.CapabilityError, match="is not a file"):
        C.resolve(str(tmp_path / "typo.json"))


def test_a_named_path_that_is_a_directory_is_refused(tmp_path):
    """A directory is not a record either, and isfile says so the same way."""
    with pytest.raises(C.CapabilityError, match="is not a file"):
        C.resolve(str(tmp_path))


def test_no_record_named_is_an_explicit_not_scanned():
    record = C.load(None)
    assert record["status"] == "not_scanned"
    assert record["capabilities"] == {}
    assert record["source"] is None
    assert "no capabilities file was supplied" in record["reason"]


def test_a_record_that_does_not_parse_is_reported_not_crashed(tmp_path):
    """A traceback is not a diagnosis. Before this, json.load raised uncaught."""
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    with pytest.raises(C.CapabilityError, match="not valid JSON"):
        C.load(str(broken))


def test_a_record_that_is_not_an_object_is_reported(tmp_path):
    """A JSON list has no 'status' and no 'capabilities' block to read."""
    wrong = tmp_path / "wrong.json"
    wrong.write_text(json.dumps([{"filesystem": True}]), encoding="utf-8")
    with pytest.raises(C.CapabilityError, match="JSON list"):
        C.load(str(wrong))


def test_a_named_record_is_returned_with_its_source(tmp_path):
    path = tmp_path / "capabilities.json"
    path.write_text(
        json.dumps({"status": "scanned", "capabilities": {"filesystem": {"present": True}}}),
        encoding="utf-8",
    )
    record = C.load(str(path))
    assert record["status"] == "scanned"
    assert record["source"] == os.path.abspath(str(path))
    assert record["capabilities"]["filesystem"]["present"] is True


# ---------------------------------------------------------------------------
# run: the artifact that used to state something false.
# ---------------------------------------------------------------------------


def _intake(tmp_path):
    directory = tmp_path / "intake"
    directory.mkdir()
    (directory / "one.json").write_text(
        json.dumps(
            {
                "task_id": "one",
                "trace": [
                    {
                        "kind": "tool_call",
                        "tool": "read_file",
                        "args": {"path": "a"},
                        "result_kind": "ok",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return directory


def _cli(argv: list[str]):
    """The Namespace the real command line builds for this argv.

    Parsed through the actual parser rather than assembled by hand, because a
    hand-written Namespace goes stale the moment any command grows an argument.
    """
    from harness_tuner.__main__ import build_parser

    return build_parser().parse_args(argv)


def _run_argv(source, out, capabilities=None):
    argv = ["run", str(source), "--out", str(out)]
    if capabilities:
        argv += ["--capabilities", str(capabilities)]
    return argv


def _only_run(out):
    entries = [name for name in os.listdir(out) if os.path.isdir(os.path.join(out, name))]
    assert len(entries) == 1, entries
    return os.path.join(out, entries[0])


def test_run_refuses_a_named_record_that_does_not_exist_before_writing_anything(tmp_path, capsys):
    """Nothing is spent on a run whose named inputs cannot be read."""
    out = tmp_path / "results"
    code = runner.main(
        _cli(_run_argv(_intake(tmp_path), out, tmp_path / "typo.json"))
    )

    assert code == 2
    err = capsys.readouterr().err
    assert "is not a file" in err
    assert "--capabilities" in err
    assert not out.exists(), "a refused run must not leave a half-written results directory"


def test_run_refuses_a_named_record_that_does_not_parse(tmp_path, capsys):
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    code = runner.main(_cli(_run_argv(_intake(tmp_path), tmp_path / "results", broken)))
    assert code == 2
    assert "not valid JSON" in capsys.readouterr().err


def test_run_records_a_named_record_and_never_claims_none_was_supplied(tmp_path):
    """The artifact has to describe the run it is in, including its gate."""
    path = tmp_path / "capabilities.json"
    path.write_text(
        json.dumps(
            {
                "status": "scanned",
                "scanned_by": "the agent that set this up",
                "capabilities": {"filesystem": {"present": True}},
            }
        ),
        encoding="utf-8",
    )
    out = tmp_path / "results"
    assert runner.main(_cli(_run_argv(_intake(tmp_path), out, path))) == 0

    with open(os.path.join(_only_run(out), "capabilities.json"), "r", encoding="utf-8") as handle:
        recorded = json.load(handle)
    assert recorded["status"] == "scanned"
    assert recorded["source"] == os.path.abspath(str(path))
    assert recorded["capabilities"]["filesystem"]["present"] is True
    assert "no capabilities file was supplied" not in json.dumps(recorded)


def test_run_with_no_record_named_still_writes_not_scanned(tmp_path):
    """The control. Absence is still absence, and it still says so."""
    out = tmp_path / "results"
    assert runner.main(_cli(_run_argv(_intake(tmp_path), out))) == 0

    with open(os.path.join(_only_run(out), "capabilities.json"), "r", encoding="utf-8") as handle:
        recorded = json.load(handle)
    assert recorded["status"] == "not_scanned"
    assert recorded["source"] is None
    assert "no capabilities file was supplied" in recorded["reason"]


def test_execute_still_writes_not_scanned_when_no_path_is_given(tmp_path):
    """The pipeline keeps its contract for callers that pass no path at all."""
    cfg = Config(
        {
            "harness": {"id": "t", "adapter_level": "L1"},
            "mode": {"subject": "harness", "execution": "observe"},
            "models": {"harness": "m"},
            "report": {"min_tasks_for_fingerprint": 1},
        },
        source="<test>",
    )
    records = [
        {
            "task_id": "t1",
            "tags": [],
            "task": {},
            "steps": normalize_trace([{"kind": "message"}]),
            "source": "test",
        }
    ]
    rundir = execute(cfg, records, str(tmp_path / "out"))
    with open(rundir.path("capabilities"), "r", encoding="utf-8") as handle:
        recorded = json.load(handle)
    assert recorded["status"] == "not_scanned"


# ---------------------------------------------------------------------------
# tasks: the command that actually applies the gate.
# ---------------------------------------------------------------------------


def _tasks_argv(capabilities=None):
    argv = ["tasks"]
    if capabilities:
        argv += ["--capabilities", str(capabilities)]
    return argv


def test_tasks_refuses_a_named_record_that_does_not_exist(tmp_path, capsys):
    code = tasks.main(_cli(_tasks_argv(tmp_path / "typo.json")))
    assert code == 2
    err = capsys.readouterr().err
    assert "is not a file" in err
    assert "capability record" in err


def test_tasks_still_gates_on_a_record_it_can_read(tmp_path, capsys):
    """The fix must not turn a working gate into a refusal."""
    path = tmp_path / "capabilities.json"
    path.write_text(
        json.dumps(
            {"status": "scanned", "capabilities": {"filesystem": {"present": False}, "tools": {"present": True}}}
        ),
        encoding="utf-8",
    )
    assert tasks.main(_cli(_tasks_argv(path))) == 0
    out = capsys.readouterr().out
    assert "skipped on capability grounds" in out
    assert "core/single-tool" in out


def test_tasks_with_no_record_named_still_skips_nothing(capsys):
    assert tasks.main(_cli(["tasks"])) == 0
    out = capsys.readouterr().out
    assert "skipped on capability grounds" not in out


# ---------------------------------------------------------------------------
# doctor: the command whose whole job is to describe the install honestly.
# ---------------------------------------------------------------------------


def test_doctor_refuses_a_named_record_that_does_not_exist(tmp_path, capsys):
    code = doctor.main(_cli(["doctor", "--capabilities", str(tmp_path / "typo.json")]))
    assert code == 2
    assert "is not a file" in capsys.readouterr().err


def test_doctor_with_no_record_named_still_reports_the_scan_absent(capsys):
    assert doctor.main(_cli(["doctor"])) == 0
    assert "absent" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# diagnose: the same rule, on the other optional file it takes.
# ---------------------------------------------------------------------------


def test_diagnose_refuses_a_named_response_that_does_not_exist(tmp_path, capsys):
    code = diagnose.main(_cli(["diagnose", str(tmp_path), "--response", str(tmp_path / "typo.json")]))
    assert code == 2
    err = capsys.readouterr().err
    assert "is not a file" in err
    assert "--response" in err


def test_diagnose_still_merges_a_named_response_that_exists(tmp_path, capsys):
    """The control: a real answer is still folded in, by the same command."""
    cfg = Config(
        {
            "harness": {"id": "t", "adapter_level": "L1"},
            "mode": {"subject": "harness", "execution": "observe"},
            "models": {"harness": "m", "diagnostician": "big-model"},
            "report": {"min_tasks_for_fingerprint": 1},
        },
        source="<test>",
    )

    records = [
        {
            "task_id": f"t{i}",
            "tags": [],
            "task": {},
            "steps": normalize_trace(
                [
                    {
                        "kind": "tool_call",
                        "tool": "read_file",
                        "args": {"path": "cfg.py"},
                        "result_kind": "ok",
                        "started_ms": j * 10,
                        "duration_ms": 10,
                    }
                    for j in range(5)
                ]
            ),
            "source": "test",
        }
        for i in range(3)
    ]
    rundir = execute(cfg, records, str(tmp_path / "run"))

    # First pass with no answer, which is what writes the request.
    assert diagnose.main(_cli(["diagnose", rundir.root])) == 0
    with open(os.path.join(rundir.root, diagnose.REQUEST_FILE), "r", encoding="utf-8") as handle:
        requested = json.load(handle)
    finding_id = requested["findings"][0]["finding_id"]

    answer = tmp_path / "answer.json"
    answer.write_text(
        json.dumps(
            {"findings": [{"finding_id": finding_id, "where": "prompts/system.md", "how": "add a guard", "confidence": "high"}]}
        ),
        encoding="utf-8",
    )
    assert diagnose.main(_cli(["diagnose", rundir.root, "--response", str(answer), "--no-request"])) == 0

    with open(os.path.join(rundir.root, diagnose.DIAGNOSIS_FILE), "r", encoding="utf-8") as handle:
        doc = json.load(handle)
    assert doc["diagnostician_response_merged"] == 1
