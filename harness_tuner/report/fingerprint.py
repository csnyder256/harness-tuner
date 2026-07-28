"""Harness DNA: the summary card.

A star rating is the most shareable thing this tool produces and the easiest
place for it to start lying, so the construction here is deliberately boring
and completely exposed.

Three rules keep it honest.

1. Every score is a stated function of measured numbers. The scales are
   declared in :data:`SCALES`, they are versioned with the protocol, and the
   fingerprint artifact carries the scale used for every component alongside
   the raw value it was computed from. Nothing is judged.
2. A dimension whose inputs were all unavailable renders as insufficient
   evidence. It never falls back to a middle score, because a harness whose
   adapter cannot see caching would then score the same as one that caches
   adequately.
3. A run with too few tasks renders no scores at all. The threshold is
   ``report.min_tasks_for_fingerprint`` and the artifact records both the
   threshold and the actual count.

The scales are opinions about what good looks like. They are not measurements,
and calling them measurements is exactly the failure this design exists to
avoid. They are published so you can disagree with a specific number rather
than with a vibe.
"""

from __future__ import annotations

from typing import Any

from ..protocol.metrics import UNAVAILABLE, is_measured

HIGHER = "higher_is_better"
LOWER = "lower_is_better"

#: metric -> (best value, worst value, direction). Linear between, clamped
#: outside. Published in the artifact next to every score derived from it.
SCALES: dict[str, tuple[float, float, str]] = {
    "cache_hit_ratio": (0.90, 0.0, HIGHER),
    "context_growth_per_step": (0.0, 1500.0, LOWER),
    "rework_ratio": (0.0, 0.50, LOWER),
    "redundant_call_ratio": (0.0, 0.40, LOWER),
    "step_efficiency": (1.0, 0.25, HIGHER),
    "error_rate": (0.0, 0.35, LOWER),
    "recovery_rate": (1.0, 0.0, HIGHER),
    "autonomy_ratio": (1.0, 0.50, HIGHER),
    "task_success": (1.0, 0.0, HIGHER),
}

#: dimension -> the metrics that compose it.
DIMENSIONS: dict[str, tuple[str, ...]] = {
    "context_engineering": ("cache_hit_ratio", "context_growth_per_step", "rework_ratio"),
    "planning": ("step_efficiency", "redundant_call_ratio"),
    "tool_reliability": ("error_rate",),
    "recovery": ("recovery_rate",),
    "autonomy": ("autonomy_ratio",),
    "task_completion": ("task_success",),
}

#: Dimensions that cannot be computed from aggregate metrics at all. They need
#: a specific kind of task or a repeated trial, and they say so rather than
#: quietly disappearing from the card.
DERIVED_DIMENSIONS = ("determinism", "memory", "safety")

INSUFFICIENT = "insufficient_evidence"


def scale_score(metric: str, value: float) -> float | None:
    """Map a measured value onto 0..1 using the declared scale."""
    scale = SCALES.get(metric)
    if scale is None or value is None:
        return None
    best, worst, _direction = scale
    if best == worst:
        return None
    raw = (float(value) - worst) / (best - worst)
    return max(0.0, min(1.0, raw))


def stars(score: float) -> int:
    """Whole stars out of five. The numeric score is always published beside it."""
    return int(round(score * 5))


def _dimension(name: str, components: tuple[str, ...], agg: dict[str, dict]) -> dict[str, Any]:
    parts = []
    for metric in components:
        entry = agg.get(metric)
        if entry is None or not is_measured(entry):
            parts.append(
                {
                    "metric": metric,
                    "status": UNAVAILABLE,
                    "reason": (entry or {}).get("reason") or "not computed in this run",
                    "value": None,
                    "scale": None,
                    "score": None,
                }
            )
            continue
        best, worst, direction = SCALES[metric]
        parts.append(
            {
                "metric": metric,
                "status": "measured",
                "reason": None,
                "value": entry["value"],
                "scale": {"best": best, "worst": worst, "direction": direction},
                "score": round(scale_score(metric, entry["value"]), 6),
            }
        )

    scored = [p["score"] for p in parts if p["score"] is not None]
    if not scored:
        return {
            "status": INSUFFICIENT,
            "reason": "every component metric was unavailable in this run",
            "score": None,
            "stars": None,
            "components": parts,
        }
    score = round(sum(scored) / len(scored), 6)
    out = {
        "status": "measured",
        "reason": None,
        "score": score,
        "stars": stars(score),
        "components": parts,
    }
    if len(scored) != len(parts):
        out["reason"] = f"computed from {len(scored)} of {len(parts)} component metrics"
    return out


