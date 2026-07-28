"""HTP-1 metric definitions.

This module is normative. Every number any HTP-1 report shows is computed here,
from a normalized trace and nothing else. An independent implementation is
conformant when it reproduces these values exactly for the shipped fixtures.

Two rules govern every function below.

1. A metric whose inputs the adapter did not supply returns ``unavailable``
   with a reason a human can act on. It never returns zero, and it never
   substitutes a plausible-looking estimate.
2. A metric that is undefined for this trace, rather than merely unobserved,
   also returns ``unavailable`` and says which. A recovery rate over zero
   errors is undefined, not perfect.
"""

from __future__ import annotations

from typing import Any, Callable, Iterable

from .trace import DEFAULT_READ_TOOL_HINTS

MEASURED = "measured"
UNAVAILABLE = "unavailable"


def measured(value: Any, unit: str = "count") -> dict[str, Any]:
    return {"value": value, "status": MEASURED, "reason": None, "unit": unit}


def unavailable(reason: str, unit: str = "count") -> dict[str, Any]:
    return {"value": None, "status": UNAVAILABLE, "reason": reason, "unit": unit}


def is_measured(measure: dict[str, Any]) -> bool:
    return measure.get("status") == MEASURED and measure.get("value") is not None


def _tool_calls(steps: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [s for s in steps if s["kind"] == "tool_call"]


def _token_series(steps: Iterable[dict[str, Any]], kind: str) -> list[int]:
    return [s["tokens"][kind] for s in steps if s["tokens"].get(kind) is not None]


def _read_hints(task: dict[str, Any]) -> tuple[str, ...]:
    hints = task.get("read_tool_hints")
    if not hints:
        return DEFAULT_READ_TOOL_HINTS
    return tuple(str(h).lower() for h in hints)


# --------------------------------------------------------------------------
# Shape of the run
# --------------------------------------------------------------------------


def step_count(steps, task):
    return measured(len(steps), "steps")


def tool_call_count(steps, task):
    return measured(len(_tool_calls(steps)), "calls")


def distinct_tools(steps, task):
    names = {s["tool"] for s in _tool_calls(steps) if s["tool"]}
    return measured(len(names), "tools")


def human_input_steps(steps, task):
    return measured(sum(1 for s in steps if s["kind"] == "human_input"), "steps")


def autonomy_ratio(steps, task):
    """Share of steps the harness took without asking a human for input."""
    if not steps:
        return unavailable("the trace has no steps", "ratio")
    asked = sum(1 for s in steps if s["kind"] == "human_input")
    return measured(round(1.0 - asked / len(steps), 6), "ratio")


# --------------------------------------------------------------------------
# Waste
# --------------------------------------------------------------------------


def redundant_call_count(steps, task):
    """Tool calls that repeat an action already taken in this task.

    Identity is (tool, args_digest). A call whose adapter supplied no arguments
    and no digest cannot be compared, so it is excluded from both the numerator
    and the denominator rather than assumed distinct.
    """
    calls = [s for s in _tool_calls(steps) if s["args_digest"] is not None]
    if not calls:
        if _tool_calls(steps):
            return unavailable(
                "no tool call carried args or an args_digest, so repeats cannot be identified",
                "calls",
            )
        return unavailable("the trace contains no tool calls", "calls")
    seen: set[tuple[str | None, str]] = set()
    repeats = 0
    for call in calls:
        key = (call["tool"], call["args_digest"])
        if key in seen:
            repeats += 1
        seen.add(key)
    return measured(repeats, "calls")


def redundant_call_ratio(steps, task):
    count = redundant_call_count(steps, task)
    if not is_measured(count):
        return unavailable(count["reason"], "ratio")
    comparable = [s for s in _tool_calls(steps) if s["args_digest"] is not None]
    return measured(round(count["value"] / len(comparable), 6), "ratio")


def loop_max_run(steps, task):
    """Longest unbroken run of the identical action.

    A high value is the signature of an agent spinning rather than one that
    revisited a file legitimately between other work.
    """
    calls = [s for s in _tool_calls(steps) if s["args_digest"] is not None]
    if not calls:
        return unavailable("no comparable tool calls in the trace", "calls")
    best = 1
    current = 1
    for prev, curr in zip(calls, calls[1:]):
        if prev["step_hash"] == curr["step_hash"]:
            current += 1
            best = max(best, current)
        else:
            current = 1
    return measured(best, "calls")


def rework_ratio(steps, task):
    """Share of read-shaped calls that re-read something already read.

    This is the metric that catches a harness with no working memory of what it
    has already looked at, which is usually the single largest recoverable
    token line in a coding harness.
    """
    hints = _read_hints(task)
    named = [
        s for s in _tool_calls(steps) if s["tool"] and any(h in s["tool"].lower() for h in hints)
    ]
    reads = [s for s in named if s["args_digest"] is not None]
    if not reads:
        if named:
            return unavailable(
                f"{len(named)} read-shaped call(s) carried no args or args_digest, "
                "so re-reads cannot be identified",
                "ratio",
            )
        return unavailable(
            "no read-shaped tool call was identified; set read_tool_hints for this harness",
            "ratio",
        )
    seen: set[tuple[str | None, str]] = set()
    repeats = 0
    for call in reads:
        key = (call["tool"], call["args_digest"])
        if key in seen:
            repeats += 1
        seen.add(key)
    return measured(round(repeats / len(reads), 6), "ratio")


# --------------------------------------------------------------------------
# Cost and context
# --------------------------------------------------------------------------


def _token_total(steps, kind, label):
    series = _token_series(steps, kind)
    if not series:
        return unavailable(
            f"no step reported {label} tokens; this adapter is below level L1",
            "tokens",
        )
    return measured(sum(series), "tokens")


def tokens_input(steps, task):
    return _token_total(steps, "input", "input")


def tokens_output(steps, task):
    return _token_total(steps, "output", "output")


def tokens_cache_read(steps, task):
    return _token_total(steps, "cache_read", "cache read")


def tokens_cache_write(steps, task):
    return _token_total(steps, "cache_write", "cache write")


def cache_hit_ratio(steps, task):
    """Cached input tokens as a share of all input tokens.

    Low values on a harness that re-sends a stable preamble every turn are the
    cheapest large saving available, which is why this is reported separately
    from raw cost.
    """
    cached = _token_series(steps, "cache_read")
    fresh = _token_series(steps, "input")
    if not cached and not fresh:
        return unavailable("no step reported input or cache_read tokens", "ratio")
    if not cached:
        return unavailable(
            "input tokens were reported but cache_read was not, so the cache share is unknown",
            "ratio",
        )
    total = sum(cached) + sum(fresh)
    if total == 0:
        return unavailable("input and cache_read token counts are both zero", "ratio")
    return measured(round(sum(cached) / total, 6), "ratio")


def cost_usd(steps, task):
    values = [s["cost_usd"] for s in steps if s["cost_usd"] is not None]
    if not values:
        return unavailable(
            "no step reported a cost; supply per-step cost or a price table for the model",
            "usd",
        )
    return measured(round(sum(values), 8), "usd")


def context_peak(steps, task):
    values = [s["context_tokens"] for s in steps if s["context_tokens"] is not None]
    if not values:
        return unavailable("no step reported context_tokens", "tokens")
    return measured(max(values), "tokens")


def context_growth_per_step(steps, task):
    """Least-squares slope of context size against step index.

    A harness that compacts holds this near zero. A harness that appends
    forever grows linearly, and the slope is what a proposed compaction change
    is predicted to move.
    """
    points = [
        (float(s["step"]), float(s["context_tokens"]))
        for s in steps
        if s["context_tokens"] is not None
    ]
    if len(points) < 3:
        return unavailable(
            f"a slope needs at least 3 steps reporting context_tokens, found {len(points)}",
            "tokens/step",
        )
    n = float(len(points))
    mean_x = sum(x for x, _ in points) / n
    mean_y = sum(y for _, y in points) / n
    denom = sum((x - mean_x) ** 2 for x, _ in points)
    if denom == 0:
        return unavailable("every reported step index is identical, so a slope is undefined", "tokens/step")
    num = sum((x - mean_x) * (y - mean_y) for x, y in points)
    return measured(round(num / denom, 6), "tokens/step")


# --------------------------------------------------------------------------
# Time
# --------------------------------------------------------------------------


def wall_ms(steps, task):
    ends = [
        (s["started_ms"] or 0) + s["duration_ms"]
        for s in steps
        if s["duration_ms"] is not None
    ]
    if not ends:
        return unavailable("no step reported duration_ms", "ms")
    return measured(max(ends), "ms")


def time_to_first_action_ms(steps, task):
    for step in steps:
        if step["kind"] in ("tool_call", "model_call"):
            if step["started_ms"] is None:
                return unavailable("the first action did not report started_ms", "ms")
            return measured(step["started_ms"], "ms")
    return unavailable("the trace contains no tool_call or model_call step", "ms")


# --------------------------------------------------------------------------
# Failure and recovery
# --------------------------------------------------------------------------


def error_count(steps, task):
    known = [s for s in steps if s["result_kind"] != "unknown"]
    if not known:
        return unavailable(
            "no step reported a result_kind; this adapter cannot distinguish success from failure",
            "steps",
        )
    return measured(sum(1 for s in known if s["result_kind"] == "error"), "steps")


def error_rate(steps, task):
    known = [s for s in steps if s["result_kind"] != "unknown"]
    if not known:
        return unavailable("no step reported a result_kind", "ratio")
    errors = sum(1 for s in known if s["result_kind"] == "error")
    return measured(round(errors / len(known), 6), "ratio")


#: How many steps after an error still count as recovering from it.
RECOVERY_WINDOW = 3


def recovery_rate(steps, task):
    """Share of errors followed by a successful retry of the same tool.

    Undefined when nothing failed. Reporting 1.0 for a trace with no errors
    would let a harness that never got far enough to fail outscore one that
    failed and recovered.
    """
    known = [s for s in steps if s["result_kind"] != "unknown"]
    if not known:
        return unavailable("no step reported a result_kind", "ratio")
    errors = [s for s in known if s["result_kind"] == "error"]
    if not errors:
        return unavailable("no error occurred, so a recovery rate is undefined", "ratio")
    by_step = {s["step"]: s for s in steps}
    recovered = 0
    for err in errors:
        for offset in range(1, RECOVERY_WINDOW + 1):
            nxt = by_step.get(err["step"] + offset)
            if nxt and nxt["tool"] == err["tool"] and nxt["result_kind"] == "ok":
                recovered += 1
                break
    return measured(round(recovered / len(errors), 6), "ratio")


# --------------------------------------------------------------------------
# Task outcome
# --------------------------------------------------------------------------


def step_efficiency(steps, task):
    """Optimal steps over actual steps. One is optimal, below one is waste."""
    optimal = task.get("optimal_steps")
    if optimal is None:
        return unavailable(
            "this task declares no optimal_steps, so efficiency has no reference point",
            "ratio",
        )
    calls = len(_tool_calls(steps))
    if calls == 0:
        return unavailable("the trace contains no tool calls", "ratio")
    return measured(round(float(optimal) / calls, 6), "ratio")


def task_success(steps, task):
    """Whether the task was accomplished.

    Supplied by a deterministic check declared in the pack, or by a judge
    verdict the user's agent handed back. Never inferred from the trace, since
    an agent that stops early leaves a trace that looks exactly like success.
    """
    outcome = task.get("success")
    if outcome is None:
        return unavailable(
            "no deterministic check and no judge verdict was supplied for this task",
            "bool",
        )
    return measured(bool(outcome), "bool")


#: The normative registry. Order is stable so artifacts are byte-reproducible.
REGISTRY: dict[str, Callable[[list, dict], dict]] = {
    "step_count": step_count,
    "tool_call_count": tool_call_count,
    "distinct_tools": distinct_tools,
    "human_input_steps": human_input_steps,
    "autonomy_ratio": autonomy_ratio,
    "redundant_call_count": redundant_call_count,
    "redundant_call_ratio": redundant_call_ratio,
    "loop_max_run": loop_max_run,
    "rework_ratio": rework_ratio,
    "tokens_input": tokens_input,
    "tokens_output": tokens_output,
    "tokens_cache_read": tokens_cache_read,
    "tokens_cache_write": tokens_cache_write,
    "cache_hit_ratio": cache_hit_ratio,
    "cost_usd": cost_usd,
    "context_peak": context_peak,
    "context_growth_per_step": context_growth_per_step,
    "wall_ms": wall_ms,
    "time_to_first_action_ms": time_to_first_action_ms,
    "error_count": error_count,
    "error_rate": error_rate,
    "recovery_rate": recovery_rate,
    "step_efficiency": step_efficiency,
    "task_success": task_success,
}

#: How each metric combines across the tasks in a run.
#:
#: This is explicit rather than inferred from the unit, because inferring it is
#: how a maximum ends up being summed. Adding a per-task longest-loop-run to
#: another task's longest-loop-run produces a number that describes no run that
#: ever happened, and it looks entirely plausible in a table.
#:
#: ``sum``  totals across tasks (counts, tokens, cost, elapsed time)
#: ``max``  the largest any single task reached (peaks and longest runs)
#: ``mean`` the average across the tasks that measured it (ratios, slopes)
#: ``rate`` share of tasks where the boolean was true
AGGREGATION: dict[str, str] = {
    "step_count": "sum",
    "tool_call_count": "sum",
    "distinct_tools": "mean",
    "human_input_steps": "sum",
    "autonomy_ratio": "mean",
    "redundant_call_count": "sum",
    "redundant_call_ratio": "mean",
    "loop_max_run": "max",
    "rework_ratio": "mean",
    "tokens_input": "sum",
    "tokens_output": "sum",
    "tokens_cache_read": "sum",
    "tokens_cache_write": "sum",
    "cache_hit_ratio": "mean",
    "cost_usd": "sum",
    "context_peak": "max",
    "context_growth_per_step": "mean",
    "wall_ms": "sum",
    "time_to_first_action_ms": "mean",
    "error_count": "sum",
    "error_rate": "mean",
    "recovery_rate": "mean",
    "step_efficiency": "mean",
    "task_success": "rate",
}

#: Metrics where a smaller number is the better outcome. Used by the report and
#: by verify to decide which direction counts as an improvement.
LOWER_IS_BETTER = frozenset(
    {
        "redundant_call_count",
        "redundant_call_ratio",
        "loop_max_run",
        "rework_ratio",
        "tokens_input",
        "tokens_output",
        "cost_usd",
        "context_peak",
        "context_growth_per_step",
        "wall_ms",
        "time_to_first_action_ms",
        "error_count",
        "error_rate",
        "human_input_steps",
        "step_count",
        "tool_call_count",
    }
)


def compute(steps: list[dict[str, Any]], task: dict[str, Any] | None = None) -> dict[str, dict]:
    """Compute every registered metric for one task's trace."""
    task = task or {}
    return {name: fn(steps, task) for name, fn in REGISTRY.items()}


def aggregate(per_task: list[dict[str, dict]]) -> dict[str, dict]:
    """Combine per-task metrics into run-level metrics.

    Counts sum, ratios and slopes average over the tasks where they were
    measured, and booleans become a success rate. A metric no task could
    measure stays unavailable and carries the reasons, deduplicated, so the
    report explains the gap once rather than per task.
    """
    if not per_task:
        return {name: unavailable("the run contains no tasks") for name in REGISTRY}

    out: dict[str, dict] = {}
    for name in REGISTRY:
        entries = [task_metrics[name] for task_metrics in per_task if name in task_metrics]
        good = [e for e in entries if is_measured(e)]
        combiner = AGGREGATION[name]
        if not good:
            reasons = sorted({e.get("reason") for e in entries if e.get("reason")})
            unit = entries[0]["unit"] if entries else "count"
            joined = "; ".join(reasons) if reasons else "not measured in any task"
            entry = unavailable(f"unavailable in all {len(entries)} task(s): {joined}", unit)
            entry["coverage"] = {"measured": 0, "total": len(entries), "combiner": combiner}
            out[name] = entry
            continue

        unit = good[0]["unit"]
        values = [e["value"] for e in good]
        if combiner == "rate":
            value = round(sum(1 for v in values if v) / len(values), 6)
            unit = "ratio"
        elif combiner == "mean":
            value = round(sum(values) / len(values), 6)
        elif combiner == "max":
            value = max(values)
        elif combiner == "sum":
            value = sum(values)
            if isinstance(value, float):
                value = round(value, 8)
        else:  # pragma: no cover - AGGREGATION is exhaustive and tested
            raise ValueError(f"{name}: unknown combiner {combiner!r}")

        entry = measured(value, unit)
        entry["coverage"] = {
            "measured": len(good),
            "total": len(entries),
            "combiner": combiner,
        }
        if len(good) != len(entries):
            # Name the combiner. "averaged over 3 of 10" on a summed metric
            # tells the reader the wrong thing about what the number is.
            verb = {"sum": "summed", "max": "max", "mean": "mean", "rate": "rate"}[combiner]
            entry["reason"] = (
                f"{verb} over the {len(good)} of {len(entries)} task(s) that measured it; "
                f"the other {len(entries) - len(good)} contributed nothing"
            )
        out[name] = entry
    return out


def partial_coverage(agg: dict[str, dict]) -> list[tuple[str, dict]]:
    """Metrics that were measured, but not in every task.

    These are the entries most likely to be misread. A cost totalled over three
    of ten tasks is not the run's cost, and it sits in a table looking exactly
    like one that is.
    """
    out = []
    for name, entry in agg.items():
        cov = entry.get("coverage")
        if not cov or not is_measured(entry):
            continue
        if cov["measured"] < cov["total"]:
            out.append((name, entry))
    return out
