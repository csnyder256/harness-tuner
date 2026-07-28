"""Assembling the task set: shipped packs plus tasks generated for your job.

Shipped packs exist so a stranger can run something in the first five minutes.
They are not the point. A harness that summarizes support tickets and one that
refactors Go services share almost nothing, and scoring both against the same
generic task set measures how well each one impersonates the other.

So the real task set is generated for the harness's actual job by the model at
``models.generator``, once, at setup. The engine does not call it. It writes a
request describing what a task must contain, your agent answers with a file,
and every generated task is stamped with its provenance so a finding can always
be traced back to where its task came from.

Which shipped packs apply by default depends on ``mode.subject``, because the
three subjects are genuinely different questions. Measuring the harness means
exercising scaffolding: memory, restraint, boundaries. Measuring the agent
built on it means exercising that agent's own job.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from . import packs as PK
from .config import load as load_config

GENERATION_REQUEST = "taskset-request.json"
GENERATED_FILENAME = "generated.json"

#: mode.subject -> the packs that apply when tasks.packs is left empty.
DEFAULT_PACKS: dict[str, list[str]] = {
    "harness": ["core", "memory", "safety"],
    "agent": ["core"],
    "both": ["core", "coding", "memory", "safety"],
}


def load_generated(directory: str) -> list[dict[str, Any]]:
    """Read the task set your agent generated for this harness's real job."""
    if not directory:
        return []
    path = directory
    if os.path.isdir(directory):
        path = os.path.join(directory, GENERATED_FILENAME)
    if not os.path.isfile(path):
        return []

    with open(path, "r", encoding="utf-8") as handle:
        raw = json.load(handle)
    tasks = raw.get("tasks") if isinstance(raw, dict) else raw
    out = []
    for i, task in enumerate(tasks or []):
        task_id = task.get("id") or task.get("task_id")
        if not task_id:
            raise ValueError(f"{path}: generated task {i} has no id")
        out.append(
            {
                "task_id": f"generated/{task_id}" if not str(task_id).startswith("generated/") else str(task_id),
                "prompt": task.get("prompt", ""),
                "tags": list(task.get("tags") or []),
                "requires": list(task.get("requires") or []),
                "optimal_steps": task.get("optimal_steps"),
                "read_tool_hints": list(task.get("read_tool_hints") or []),
                "pack": "generated",
                "provenance": {
                    "pack": "generated",
                    "pack_version": str(raw.get("version", "1")) if isinstance(raw, dict) else "1",
                    "source": os.path.abspath(path),
                    "generated_by": raw.get("generated_by") if isinstance(raw, dict) else None,
                    "generated_for": raw.get("generated_for") if isinstance(raw, dict) else None,
                },
            }
        )
    return out


