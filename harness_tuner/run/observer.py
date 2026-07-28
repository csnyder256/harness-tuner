"""Observe mode: read traces the harness already produced.

The adapter contract is deliberately one shape, and it is the same shape as the
conformance fixtures, so anyone who can read a fixture can write an adapter.
Each task is one JSON file::

    {
      "task_id": "read-and-summarize",
      "tags": ["coding"],
      "task": {"optimal_steps": 4, "success": true},
      "trace": [ {"kind": "tool_call", "tool": "read_file", ...}, ... ]
    }

Nothing above is required except ``trace``. A harness that can only tell you
which tools ran, in what order, still produces a usable file, and the report
says exactly which metrics that costs.

Observe mode never invokes anything. It is the only mode available for a
harness with no scriptable entry point, and it is the right default for
measuring real usage rather than synthetic tasks.
"""

from __future__ import annotations

import json
import os
from typing import Any

from ..protocol import TraceError, normalize_trace


class IntakeError(ValueError):
    """An adapter produced something the engine cannot read."""


def _read_one(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        try:
            raw = json.load(handle)
        except json.JSONDecodeError as exc:
            raise IntakeError(f"{path}: not valid JSON: {exc}") from exc

    if not isinstance(raw, dict):
        raise IntakeError(f"{path}: expected an object at the top level")
    if "trace" not in raw:
        raise IntakeError(
            f"{path}: no 'trace' key. Every intake file must carry a trace, even an empty one, "
            "because a task that produced no steps is a finding rather than a missing file."
        )

    task_id = raw.get("task_id") or raw.get("name") or os.path.splitext(os.path.basename(path))[0]
    tags = raw.get("tags") or []
    if not isinstance(tags, list):
        raise IntakeError(f"{path}: 'tags' must be a list")

    try:
        steps = normalize_trace(raw["trace"])
    except TraceError as exc:
        raise IntakeError(f"{path}: {exc}") from exc

    return {
        "task_id": str(task_id),
        "tags": [str(t) for t in tags],
        "task": raw.get("task") or {},
        "steps": steps,
        "source": os.path.abspath(path),
    }


def collect(path: str) -> list[dict[str, Any]]:
    """Read one intake file, or every ``*.json`` in a directory.

    Files are read in sorted order so a run over the same inputs produces the
    same artifacts regardless of filesystem ordering.
    """
    path = os.path.abspath(path)
    if os.path.isfile(path):
        return [_read_one(path)]
    if not os.path.isdir(path):
        raise IntakeError(f"{path}: no such file or directory")

    names = sorted(n for n in os.listdir(path) if n.endswith(".json"))
    if not names:
        raise IntakeError(f"{path}: contains no .json intake files")
    return [_read_one(os.path.join(path, name)) for name in names]
