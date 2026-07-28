"""Findings: what the traces show, detected deterministically.

The division of labour here is the point of the module. Detection is code.
Every finding below is produced by counting things in a trace, and every one
carries the exact steps it was derived from, so a reader can check it against
the raw records rather than taking it on trust.

The diagnostician model's job is narrower than it first appears: explain a
finding in terms of this particular harness, and say which knob in this
particular harness would address it. It is not asked whether the finding is
real. A model asked to find problems in a trace will find problems in any
trace, and none of them will come with a step number.

Severity is likewise mechanical. It is a function of how much of the run the
finding accounts for, not a judgement.
"""

from __future__ import annotations

from typing import Any

from ..protocol.metrics import is_measured

CRITICAL = "critical"
HIGH = "high"
MEDIUM = "medium"
LOW = "low"
INFO = "info"

_ORDER = {CRITICAL: 0, HIGH: 1, MEDIUM: 2, LOW: 3, INFO: 4}


def _severity_from_share(share: float) -> str:
    """Severity from the share of the run a finding accounts for."""
    if share >= 0.40:
        return CRITICAL
    if share >= 0.20:
        return HIGH
    if share >= 0.08:
        return MEDIUM
    return LOW


def _finding(**kwargs) -> dict[str, Any]:
    kwargs.setdefault("evidence", [])
    kwargs.setdefault("tasks", [])
    return kwargs


# ---------------------------------------------------------------------------
# Detectors. Each takes the per-task runs and the aggregate, and returns
# findings or nothing. None of them guesses.
# ---------------------------------------------------------------------------


