"""The capability record: what the setup agent found, or an explicit not-scanned.

Capability discovery is performed by the agent that sets harness-tuner up, by
reading the harness, and its findings are written to a JSON file. That file is
the only thing that decides which benchmark tasks are skipped, because guessing
that a harness lacks a capability would silently shrink the task set and inflate
every rate computed from it.

Two situations look alike and mean opposite things:

* **No file named.** No capability scan exists. Nothing is skipped on
  capability grounds, and the run artifact says so in as many words.
* **A file named that cannot be read.** The operator meant to gate the task set
  on a scan, and the gate is not there. Reporting this as "no file named" is the
  same class of mistake the protocol forbids one layer down when it refuses to
  read an unobserved value as zero: the two situations call for opposite
  actions, and only one of them is what was asked for.

:func:`resolve` is the gate. A path the operator named has to exist, and a file
that exists has to parse into an object, because a mistyped path that runs the
whole pipeline in silence is exactly the failure this module exists to stop. The
run's own ``capabilities.json`` used to record "no capabilities file was
supplied" as the reason no benchmark was gated, on a run where a file *was*
named and then ignored.
"""

from __future__ import annotations

import json
import os
from typing import Any

#: The reason written into a run when no capability file was named at all. It is
#: a statement about the run, so it has to be true of the run it lands in.
NOT_SCANNED_REASON = (
    "no capabilities file was supplied. Capability discovery is performed by the "
    "agent that set harness-tuner up, by reading the harness, and its findings are "
    "written to a capabilities file. Without it, no benchmark was skipped on "
    "capability grounds and none was scored as failed on capability grounds either."
)


class CapabilityError(ValueError):
    """A capability record the operator named could not be used."""


def not_scanned() -> dict[str, Any]:
    """The record for a run where no capability file was named."""
    return {
        "status": "not_scanned",
        "reason": NOT_SCANNED_REASON,
        "capabilities": {},
        "source": None,
    }


def resolve(path: str | None) -> str | None:
    """The path to read, or ``None`` when the operator named no file.

    Raises :class:`CapabilityError` when a named path is not a file, so a typo is
    caught before anything runs rather than quietly becoming "no scan happened".
    """
    if not path:
        return None
    if not os.path.isfile(path):
        raise CapabilityError(
            f"{path} is not a file. --capabilities names the capability record your setup "
            "agent wrote after reading the harness, and naming a path that does not exist "
            "is not the same as naming none: the first asks for a scan that gates the task "
            "set, the second asks for no gate at all. Point it at the file, or omit the "
            "flag to run with no capability scan (nothing is then skipped on capability "
            "grounds)."
        )
    return path


def load(path: str | None) -> dict[str, Any]:
    """Read the capability record, or return the explicit not-scanned record."""
    resolved = resolve(path)
    if resolved is None:
        return not_scanned()

    try:
        with open(resolved, "r", encoding="utf-8") as handle:
            record = json.load(handle)
    except OSError as exc:
        raise CapabilityError(f"{resolved} could not be read: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise CapabilityError(
            f"{resolved} is not valid JSON: {exc}. It is the capability record your setup "
            "agent wrote, and it has to parse before any of it can gate a task."
        ) from exc

    if not isinstance(record, dict):
        raise CapabilityError(
            f"{resolved} holds a JSON {type(record).__name__}. A capability record is an "
            "object with a 'status' and a 'capabilities' block; anything else would be read "
            "as no scan at all, which is the mistake this check exists to prevent."
        )

    # The artifact has to name where the record came from, so a report read later
    # can be traced to the file that gated its task set.
    record.setdefault("source", os.path.abspath(resolved))
    return record
