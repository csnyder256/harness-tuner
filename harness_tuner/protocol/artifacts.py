"""HTP-1 artifacts: the exact files a run emits, and their exact names.

Every run writes all eight files. A run that could not produce the content for
one of them still writes it, carrying the reason, because a missing file is
indistinguishable from a crashed run while a file that says why it is empty is
evidence.

Any tool that can read these eight files can consume a run produced by any
HTP-1 implementation, which is the whole point of fixing them.
"""

from __future__ import annotations

import os
from typing import Any, Iterable

from .trace import HTP_VERSION, canonical_json

ENGINE_NAME = "harness-tuner"
ENGINE_VERSION = "0.1.0"
IMPLEMENTATION = "reference-python"

#: The normative artifact set. Keys are stable identifiers, values are the
#: filenames on disk. Neither may change within HTP-1.
ARTIFACTS: dict[str, str] = {
    "run": "run.json",
    "trace": "trace.jsonl",
    "metrics": "metrics.json",
    "summary": "summary.md",
    "report": "report.html",
    "fingerprint": "fingerprint.json",
    "manifest": "manifest.json",
    "capabilities": "capabilities.json",
}

ARTIFACT_NAMES: tuple[str, ...] = tuple(sorted(ARTIFACTS.values()))

#: Files that may appear beside a run without being artifacts.
#:
#: These belong to the delegated model path: the engine writes a request, your
#: agent writes the answer back, and the engine folds it in. They are declared
#: here rather than left ad hoc so that a run directory containing anything
#: else is a drift worth failing on. The protocol fixes eight artifacts; this
#: list is the complete set of everything else that is allowed to appear.
SIDE_FILES: tuple[str, ...] = (
    "judge-request.json",
    "judge-response.json",
    "diagnosis-request.json",
    "diagnosis-response.json",
    "diagnosis.json",
    "diagnosis.md",
)

TASKS_DIR = "tasks"

#: A synthetic absolute base used to decide whether a task id stays inside the
#: run's ``tasks/`` directory. It is never touched on disk; it exists so the
#: containment rule is evaluated with the platform's own path semantics, which
#: is the only way the answer is right on both a POSIX machine and Windows.
_TASKS_BASE = os.path.join(os.sep, "tasks")


class TaskIdError(ValueError):
    """A task id cannot name a directory inside the run."""


def check_task_id(task_id: Any) -> str:
    """Refuse a task id that would not stay inside the run's ``tasks/`` directory.

    A task id is a *name*, but the protocol uses it as the last part of a path:
    the per-task trace lives at ``tasks/<task_id>/trace.jsonl`` inside the run
    directory. So the id decides where the engine writes, and the id is input
    rather than fact -- it comes from an adapter reading a harness, or from a
    task set a model wrote.

    An id containing a path separator or a ``..`` segment is therefore not a
    naming quirk, it is a destination. Reproduced before this check, an intake
    file naming its task ``../../../tmp/elsewhere`` had its per-task trace
    written to ``/tmp/elsewhere/trace.jsonl``: outside the run directory the
    report then describes, and outside the eight files a run is defined to be.
    The escape was silent -- the run exited zero and every artifact looked
    normal -- which is exactly the failure this project exists to refuse.

    A namespaced id is legitimate and stays legal: the generated task set names
    its tasks ``generated/<id>``. What is refused is an id that resolves above
    ``tasks/`` or to ``tasks/`` itself, and it is refused rather than sanitised,
    because a run whose task directory was silently renamed is a run whose files
    and whose report no longer agree on what the task is called.

    Returns the id unchanged, so a caller can validate and use it in one step.
    """
    if not isinstance(task_id, str) or not task_id:
        raise TaskIdError(
            f"a task id must be a non-empty string, got {task_id!r}. The id names the "
            "directory its per-task trace is written to, at tasks/<task_id>/trace.jsonl."
        )
    resolved = os.path.normpath(os.path.join(_TASKS_BASE, task_id))
    if resolved == _TASKS_BASE or not resolved.startswith(_TASKS_BASE + os.sep):
        raise TaskIdError(
            f"task id {task_id!r} would not stay inside the run's tasks/ directory. The id "
            "names a directory: the per-task trace is written to tasks/<task_id>/trace.jsonl, "
            "so an id containing '..' or starting with a path separator sends that file "
            "outside the run it belongs to, where nothing the run reports refers to it. A "
            "namespaced id such as 'generated/<id>' is fine; an id that resolves above tasks/ "
            "is refused rather than renamed, because a run's files and its report have to "
            "agree on what a task is called."
        )
    return task_id


