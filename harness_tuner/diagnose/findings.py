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


def _unavailable_split(
    agg: dict[str, dict],
) -> tuple[list[tuple[str, dict[str, Any]]], list[tuple[str, dict[str, Any]]]]:
    """Split the unavailable metrics into (blind, undefined).

    Two different things make a metric unavailable and they must not be reported
    as one, because they have different remedies and only one of them has a
    remedy at all.

    * **Blind.** The adapter did not supply a field the metric needs. Raising the
      adapter level closes it. These are the real gaps.
    * **Undefined.** Every input was supplied and the metric still has no value,
      because the question does not apply to this run. The clear case is a
      recovery rate over zero errors: nothing failed, so there is nothing to
      rate. No adapter change reaches it, and telling the operator to go and
      supply data it already supplied is advice that cannot be followed.

      The classification is deliberately narrow; :func:`_undefined_for_this_run`
      holds the rule. A metric is moved here only when the run itself makes it
      inapplicable, never when the operator could still declare an input that
      would define it.

    Lumping the second into the first is not cosmetic. It inflates the count of
    "things you could fix by raising the level", and the remedy text is attached
    to both, so a harness with a perfect adapter is handed a work item for a
    metric that is undefined by construction. The metric's own definition says
    so: reporting 1.0 for a recovery rate over no errors would let a harness that
    never got far enough to fail outscore one that failed and recovered.
    """
    blind: list[tuple[str, dict[str, Any]]] = []
    undefined: list[tuple[str, dict[str, Any]]] = []
    for name, entry in agg.items():
        if is_measured(entry):
            continue
        if _undefined_for_this_run(name, entry):
            undefined.append((name, entry))
        else:
            blind.append((name, entry))
    return blind, undefined


#: Reasons that mean a metric is undefined for this run rather than unobserved.
#:
#: Matched against the metric's own stated reason, because the reason is the
#: contract: these strings are produced by :mod:`..protocol.metrics` and carry
#: into every artifact. A metric reporting one of these is telling the reader
#: that its inputs were present and the question does not apply. Nothing here is
#: guessed from a metric name, so a definition change in metrics.py that alters
#: the wording shows up as a classification change rather than silently holding
#: a stale opinion.
_UNDEFINED_REASON_MARKERS = ("is undefined",)


def _undefined_for_this_run(name: str, entry: dict[str, Any]) -> bool:
    """True when no adapter change could give this metric a value.

    The test is deliberately narrow, and narrowed twice.

    It requires the metric's own reason to say the value is undefined rather
    than unobserved, and it requires the metric to be one whose unavailability
    is a property of the run rather than of the task set.

    ``recovery_rate`` is the unambiguous case: it is the only registered metric
    that stays unavailable on a fully-capable adapter with a fully-declared
    task, because its denominator is an event count and the run contained no
    events. ``step_efficiency`` and ``task_success`` also go unavailable when
    the task declares nothing, but the operator can fix those by declaring
    ``optimal_steps`` or supplying a verdict, so they stay classified as gaps
    and their remedy stays reachable.

    Returns False for anything else, which is the safe direction: a metric
    wrongly left in the gap list still gets the adapter advice, while a metric
    wrongly moved to undefined would lose the advice that could actually fix it.
    """
    if name not in _RUN_DEFINED_METRICS:
        return False
    reason = entry.get("reason") or ""
    return any(marker in reason for marker in _UNDEFINED_REASON_MARKERS)


#: Metrics whose unavailable status is a fact about the run, not the adapter.
_RUN_DEFINED_METRICS = frozenset({"recovery_rate"})


def detect_measurement_gaps(agg: dict[str, dict]) -> list[dict[str, Any]]:
    """What the adapter cannot see. A finding about the setup, not the harness."""
    blind, undefined = _unavailable_split(agg)
    if not blind:
        return []
    return [
        _finding(
            id="adapter:gaps",
            kind="measurement",
            severity=INFO,
            title=f"{len(blind)} metric(s) could not be measured through this adapter",
            detail=(
                "These are gaps in what harness-tuner can see, not defects in the harness. "
                "They are reported so that no reader mistakes silence for a clean result. "
                "Raising the adapter level closes most of them."
            ),
            metric=None,
            direction=None,
            evidence=[{"metric": name, "reason": entry["reason"]} for name, entry in blind],
            measured={
                "unavailable_metrics": len(blind),
                "total_metrics": len(agg),
                "undefined_metrics": len(undefined),
            },
        )
    ]


def detect_undefined_measurements(agg: dict[str, dict]) -> list[dict[str, Any]]:
    """Metrics the run could not define, as distinct from what the adapter missed.

    Kept separate from :func:`detect_measurement_gaps` so the two carry different
    remedies. A gap is a work item; this is not. Mixing them told an operator
    whose adapter was already complete to go and raise its level for a metric
    that no level can produce.
    """
    blind, undefined = _unavailable_split(agg)
    if not undefined:
        return []
    return [
        _finding(
            id="measurement:undefined",
            kind="undefined_measurement",
            severity=INFO,
            title=f"{len(undefined)} metric(s) are undefined for this run, not unobserved",
            detail=(
                "Every field these metrics need was supplied, and they still have no value, "
                "because what they measure did not happen: no error occurred, so there is no "
                "recovery to rate. This is the definition working. No adapter change will move "
                "them, and they are counted separately so the number of metrics an adapter "
                "could actually fix stays honest."
            ),
            metric=None,
            direction=None,
            evidence=[{"metric": name, "reason": entry["reason"]} for name, entry in undefined],
            measured={
                "undefined_metrics": len(undefined),
                "total_metrics": len(agg),
                "unavailable_metrics": len(blind),
            },
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
    findings += detect_undefined_measurements(agg)

    findings.sort(key=lambda f: (_ORDER[f["severity"]], f["id"]))
    return findings
