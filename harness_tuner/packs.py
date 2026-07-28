"""Evaluation packs: declarative task sets.

Packs are TOML, read with ``tomllib`` from the standard library. YAML would be
the conventional choice and it is not available: Python ships a TOML reader and
no YAML reader, so a YAML pack format would break the zero-dependency promise
on the first install. TOML is also stricter, which for a file format people
hand-write is a feature.

The part worth understanding is capability gating. A pack declares what a task
needs, the setup agent records what the harness actually has, and a task whose
requirements are not met is SKIPPED and recorded as skipped. It is never scored
as a failure. A harness with no browser is not worse at browsing than one with
a broken browser, it is a different thing, and a benchmark that conflated the
two would rank harnesses by how many features they happen to have rather than
by how well they use what they have.

Skips are counted and reported. Silent skipping would be its own dishonesty:
"18 of 18 passed" reads very differently when nine were never attempted.
"""

from __future__ import annotations

import os
import tomllib
from typing import Any

PACKS_DIRNAME = "packs"


class PackError(ValueError):
    """A pack is malformed."""


def packs_root(explicit: str | None = None) -> str:
    if explicit:
        return os.path.abspath(explicit)
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(os.path.dirname(here), PACKS_DIRNAME)


def _load_file(path: str) -> dict[str, Any]:
    with open(path, "rb") as handle:
        try:
            data = tomllib.load(handle)
        except tomllib.TOMLDecodeError as exc:
            raise PackError(f"{path}: {exc}") from exc

    meta = data.get("pack")
    if not isinstance(meta, dict) or not meta.get("name"):
        raise PackError(f"{path}: needs a [pack] table with a name")
    tasks = data.get("task") or []
    if not isinstance(tasks, list) or not tasks:
        raise PackError(f"{path}: needs at least one [[task]]")

    seen: set[str] = set()
    normalized = []
    for i, task in enumerate(tasks):
        task_id = task.get("id")
        if not task_id:
            raise PackError(f"{path}: task {i} has no id")
        if task_id in seen:
            raise PackError(f"{path}: duplicate task id {task_id!r}")
        seen.add(task_id)
        if not task.get("prompt"):
            raise PackError(f"{path}: task {task_id!r} has no prompt")
        normalized.append(
            {
                "task_id": f"{meta['name']}/{task_id}",
                "prompt": task["prompt"],
                "tags": list(task.get("tags") or []),
                "requires": list(task.get("requires") or []),
                "optimal_steps": task.get("optimal_steps"),
                "read_tool_hints": list(task.get("read_tool_hints") or []),
                "pack": meta["name"],
                "provenance": {
                    "pack": meta["name"],
                    "pack_version": str(meta.get("version", "0")),
                    "source": os.path.abspath(path),
                    "author": meta.get("author"),
                    "inspired_by": meta.get("inspired_by"),
                },
            }
        )

    return {
        "name": meta["name"],
        "version": str(meta.get("version", "0")),
        "description": meta.get("description", ""),
        "author": meta.get("author"),
        "path": os.path.abspath(path),
        "tasks": normalized,
    }


def available(root: str | None = None) -> list[dict[str, Any]]:
    """Every pack shipped with, or installed beside, this engine."""
    base = packs_root(root)
    if not os.path.isdir(base):
        return []
    out = []
    for name in sorted(os.listdir(base)):
        directory = os.path.join(base, name)
        if not os.path.isdir(directory):
            continue
        for filename in sorted(os.listdir(directory)):
            if filename.endswith(".toml"):
                out.append(_load_file(os.path.join(directory, filename)))
    return out


def load(names: list[str], root: str | None = None) -> list[dict[str, Any]]:
    """Load the named packs, or raise naming the ones that do not exist."""
    found = {pack["name"]: pack for pack in available(root)}
    missing = [n for n in names if n not in found]
    if missing:
        raise PackError(
            f"unknown pack(s): {missing}. Available: {sorted(found)}. "
            "Packs are directories of TOML under the packs/ directory."
        )
    return [found[n] for n in names]


def _capability_set(capabilities: dict[str, Any] | None) -> tuple[set[str], bool]:
    """What the harness has, and whether anybody actually looked."""
    if not capabilities or capabilities.get("status") == "not_scanned":
        return set(), False
    block = capabilities.get("capabilities") or {}
    have = {
        name
        for name, entry in block.items()
        if (entry.get("present") if isinstance(entry, dict) else bool(entry))
    }
    return have, True


def select(
    packs: list[dict[str, Any]],
    capabilities: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split a pack's tasks into those that can run and those that cannot.

    When no capability scan exists, nothing is skipped. Guessing that a harness
    lacks a capability, and quietly dropping tasks on that guess, would silently
    shrink the task set and inflate every rate computed from it.
    """
    have, scanned = _capability_set(capabilities)
    runnable: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for pack in packs:
        for task in pack["tasks"]:
            needs = set(task["requires"])
            if not needs or not scanned:
                runnable.append(task)
                continue
            unmet = sorted(needs - have)
            if unmet:
                skipped.append(
                    {
                        "task_id": task["task_id"],
                        "requires": sorted(needs),
                        "unmet": unmet,
                        "reason": (
                            f"this harness does not have {', '.join(unmet)}. The task was "
                            "skipped, not failed: a harness that lacks a capability is a "
                            "different thing from one whose capability is broken."
                        ),
                    }
                )
            else:
                runnable.append(task)
    return runnable, skipped


def add_arguments(parser) -> None:
    parser.add_argument("--root", default=None, metavar="DIR", help="a packs directory to list")


def main(args) -> int:
    packs = available(args.root)
    if not packs:
        print("no packs found")
        return 0
    for pack in packs:
        tags: dict[str, int] = {}
        requires: set[str] = set()
        for task in pack["tasks"]:
            for tag in task["tags"]:
                tags[tag] = tags.get(tag, 0) + 1
            requires |= set(task["requires"])
        print(f"{pack['name']}  v{pack['version']}  ({len(pack['tasks'])} tasks)")
        print(f"  {pack['description']}")
        if requires:
            print(f"  requires: {', '.join(sorted(requires))}")
        if tags:
            print(f"  tags: {', '.join(f'{k} x{v}' for k, v in sorted(tags.items()))}")
    return 0
