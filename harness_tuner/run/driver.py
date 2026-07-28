"""Drive mode: invoke the harness on a fixed task set.

This is the mode that makes controlled comparison possible. The same tasks, the
same seed, the same order, so that a difference between two runs is a
difference in the harness rather than in what it was asked to do. The e-process
and the verify command both depend on it.

It is also the only mode that executes anything, so it is the only mode with a
blast radius. The containment answer is checked here at execution time as well
as during config validation, because the run that matters is the one that
actually starts, and it is stamped into the artifacts so a run is always
self-describing about what it was allowed to reach.

harness-tuner prescribes no containment mechanism. It has no opinion about
whether your machine should use a container, a virtual machine, a throwaway
clone, or a separate user account, because it cannot know what your machine
supports. It does insist that somebody wrote down the answer and approved it.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from typing import Any

from ..protocol import TraceError, normalize_trace
from .budget import BudgetExceeded, Governor
from .cassette import Cassette

PLACEHOLDERS = ("task_id", "prompt", "workspace", "out", "seed")


class DriveError(RuntimeError):
    """The harness could not be driven."""


class ContainmentError(DriveError):
    """Driving was attempted without a recorded, approved containment answer."""


def render_argv(template: list[str], values: dict[str, str]) -> list[str]:
    """Substitute placeholders into the invoke template.

    Unknown placeholders raise rather than being left in the argv, because a
    literal ``{outdir}`` reaching the harness produces a confusing failure
    several layers away from the typo that caused it.
    """
    if not template:
        raise DriveError("harness.invoke is empty, so there is nothing to run")
    out: list[str] = []
    for part in template:
        try:
            out.append(part.format(**values))
        except KeyError as exc:
            raise DriveError(
                f"harness.invoke contains {{{exc.args[0]}}}, which is not a known placeholder. "
                f"Known placeholders: {', '.join('{' + p + '}' for p in PLACEHOLDERS)}"
            ) from exc
        except (IndexError, ValueError) as exc:
            raise DriveError(f"harness.invoke entry {part!r} is not a valid template: {exc}") from exc
    return out


def check_containment(cfg) -> None:
    """Refuse to execute anything without a recorded, approved answer."""
    if not cfg.get("containment.approved"):
        raise ContainmentError(
            "containment.approved is false. A driven run executes your harness with its real "
            "tool access, so before driving, record what it may reach in containment.reach, "
            "record the mechanism your machine actually supports in containment.mechanism, "
            "and set containment.approved. harness-tuner does not prescribe a mechanism; it "
            "requires that somebody decided and wrote it down."
        )
    if not cfg.get("containment.reach"):
        raise ContainmentError(
            "containment.approved is set but containment.reach is empty. Every run artifact "
            "records what the run could touch, and an empty reach makes the artifact "
            "unreadable later."
        )


def _read_intake(path: str, task: dict[str, Any]) -> dict[str, Any]:
    if not os.path.isfile(path):
        raise DriveError(
            f"the harness exited without writing an intake file to {path}. "
            "Your adapter is responsible for writing one file per task at the {out} path."
        )
    with open(path, "r", encoding="utf-8") as handle:
        try:
            raw = json.load(handle)
        except json.JSONDecodeError as exc:
            raise DriveError(f"{path}: the harness wrote invalid JSON: {exc}") from exc
    if "trace" not in raw:
        raise DriveError(f"{path}: the harness wrote no 'trace' key")
    try:
        return {"steps": normalize_trace(raw["trace"]), "task_extra": raw.get("task") or {}}
    except TraceError as exc:
        raise DriveError(f"{path}: {exc}") from exc


def drive_one(
    cfg,
    task: dict[str, Any],
    *,
    seed: int,
    workspace: str,
) -> dict[str, Any]:
    """Invoke the harness once for one task and read back what it produced."""
    template = list(cfg.get("harness.invoke"))
    timeout = int(cfg.get("harness.timeout_s")) or None

    scratch = tempfile.mkdtemp(prefix="harness-tuner-")
    out_path = os.path.join(scratch, "intake.json")
    try:
        argv = render_argv(
            template,
            {
                "task_id": str(task["task_id"]),
                "prompt": str(task.get("prompt", "")),
                "workspace": workspace,
                "out": out_path,
                "seed": str(seed),
            },
        )
        try:
            completed = subprocess.run(
                argv,
                cwd=workspace,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            raise DriveError(f"cannot execute {argv[0]!r}: {exc}") from exc
        except subprocess.TimeoutExpired as exc:
            raise DriveError(
                f"task {task['task_id']!r} exceeded harness.timeout_s ({timeout}s). "
                "A harness that hangs is a finding; raise the timeout only if the task is "
                "genuinely long rather than stuck."
            ) from exc

        payload = _read_intake(out_path, task)
        payload["exit_code"] = completed.returncode
        payload["stderr_tail"] = (completed.stderr or "")[-2000:]
        return payload
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _task_meta(task: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    meta = dict(extra)
    for key in ("optimal_steps", "success", "read_tool_hints"):
        if key in task and key not in meta:
            meta[key] = task[key]
    return meta


def drive(
    cfg,
    tasks: list[dict[str, Any]],
    *,
    workspace: str,
    cassette: Cassette | None,
    governor: Governor,
    seed: int,
    on_event=None,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Run every task, honouring the cassette and the budget ceiling.

    Returns the task records plus a halt record, which is ``None`` when the run
    completed. A halted run is still a run: the caller writes all eight
    artifacts for whatever was measured before the ceiling was reached.
    """
    check_containment(cfg)

    config_digest = cfg.get("harness.adapter") + "|" + cfg.get("models.harness")
    records: list[dict[str, Any]] = []
    halt: dict[str, Any] | None = None

    for task in tasks:
        task_id = str(task["task_id"])
        key = Cassette.key(task_id, seed, config_digest) if cassette else None

        payload = cassette.get(key) if cassette else None
        replayed = payload is not None
        if payload is None:
            payload = drive_one(cfg, task, seed=seed, workspace=workspace)
            if cassette:
                cassette.put(key, payload)
        else:
            payload = {"steps": normalize_trace(payload["steps"]), **{
                k: v for k, v in payload.items() if k != "steps"
            }}

        if on_event:
            on_event(task_id, "replayed" if replayed else "driven")

        steps = payload["steps"]
        records.append(
            {
                "task_id": task_id,
                "tags": task.get("tags") or [],
                "task": _task_meta(task, payload.get("task_extra") or {}),
                "steps": steps,
                "source": "cassette" if replayed else "driven",
            }
        )

        # A replayed task cost nothing, so charging for it would make the
        # governor fire on a free run.
        if replayed:
            continue
        cost = sum(s["cost_usd"] for s in steps if s["cost_usd"] is not None) or None
        tokens = sum(
            (s["tokens"]["input"] or 0) + (s["tokens"]["output"] or 0) for s in steps
        ) or None
        try:
            governor.charge(cost_usd=cost, tokens=tokens, task_id=task_id)
        except BudgetExceeded as exc:
            halt = {
                "halted": True,
                "at_task": task_id,
                "reason": str(exc),
                "tasks_completed": len(records),
                "tasks_planned": len(tasks),
                "budget": exc.state,
            }
            break

    if cassette:
        cassette.save()
    return records, halt
