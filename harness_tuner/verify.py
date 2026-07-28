"""verify: did the change you applied actually help?

harness-tuner proposes diffs and never applies them. That leaves an obvious
hole, and this command is what fills it. Without it, every proposal would carry
an expected gain that nobody could ever check, which is the failure mode this
whole project is a reaction to.

The flow is deliberately human-in-the-middle:

1. A run produces findings and proposals. Each proposal carries a falsifiable
   prediction, written into ``manifest.json``: which metric, which direction.
2. You read the proposal, and apply the diff to your harness, or do not.
3. You run the same task set again, from the same manifest.
4. ``verify`` pairs the two runs task by task and reports what actually
   happened, against what was predicted.

Pairing matters. Comparing two run-level averages throws away the fact that the
same task was run twice, and a paired comparison extracts far more evidence
from the same number of expensive trials. Tasks present in only one of the two
runs are dropped and counted, never silently matched to something else.

The verdict comes from the e-process, so it is safe to run verify after every
few trials and stop when the answer is clear.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from .protocol import LOWER_IS_BETTER, REGISTRY, RunDir, envelope, is_measured, write_json
from .stats import eprocess as E


class VerifyError(RuntimeError):
    pass


def _load(run_root: str) -> tuple[dict[str, Any], dict[str, Any]]:
    rundir = RunDir(run_root)
    missing = rundir.missing()
    if missing:
        raise VerifyError(f"{run_root} is not a complete run; missing {missing}")
    with open(rundir.path("metrics"), "r", encoding="utf-8") as handle:
        metrics = json.load(handle)
    with open(rundir.path("manifest"), "r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    return metrics, manifest


def _by_task(metrics_doc: dict[str, Any]) -> dict[str, dict[str, dict]]:
    return {entry["task_id"]: entry["metrics"] for entry in metrics_doc["per_task"]}


def compare_metric(
    baseline: dict[str, dict[str, dict]],
    variant: dict[str, dict[str, dict]],
    metric: str,
    *,
    alpha: float,
) -> dict[str, Any]:
    """Paired comparison of one metric across the two runs."""
    shared = sorted(set(baseline) & set(variant))
    pairs = []
    for task_id in shared:
        b = baseline[task_id].get(metric)
        v = variant[task_id].get(metric)
        pairs.append(
            (
                b["value"] if b and is_measured(b) else None,
                v["value"] if v and is_measured(v) else None,
            )
        )

    lower_better = metric in LOWER_IS_BETTER
    outcomes = E.outcomes_from_pairs(pairs, lower_is_better=lower_better)
    result = E.test(outcomes, alpha=alpha)

    usable = [(b, v) for b, v in pairs if b is not None and v is not None]
    mean_baseline = sum(float(b) for b, _ in usable) / len(usable) if usable else None
    mean_variant = sum(float(v) for _, v in usable) / len(usable) if usable else None

    return {
        "metric": metric,
        "lower_is_better": lower_better,
        "tasks_shared": len(shared),
        "tasks_comparable": len(usable),
        "tasks_dropped": len(shared) - len(usable),
        "mean_baseline": round(mean_baseline, 6) if mean_baseline is not None else None,
        "mean_variant": round(mean_variant, 6) if mean_variant is not None else None,
        "delta": (
            round(mean_variant - mean_baseline, 6)
            if mean_baseline is not None and mean_variant is not None
            else None
        ),
        "evidence": result,
    }


def _judge_prediction(prediction: dict[str, Any], comparison: dict[str, Any]) -> dict[str, Any]:
    """Score one prediction against what was measured."""
    wanted = prediction.get("direction", "improve")
    verdict = comparison["evidence"]["verdict"]

    if verdict == E.UNDECIDED:
        outcome = "unproven"
        note = (
            "not enough evidence either way yet. This is not a failed prediction, it is an "
            "unfinished measurement. Add trials and run verify again."
        )
    elif (wanted == "improve" and verdict == E.IMPROVEMENT) or (
        wanted == "worsen" and verdict == E.REGRESSION
    ):
        outcome = "confirmed"
        note = "the measured direction matches what the proposal predicted."
    else:
        outcome = "refuted"
        note = (
            "the measurement went the other way with evidence clearing the bar. "
            "The proposal was wrong, and this is the result worth acting on."
        )

    return {
        "proposal_id": prediction.get("proposal_id"),
        "metric": prediction.get("metric"),
        "predicted_direction": wanted,
        "predicted_note": prediction.get("note"),
        "measured_verdict": verdict,
        "outcome": outcome,
        "note": note,
    }


def run_verify(
    baseline_root: str,
    variant_root: str,
    *,
    metrics: list[str] | None = None,
    alpha: float | None = None,
) -> dict[str, Any]:
    base_metrics, base_manifest = _load(baseline_root)
    var_metrics, _var_manifest = _load(variant_root)

    if alpha is None:
        alpha = float(base_manifest.get("alpha") or 0.05)

    base_by_task = _by_task(base_metrics)
    var_by_task = _by_task(var_metrics)

    shared = set(base_by_task) & set(var_by_task)
    if not shared:
        raise VerifyError(
            "the two runs share no task ids, so nothing can be paired. verify compares the "
            "same task set before and after a change; it cannot compare different task sets."
        )

    predictions = base_manifest.get("predictions") or []
    wanted = metrics or [p["metric"] for p in predictions if p.get("metric") in REGISTRY]
    if not wanted:
        # No proposal to check, so report the metrics most likely to move.
        wanted = [
            m
            for m in (
                "task_success",
                "cost_usd",
                "tokens_input",
                "rework_ratio",
                "redundant_call_ratio",
                "step_count",
                "error_rate",
                "cache_hit_ratio",
                "context_growth_per_step",
            )
            if m in REGISTRY
        ]

    comparisons = [
        compare_metric(base_by_task, var_by_task, metric, alpha=alpha) for metric in wanted
    ]
    by_metric = {c["metric"]: c for c in comparisons}

    checked = [
        _judge_prediction(p, by_metric[p["metric"]])
        for p in predictions
        if p.get("metric") in by_metric
    ]

    doc = envelope(base_manifest.get("run_id", "unknown"), "verify")
    doc.update(
        {
            "baseline_run": os.path.abspath(baseline_root),
            "variant_run": os.path.abspath(variant_root),
            "baseline_config_digest": base_manifest.get("config_digest"),
            "tasks_in_baseline": len(base_by_task),
            "tasks_in_variant": len(var_by_task),
            "tasks_paired": len(shared),
            "tasks_unpaired": sorted(set(base_by_task) ^ set(var_by_task)),
            "alpha": alpha,
            "comparisons": comparisons,
            "predictions_checked": checked,
        }
    )
    return doc


def _print(doc: dict[str, Any], stream=sys.stdout) -> None:
    print(f"baseline  {doc['baseline_run']}", file=stream)
    print(f"variant   {doc['variant_run']}", file=stream)
    print(
        f"paired    {doc['tasks_paired']} task(s)"
        + (f", {len(doc['tasks_unpaired'])} unpaired and dropped" if doc["tasks_unpaired"] else ""),
        file=stream,
    )
    print(f"alpha     {doc['alpha']} (evidence bar {1 / doc['alpha']:.0f} to 1)\n", file=stream)

    for comparison in doc["comparisons"]:
        ev = comparison["evidence"]
        arrow = "lower is better" if comparison["lower_is_better"] else "higher is better"
        print(f"{comparison['metric']}  ({arrow})", file=stream)
        if comparison["tasks_comparable"] == 0:
            print("  not comparable: neither run measured this in any shared task\n", file=stream)
            continue
        print(
            f"  baseline {comparison['mean_baseline']}  ->  variant {comparison['mean_variant']}"
            f"   delta {comparison['delta']}",
            file=stream,
        )
        print(
            f"  {ev['wins']} win / {ev['losses']} loss / {ev['ties']} tie "
            f"over {comparison['tasks_comparable']} paired task(s)",
            file=stream,
        )
        print(f"  {ev['verdict'].upper()}: {ev['detail']}\n", file=stream)

    if doc["predictions_checked"]:
        print("Predictions from the proposal:", file=stream)
        for check in doc["predictions_checked"]:
            print(
                f"  [{check['outcome']}] {check['proposal_id']}: "
                f"{check['metric']} predicted to {check['predicted_direction']}",
                file=stream,
            )
            print(f"      {check['note']}", file=stream)
    else:
        print(
            "No prediction was recorded in the baseline manifest, so nothing was scored "
            "against a forecast. The comparisons above still stand on their own.",
            file=stream,
        )


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--baseline", required=True, metavar="RUNDIR", help="the run before the change")
    parser.add_argument("--variant", required=True, metavar="RUNDIR", help="the run after the change")
    parser.add_argument(
        "--metric",
        action="append",
        default=None,
        dest="metrics",
        help="compare this metric (repeatable). Defaults to the proposal's predictions.",
    )
    parser.add_argument("--alpha", type=float, default=None, help="override the evidence bar")
    parser.add_argument("--out", default=None, metavar="FILE", help="also write the result as JSON")


def main(args: argparse.Namespace) -> int:
    try:
        doc = run_verify(
            args.baseline, args.variant, metrics=args.metrics, alpha=args.alpha
        )
    except VerifyError as exc:
        print(f"verify failed: {exc}", file=sys.stderr)
        return 2
    _print(doc)
    if args.out:
        write_json(args.out, doc)
        print(f"\nwritten to {args.out}")
    return 0