def detect_loops(task_runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Consecutive identical calls: the shape that burns a budget without erroring."""
    out = []
    for run in task_runs:
        loop = run["metrics"]["loop_max_run"]
        if not is_measured(loop) or loop["value"] < 3:
            continue
        steps = run.get("steps") or []
        offenders = _consecutive_runs(steps)
        worst = max(offenders, key=lambda o: o["length"], default=None)
        if worst is None:
            continue
        share = worst["length"] / max(1, len(steps))
        out.append(
            _finding(
                id=f"loop:{run['task_id']}",
                kind="loop",
                severity=_severity_from_share(share),
                title=f"{worst['length']} identical calls in a row to {worst['tool']}",
                detail=(
                    f"In task {run['task_id']}, {worst['tool']} was called {worst['length']} "
                    f"times consecutively with byte-identical arguments, at steps "
                    f"{worst['first_step']} through {worst['last_step']}. Nothing errored and "
                    "nothing hung, so a suite that only checks task outcome sees a pass."
                ),
                metric="loop_max_run",
                direction="improve",
                tasks=[run["task_id"]],
                evidence=[
                    {
                        "task_id": run["task_id"],
                        "steps": list(range(worst["first_step"], worst["last_step"] + 1)),
                        "tool": worst["tool"],
                        "args_digest": worst["args_digest"],
                    }
                ],
                measured={"consecutive_calls": worst["length"], "share_of_task": round(share, 4)},
            )
        )
    return out


def _consecutive_runs(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for step in steps:
        if step["kind"] != "tool_call" or step["args_digest"] is None:
            current = None
            continue
        if current and current["hash"] == step["step_hash"]:
            current["length"] += 1
            current["last_step"] = step["step"]
            continue
        current = {
            "hash": step["step_hash"],
            "tool": step["tool"],
            "args_digest": step["args_digest"],
            "length": 1,
            "first_step": step["step"],
            "last_step": step["step"],
        }
        runs.append(current)
    return [r for r in runs if r["length"] >= 3]


def detect_rework(task_runs: list[dict[str, Any]], agg: dict[str, dict]) -> list[dict[str, Any]]:
    """Reading the same thing more than once across a task."""
    entry = agg.get("rework_ratio")
    if not entry or not is_measured(entry) or entry["value"] <= 0.05:
        return []

    repeats: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for run in task_runs:
        seen: dict[tuple[str, str], int] = {}
        for step in run.get("steps") or []:
            if step["kind"] != "tool_call" or not step["tool"] or step["args_digest"] is None:
                continue
            key = (step["tool"], step["args_digest"])
            if key in seen:
                repeats.setdefault(key, []).append(
                    {"task_id": run["task_id"], "step": step["step"], "first_seen": seen[key]}
                )
            else:
                seen[key] = step["step"]

    if not repeats:
        return []
    worst = sorted(repeats.items(), key=lambda kv: -len(kv[1]))[:5]
    total_repeats = sum(len(v) for v in repeats.values())
    return [
        _finding(
            id="rework:reads",
            kind="rework",
            severity=_severity_from_share(entry["value"]),
            title=f"{entry['value']:.0%} of read-shaped calls re-read something already read",
            detail=(
                f"{total_repeats} repeated read(s) across {len({e['task_id'] for v in repeats.values() for e in v})} "
                "task(s). The agent had already seen this content earlier in the same task, so "
                "the tokens spent re-reading it bought nothing. This is usually the largest "
                "single recoverable line in a coding harness."
            ),
            metric="rework_ratio",
            direction="improve",
            tasks=sorted({e["task_id"] for v in repeats.values() for e in v}),
            evidence=[
                {
                    "tool": tool,
                    "args_digest": digest_value,
                    "repeated_times": len(entries),
                    "occurrences": entries[:6],
                }
                for (tool, digest_value), entries in worst
            ],
            measured={"rework_ratio": entry["value"], "repeated_reads": total_repeats},
        )
    ]


def detect_cold_cache(agg: dict[str, dict]) -> list[dict[str, Any]]:
    """Cache reported as present and empty, with real input tokens being spent."""
    ratio = agg.get("cache_hit_ratio")
    inputs = agg.get("tokens_input")
    if not ratio or not is_measured(ratio) or not inputs or not is_measured(inputs):
        return []
    if ratio["value"] > 0.10 or inputs["value"] < 1000:
        return []
    return [
        _finding(
            id="cache:cold",
            kind="cache",
            severity=HIGH if ratio["value"] == 0.0 else MEDIUM,
            title=f"cache hit ratio is {ratio['value']:.1%} across {inputs['value']:,} input tokens",
            detail=(
                "The adapter reports caching explicitly, and it reports almost none happening. "
                "Every turn is paying full price for whatever preamble the harness re-sends. "
                "This is a measured property of the harness, not an inference: an adapter that "
                "could not see caching would have reported this metric as unavailable instead."
            ),
            metric="cache_hit_ratio",
            direction="improve",
            measured={"cache_hit_ratio": ratio["value"], "tokens_input": inputs["value"]},
        )
    ]


def detect_context_growth(agg: dict[str, dict]) -> list[dict[str, Any]]:
    """Context that only ever grows: a harness that appends and never compacts."""
    slope = agg.get("context_growth_per_step")
    peak = agg.get("context_peak")
    if not slope or not is_measured(slope) or slope["value"] < 250:
        return []
    detail = (
        f"Context grew by about {slope['value']:,.0f} tokens per step. A harness that compacts "
        "holds this near zero; one that appends forever grows linearly until it hits a limit "
        "or a bill."
    )
    if peak and is_measured(peak):
        detail += f" The largest context observed was {peak['value']:,} tokens."
    return [
        _finding(
            id="context:unbounded",
            kind="context",
            severity=HIGH if slope["value"] >= 800 else MEDIUM,
            title=f"context grows about {slope['value']:,.0f} tokens per step",
            detail=detail,
            metric="context_growth_per_step",
            direction="improve",
            measured={"slope": slope["value"], "peak": peak["value"] if peak and is_measured(peak) else None},
        )
    ]


def detect_unrecovered_errors(agg: dict[str, dict], task_runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Failures the harness never retried."""
    recovery = agg.get("recovery_rate")
    errors = agg.get("error_count")
    if not recovery or not is_measured(recovery) or recovery["value"] >= 0.75:
        return []
    if not errors or not is_measured(errors) or errors["value"] == 0:
        return []
    evidence = []
    for run in task_runs:
        for step in run.get("steps") or []:
            if step["result_kind"] == "error":
                evidence.append(
                    {"task_id": run["task_id"], "step": step["step"], "tool": step["tool"]}
                )
    return [
        _finding(
            id="recovery:weak",
            kind="recovery",
            severity=HIGH if recovery["value"] <= 0.25 else MEDIUM,
            title=f"only {recovery['value']:.0%} of failures were retried on the same tool",
            detail=(
                f"{errors['value']} step(s) failed. In most of them the harness moved on to "
                "something else rather than retrying, which means the work of discovering the "
                "problem was paid for and then discarded."
            ),
            metric="recovery_rate",
            direction="improve",
            tasks=sorted({e["task_id"] for e in evidence}),
            evidence=evidence[:10],
            measured={"recovery_rate": recovery["value"], "errors": errors["value"]},
        )
    ]


def detect_measurement_gaps(agg: dict[str, dict]) -> list[dict[str, Any]]:
    """What the adapter cannot see. A finding about the setup, not the harness."""
    gaps = [(name, entry) for name, entry in agg.items() if not is_measured(entry)]
    if not gaps:
        return []
    return [
        _finding(
            id="adapter:gaps",
            kind="measurement",
            severity=INFO,
            title=f"{len(gaps)} metric(s) could not be measured through this adapter",
            detail=(
                "These are gaps in what harness-tuner can see, not defects in the harness. "
                "They are reported so that no reader mistakes silence for a clean result. "
                "Raising the adapter level closes most of them."
            ),
            metric=None,
            direction=None,
            evidence=[{"metric": name, "reason": entry["reason"]} for name, entry in gaps],
            measured={"unavailable_metrics": len(gaps), "total_metrics": len(agg)},
        )
    ]


def detect_budget_blind_spots(run_doc: dict[str, Any]) -> list[dict[str, Any]]:
    """A configured ceiling that cannot fire is a promise the tool cannot keep."""
    budget = (run_doc or {}).get("budget") or {}
    blind = budget.get("blind_spots") or []
    if not blind:
        return []
    return [
        _finding(
            id="budget:blind",
            kind="budget",
            severity=HIGH,
            title="a configured spend ceiling cannot fire",
            detail=(
                "The budget governor is enabled, but the data it needs to enforce a ceiling is "
                "not being reported. An operator relying on this ceiling is relying on nothing."
            ),
            metric=None,
            direction=None,
            evidence=[{"problem": text} for text in blind],
            measured={"blind_spots": len(blind)},
        )
    ]


def analyze(
    agg: dict[str, dict],
    task_runs: list[dict[str, Any]],
    run_doc: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Run every detector and rank the results."""
    findings: list[dict[str, Any]] = []
    findings += detect_loops(task_runs)
    findings += detect_rework(task_runs, agg)
    findings += detect_cold_cache(agg)
    findings += detect_context_growth(agg)
    findings += detect_unrecovered_errors(agg, task_runs)
    findings += detect_budget_blind_spots(run_doc or {})
    findings += detect_measurement_gaps(agg)

    findings.sort(key=lambda f: (_ORDER[f["severity"]], f["id"]))
    return findings