def check_task_ids(task_ids: Iterable[Any]) -> None:
    """Check a whole task set at once, naming every id that would escape.

    A driven run uses this to refuse the set before it invokes anything: a
    collision discovered while the artifacts are written would have been found
    after every task had already been driven and paid for.
    """
    offenders = []
    for task_id in task_ids:
        try:
            check_task_id(task_id)
        except TaskIdError:
            offenders.append(repr(task_id))
    if offenders:
        raise TaskIdError(
            f"{len(offenders)} task id(s) would not stay inside the run's tasks/ directory: "
            f"{', '.join(offenders)}. A task id names the directory its per-task trace is "
            "written to, at tasks/<task_id>/trace.jsonl, so it may not contain '..' or start "
            "with a path separator. A namespaced id such as 'generated/<id>' is fine."
        )


def envelope(run_id: str, kind: str) -> dict[str, Any]:
    """The header every JSON artifact carries.

    It records which protocol version produced the file and which
    implementation did it, so a report read six months later can be traced to
    the code that made it.
    """
    return {
        "htp_version": HTP_VERSION,
        "artifact": kind,
        "run_id": run_id,
        "engine": {
            "name": ENGINE_NAME,
            "version": ENGINE_VERSION,
            "implementation": IMPLEMENTATION,
        },
    }


def write_json(path: str, payload: dict[str, Any]) -> None:
    """Write a JSON artifact deterministically.

    Sorted keys, LF line endings and a trailing newline, so two runs that
    computed the same thing produce byte-identical files. Cassette replay
    verification compares these bytes.
    """
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(canonical_json(payload))
        handle.write("\n")


def write_text(path: str, text: str) -> None:
    """Write a text artifact with LF endings regardless of platform."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    if not text.endswith("\n"):
        text += "\n"
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


class RunDir:
    """The directory holding one run's artifacts.

    Layout::

        <root>/
          run.json  trace.jsonl  metrics.json  summary.md
          report.html  fingerprint.json  manifest.json  capabilities.json
          tasks/<task_id>/trace.jsonl

    The root ``trace.jsonl`` is the merged view across tasks. Each of its
    records carries an ``x_task_id`` extension naming the task it came from,
    and its ``step`` stays the within-task step number rather than being
    renumbered, so a record in the merged file and the same record in the
    per-task file are identical apart from that one added field.

    The task id is used as a directory name, so it is checked before it is
    joined: see :func:`check_task_id`. Every per-task read and write goes
    through :meth:`task_dir`, which is what makes that check unconditional.
    """

    def __init__(self, root: str):
        self.root = os.path.abspath(root)

    def path(self, key: str) -> str:
        if key not in ARTIFACTS:
            raise KeyError(f"{key!r} is not an HTP-1 artifact; known: {sorted(ARTIFACTS)}")
        return os.path.join(self.root, ARTIFACTS[key])

    def task_dir(self, task_id: str) -> str:
        """The directory holding one task's trace, inside this run.

        ``task_id`` comes from an adapter or a task set, so it is validated as a
        relative path that stays inside ``tasks/`` before it is used as one.
        """
        return os.path.join(self.root, TASKS_DIR, check_task_id(task_id))

    def task_path(self, task_id: str, key: str) -> str:
        return os.path.join(self.task_dir(task_id), ARTIFACTS[key])

    def ensure(self) -> None:
        os.makedirs(self.root, exist_ok=True)

    def missing(self) -> list[str]:
        """Artifact filenames that should exist here and do not."""
        return [name for name in ARTIFACT_NAMES if not os.path.isfile(os.path.join(self.root, name))]

    def is_complete(self) -> bool:
        return not self.missing()