def _determinism(task_runs: list[dict[str, Any]]) -> dict[str, Any]:
    """Do repeated runs of the same task take the same actions?

    Only computable when the run repeated at least one task. A single pass over
    each task says nothing about determinism, and reporting a perfect score for
    it would be an outright fabrication.
    """
    by_task: dict[str, set[str]] = {}
    for run in task_runs:
        by_task.setdefault(run["task_id"], set()).add(run["trace_digest"])
    repeated = {k: v for k, v in by_task.items() if len(v) >= 1 and _count(task_runs, k) > 1}
    if not repeated:
        return {
            "status": INSUFFICIENT,
            "reason": "no task was run more than once, so determinism cannot be observed",
            "score": None,
            "stars": None,
            "components": [],
        }
    identical = sum(1 for digests in repeated.values() if len(digests) == 1)
    score = round(identical / len(repeated), 6)
    return {
        "status": "measured",
        "reason": f"over {len(repeated)} repeated task(s)",
        "score": score,
        "stars": stars(score),
        "components": [
            {
                "metric": "trace_digest_agreement",
                "status": "measured",
                "reason": None,
                "value": score,
                "scale": {"best": 1.0, "worst": 0.0, "direction": HIGHER},
                "score": score,
            }
        ],
    }


def _count(task_runs: list[dict[str, Any]], task_id: str) -> int:
    return sum(1 for r in task_runs if r["task_id"] == task_id)


def _tagged(task_runs: list[dict[str, Any]], tag: str, label: str) -> dict[str, Any]:
    """A dimension that only exists when the task set exercised it."""
    tagged = [r for r in task_runs if tag in (r.get("tags") or ())]
    if not tagged:
        return {
            "status": INSUFFICIENT,
            "reason": f"the task set contained no task tagged {tag!r}, so {label} was never exercised",
            "score": None,
            "stars": None,
            "components": [],
        }
    outcomes = [r["metrics"]["task_success"] for r in tagged]
    known = [o["value"] for o in outcomes if is_measured(o)]
    if not known:
        return {
            "status": INSUFFICIENT,
            "reason": (
                f"{len(tagged)} task(s) tagged {tag!r} ran, but none reported an outcome; "
                "supply a deterministic check or a judge verdict"
            ),
            "score": None,
            "stars": None,
            "components": [],
        }
    score = round(sum(1 for v in known if v) / len(known), 6)
    return {
        "status": "measured",
        "reason": f"over {len(known)} task(s) tagged {tag!r}",
        "score": score,
        "stars": stars(score),
        "components": [
            {
                "metric": "task_success",
                "status": "measured",
                "reason": None,
                "value": score,
                "scale": {"best": 1.0, "worst": 0.0, "direction": HIGHER},
                "score": score,
            }
        ],
    }


def build(
    agg: dict[str, dict],
    task_runs: list[dict[str, Any]],
    *,
    min_tasks: int,
) -> dict[str, Any]:
    """Assemble the fingerprint block for the artifact."""
    task_count = len(task_runs)
    header = {
        "task_count": task_count,
        "min_tasks_for_fingerprint": min_tasks,
        "scales": {k: {"best": v[0], "worst": v[1], "direction": v[2]} for k, v in SCALES.items()},
        "scales_are": (
            "declared opinions about what good looks like, not measurements. "
            "They are published so a disagreement can be about a specific number."
        ),
    }

    if task_count < min_tasks:
        header["status"] = INSUFFICIENT
        header["reason"] = (
            f"this run covered {task_count} task(s) and the configured floor is {min_tasks}. "
            "No scores are shown, because a card built on one task would be read as a "
            "property of the harness rather than of a single run."
        )
        header["dimensions"] = {
            name: {
                "status": INSUFFICIENT,
                "reason": "below the task floor",
                "score": None,
                "stars": None,
                "components": [],
            }
            for name in list(DIMENSIONS) + list(DERIVED_DIMENSIONS)
        }
        return header

    dimensions: dict[str, Any] = {
        name: _dimension(name, comps, agg) for name, comps in DIMENSIONS.items()
    }
    dimensions["determinism"] = _determinism(task_runs)
    dimensions["memory"] = _tagged(task_runs, "memory", "memory retention")
    dimensions["safety"] = _tagged(task_runs, "safety", "safety behaviour")

    header["status"] = "measured"
    header["reason"] = None
    header["dimensions"] = dimensions
    return header
