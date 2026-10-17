"""doctor: what this install cannot do, and why.

Every tool has a command that lists what it supports. This one lists what it
does not, because that is the list nobody writes and the one that changes how
you read a report.

The failure it guards against is specific. A harness-tuner install can be
configured perfectly, run without error, and produce a confident-looking report
in which a third of the metrics were never computable, a budget ceiling could
never have fired, and two fingerprint dimensions were never exercised. Nothing
about that run looks wrong. The exit code is zero.

So doctor reports the gaps first and by name: what the adapter level costs you,
which ceilings are inert, which fingerprint dimensions the task set cannot
reach, whether the engine will be calling models itself, and what `verify`
needs from a run rather than from the adapter level.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from .config import ADAPTER_LEVELS, load as load_config
from .protocol import REGISTRY
from .report.fingerprint import DIMENSIONS
from .score import direct

#: What each adapter level can and cannot produce. The metric lists are the
#: consequence of the level, not a policy: an adapter that reports no tokens
#: cannot produce a token metric no matter how the engine is configured.
LEVEL_COSTS: dict[str, tuple[str, tuple[str, ...]]] = {
    "L0": (
        "Only what an after-the-fact record shows: which tools ran, in what order.",
        (
            "tokens_input", "tokens_output", "tokens_cache_read", "tokens_cache_write",
            "cache_hit_ratio", "cost_usd", "context_peak", "context_growth_per_step",
            "wall_ms", "time_to_first_action_ms", "error_count", "error_rate",
            "recovery_rate",
        ),
    ),
    "L1": ("Full step records with tokens, timings and outcomes.", ()),
    "L2": ("L1 plus a scriptable entry point, so repeated seeded trials are possible.", ()),
    "L3": ("L2 plus programmatic config, so a proposed change can be A/B compared.", ()),
}

#: Metrics that need the *arguments* of a call, not merely its name.
#:
#: A record showing which tools ran and in what order cannot tell two calls to
#: the same tool apart, and every metric below is a statement about repeats: the
#: longest unbroken run of one action, the share of read-shaped calls that
#: re-read something already read, the count of calls repeating an action
#: already taken. Identity is (tool, arguments), so with the arguments
#: unobserved all four report unavailable. The shipped L0 fixture reports
#: exactly that, and it is the reason loop detection cannot be promised to a
#: record that carries tool names and nothing else.
#:
#: This is a second list rather than four more rows in :data:`LEVEL_COSTS`
#: because it is a property of the *record*, not of the level. An adapter that
#: can digest its own log closes all four by supplying ``args_digest``, without
#: storing a single argument, so "L0 cannot detect loops" would be wrong in the
#: other direction. Keeping them apart is what makes that visible.
ARGUMENT_DEPENDENT: tuple[str, ...] = (
    "redundant_call_count",
    "redundant_call_ratio",
    "loop_max_run",
    "rework_ratio",
)

ARGUMENT_REASON = (
    "an L0 record names the tool but not what it was called with, so two calls to the same "
    "tool cannot be told apart. Supply args_digest, a digest of the arguments, from the "
    "adapter's own record to close this without storing any argument."
)


def _verify_note(level: str) -> str:
    """What the pairing step needs at this level.

    ``verify`` compares two completed runs task by task and re-runs nothing, so
    no adapter level gates the command, and saying otherwise told an L0 or L1
    operator that the project's whole propose-then-prove loop was closed to
    them. What drive mode buys is the second run of the pair.
    """
    if level in ("L0", "L1"):
        return (
            "verify compares two completed runs task by task and re-runs nothing itself, so "
            "it works at every level: point it at a run from before the change and a run "
            "from after it, over the same task ids. What this level cannot do is produce "
            "that second run for you. Drive mode re-runs the identical seeded task set, "
            "which needs L2."
        )
    return (
        "verify compares two completed runs task by task. This level can drive, so the "
        "second run can be the identical seeded task set rather than whatever the harness "
        "happened to be asked to do."
    )


def assess(cfg, capabilities: dict[str, Any] | None = None) -> dict[str, Any]:
    level = cfg.get("harness.adapter_level")
    description, at_risk = LEVEL_COSTS.get(level, ("unknown level", ()))

    blocked: list[dict[str, str]] = []
    if level == "L0":
        for metric in at_risk:
            blocked.append(
                {
                    "metric": metric,
                    "reason": "an L0 adapter reports no tokens, timings or outcomes",
                }
            )
        for metric in ARGUMENT_DEPENDENT:
            blocked.append({"metric": metric, "reason": ARGUMENT_REASON})

    ceilings: list[str] = []
    if cfg.get("cost.budget.enabled"):
        max_usd = cfg.get("cost.budget.max_usd")
        max_tokens = cfg.get("cost.budget.max_tokens")
        if max_usd <= 0 and max_tokens <= 0:
            ceilings.append("the budget governor is enabled but both ceilings are zero")
        if max_usd > 0 and level == "L0":
            ceilings.append(
                f"a ceiling of {max_usd} USD is set, but an L0 adapter reports no cost, "
                "so it can never fire"
            )
        if max_tokens > 0 and level == "L0":
            ceilings.append(
                f"a ceiling of {max_tokens} tokens is set, but an L0 adapter reports no "
                "token counts, so it can never fire"
            )

    scanned = bool(capabilities and capabilities.get("status") != "not_scanned")

    return {
        "adapter_level": level,
        "adapter_level_means": description,
        "blocked": blocked,
        "inert_ceilings": ceilings,
        "capabilities_scanned": scanned,
        "model_calls": direct.describe(cfg),
        "verify": _verify_note(level),
        "fingerprint_dimensions_needing_specific_tasks": {
            "memory": "needs at least one task tagged 'memory'",
            "safety": "needs at least one task tagged 'safety'",
            "determinism": "needs at least one task run more than once",
        },
        "metrics_total": len(REGISTRY),
        "dimensions_total": len(DIMENSIONS) + 3,
    }


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", default=None, metavar="FILE", help="path to harness-tuner.toml")
    parser.add_argument(
        "--capabilities", default=None, metavar="FILE", help="capability record from your setup agent"
    )


def main(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    capabilities = None
    if args.capabilities and os.path.isfile(args.capabilities):
        with open(args.capabilities, "r", encoding="utf-8") as handle:
            capabilities = json.load(handle)

    problems = cfg.validate()
    report = assess(cfg, capabilities)

    print(f"config: {cfg.source}")
    if problems:
        print(f"\n{len(problems)} configuration problem(s):")
        for problem in problems:
            print(f"  {problem}")
    else:
        print("configuration is valid")

    level = report["adapter_level"]
    print(f"\nadapter level {level} of {', '.join(ADAPTER_LEVELS)}")
    print(f"  {report['adapter_level_means']}")

    print("\nWhat this install cannot measure:")
    if not report["blocked"]:
        print("  nothing on adapter-level grounds")
    for entry in report["blocked"]:
        print(f"  {entry['metric']}: {entry['reason']}")

    print("\nProving a change helped:")
    print(f"  {report['verify']}")

    print("\nCeilings that cannot fire:")
    if not report["inert_ceilings"]:
        print("  none")
    for text in report["inert_ceilings"]:
        print(f"  {text}")

    print("\nCapability scan:")
    if report["capabilities_scanned"]:
        print("  present. Tasks a harness cannot run will be skipped rather than failed.")
    else:
        print(
            "  absent. Nothing will be skipped on capability grounds, which is deliberate: "
            "guessing a capability is missing would silently shrink the task set."
        )

    print("\nFingerprint dimensions that need a specific kind of task:")
    for name, need in report["fingerprint_dimensions_needing_specific_tasks"].items():
        print(f"  {name}: {need}")

    calls = report["model_calls"]
    print(f"\nModel calls: {calls['mode']}")
    print(f"  {calls['note']}")
    for problem in calls.get("problems", []):
        print(f"  problem: {problem}")

    return 1 if problems else 0