def assemble(cfg, capabilities: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build the task set for this configuration.

    Returns the runnable tasks, the skipped ones with their reasons, and a
    provenance summary. Skips are returned rather than dropped so the report can
    say how many tasks were never attempted.
    """
    names = list(cfg.get("tasks.packs"))
    subject = cfg.get("mode.subject")
    defaulted = False
    if not names:
        names = DEFAULT_PACKS.get(subject, DEFAULT_PACKS["harness"])
        defaulted = True

    loaded = PK.load(names)
    runnable, skipped = PK.select(loaded, capabilities)

    generated = load_generated(cfg.get("tasks.generated_dir"))
    if generated:
        gen_runnable, gen_skipped = PK.select(
            [{"name": "generated", "tasks": generated}], capabilities
        )
        runnable = runnable + gen_runnable
        skipped = skipped + gen_skipped

    by_pack: dict[str, int] = {}
    for task in runnable:
        by_pack[task["pack"]] = by_pack.get(task["pack"], 0) + 1

    return {
        "tasks": runnable,
        "skipped": skipped,
        "provenance": {
            "subject": subject,
            "packs_requested": names,
            "packs_defaulted_from_subject": defaulted,
            "generated_tasks": len(generated),
            "generated_dir": cfg.get("tasks.generated_dir") or None,
            "tasks_by_pack": by_pack,
            "tasks_runnable": len(runnable),
            "tasks_skipped": len(skipped),
        },
    }


def build_generation_request(cfg) -> dict[str, Any]:
    """What the generator model is asked, and the shape it must answer in."""
    return {
        "instructions": (
            "Write a task set for THIS harness's actual job, not a generic benchmark. "
            "Each task must be checkable: someone reading the trace afterwards has to be able "
            "to tell whether it was accomplished. Prefer tasks that take several steps, since "
            "a one-shot question exercises almost none of a harness. State optimal_steps only "
            "when you are confident of it; leaving it out makes step_efficiency unavailable, "
            "which is better than making it wrong. List a capability in 'requires' whenever a "
            "task cannot run without it, so a harness that lacks it is skipped rather than "
            "failed."
        ),
        "model_requested": cfg.get("models.generator") or "(models.generator is unset)",
        "subject_under_test": cfg.get("mode.subject"),
        "harness": {
            "id": cfg.get("harness.id"),
            "adapter_level": cfg.get("harness.adapter_level"),
        },
        "answer_format": {
            "file": os.path.join(cfg.get("tasks.generated_dir") or "tasks", GENERATED_FILENAME),
            "shape": {
                "generated_by": "the model you used",
                "generated_for": "one sentence on what this harness is for",
                "version": "1",
                "tasks": [
                    {
                        "id": "kebab-case-id",
                        "prompt": "what the harness is asked to do",
                        "tags": ["coding"],
                        "requires": ["filesystem"],
                        "optimal_steps": 3,
                    }
                ],
            },
        },
    }


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", default=None, metavar="FILE", help="path to harness-tuner.toml")
    parser.add_argument(
        "--capabilities", default=None, metavar="FILE", help="capability record from your setup agent"
    )
    parser.add_argument(
        "--request",
        action="store_true",
        help="write the task-generation request for models.generator instead of listing tasks",
    )


def main(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)

    if args.request:
        from .protocol import write_json

        target = cfg.get("tasks.generated_dir") or "tasks"
        path = os.path.join(target, GENERATION_REQUEST)
        write_json(path, build_generation_request(cfg))
        print(f"task-generation request written to {path}")
        print(
            f"Have your agent answer it with {cfg.get('models.generator') or 'models.generator'} "
            f"and write {os.path.join(target, GENERATED_FILENAME)}."
        )
        return 0

    capabilities = None
    if args.capabilities and os.path.isfile(args.capabilities):
        with open(args.capabilities, "r", encoding="utf-8") as handle:
            capabilities = json.load(handle)

    try:
        assembled = assemble(cfg, capabilities)
    except PK.PackError as exc:
        print(f"cannot assemble a task set: {exc}", file=sys.stderr)
        return 2

    prov = assembled["provenance"]
    print(f"subject under test: {prov['subject']}")
    print(
        f"packs: {', '.join(prov['packs_requested'])}"
        + ("  (defaulted from mode.subject)" if prov["packs_defaulted_from_subject"] else "")
    )
    if prov["generated_tasks"]:
        print(f"generated for this harness: {prov['generated_tasks']} task(s)")
    else:
        print(
            "generated for this harness: none. Shipped packs are a starting point, not the "
            "point. Run with --request to produce a task set for what this harness actually does."
        )
    print(f"\n{prov['tasks_runnable']} runnable task(s):")
    for pack, count in sorted(prov["tasks_by_pack"].items()):
        print(f"  {pack}: {count}")

    if assembled["skipped"]:
        print(f"\n{len(assembled['skipped'])} task(s) skipped on capability grounds:")
        for entry in assembled["skipped"]:
            print(f"  {entry['task_id']}  needs {', '.join(entry['unmet'])}")
        print("  Skipped, not failed. They are excluded from every rate computed below.")
    elif capabilities:
        print("\nnothing skipped: this harness meets every declared requirement")
    return 0
