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
from typing import Any

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
          tasks/<task_id>/metrics.json

    The root ``trace.jsonl`` is the merged view across tasks. Each of its
    records carries an ``x_task_id`` extension naming the task it came from,
    and its ``step`` stays the within-task step number rather than being
    renumbered, so a record in the merged file and the same record in the
    per-task file are identical apart from that one added field.
    """

    def __init__(self, root: str):
        self.root = os.path.abspath(root)

    def path(self, key: str) -> str:
        if key not in ARTIFACTS:
            raise KeyError(f"{key!r} is not an HTP-1 artifact; known: {sorted(ARTIFACTS)}")
        return os.path.join(self.root, ARTIFACTS[key])

    def task_dir(self, task_id: str) -> str:
        return os.path.join(self.root, TASKS_DIR, task_id)

    def task_path(self, task_id: str, key: str) -> str:
        return os.path.join(self.task_dir(task_id), ARTIFACTS[key])

    def ensure(self) -> None:
        os.makedirs(self.root, exist_ok=True)

    def missing(self) -> list[str]:
        """Artifact filenames that should exist here and do not."""
        return [name for name in ARTIFACT_NAMES if not os.path.isfile(os.path.join(self.root, name))]

    def is_complete(self) -> bool:
        return not self.missing()
