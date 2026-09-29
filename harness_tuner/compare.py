"""Produce a portable before/after report from two complete HTP runs."""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import io
import json
import math
import sys
from pathlib import Path

from .protocol import REGISTRY, is_measured
from .report.interactive import explorer
from .report.render import _CSS
from .verify import VerifyError, _load, compare_metric, _judge_prediction


def _validated(metrics, manifest, root):
    if metrics.get("htp_version") != manifest.get("htp_version"):
        raise VerifyError("metrics and manifest protocol versions disagree")
    if metrics.get("run_id") != manifest.get("run_id"):
        raise VerifyError("metrics and manifest run IDs disagree")
    result = {}
    for item in metrics["per_task"]:
        task_id = item["task_id"]
        if not isinstance(task_id, str) or task_id in result:
            raise VerifyError("task IDs must be unique strings; duplicate pairing would invent evidence")
        result[task_id] = item["metrics"]
        for entry in item["metrics"].values():
            if is_measured(entry) and (not isinstance(entry["value"], (int, float)) or not math.isfinite(entry["value"])):
                raise VerifyError("measured metrics must contain finite numeric values")
    hashes = {name: hashlib.sha256((Path(root) / (name + ".json")).read_bytes()).hexdigest() for name in ("metrics", "manifest")}
    return result, hashes


def build_comparison(baseline: str, variant: str, *, alpha: float | None = None) -> dict:
    bm, bmanifest = _load(baseline)
    vm, vmanifest = _load(variant)
    base, bhashes = _validated(bm, bmanifest, baseline)
    var, vhashes = _validated(vm, vmanifest, variant)
    if bm.get("htp_version") != vm.get("htp_version"):
        raise VerifyError("runs use different protocol versions")
    shared = sorted(set(base) & set(var))
    if not shared:
        raise VerifyError("the two runs share no task ids")
    alpha = float(bmanifest.get("alpha", 0.05) if alpha is None else alpha)
    if not math.isfinite(alpha) or not 0 < alpha < 1:
        raise VerifyError("alpha must be a finite number between zero and one")
    warnings = []
    unpaired = sorted(set(base) ^ set(var))
    if unpaired:
        warnings.append(f"{len(unpaired)} unpaired task IDs were excluded. Results describe only the paired population.")
    if bmanifest.get("seed") != vmanifest.get("seed"):
        warnings.append("Run seeds differ; random inputs may confound the comparison.")
    warnings.append("A paired change is not a causal claim. Check task definitions, conditions and interventions; task ID equality alone cannot establish equivalent tasks.")
    rows, pairs = [], []
    for metric in sorted(REGISTRY):
        eligible_base, eligible_var, mismatched = {}, {}, []
        # Aggregate success is a ratio, while each task records a boolean.
        # Pair using measured task units; never combine different task units.
        base_units = {base[t][metric].get("unit") for t in shared
                      if metric in base[t] and is_measured(base[t][metric])}
        declared_unit = next(iter(base_units)) if len(base_units) == 1 else None
        for task_id in shared:
            b, v = base[task_id].get(metric), var[task_id].get(metric)
            if b and v and is_measured(b) and is_measured(v) and (b.get("unit") != v.get("unit") or (len(base_units) != 1 or b.get("unit") != declared_unit)):
                mismatched.append(task_id)
                continue
            eligible_base[task_id] = base[task_id]
            eligible_var[task_id] = var[task_id]
            bv = b["value"] if b and is_measured(b) else None
            vv = v["value"] if v and is_measured(v) else None
            pairs.append({"task_id": task_id, "metric": metric, "baseline": bv, "variant": vv,
                          "delta": vv-bv if bv is not None and vv is not None else None})
        row = compare_metric(eligible_base, eligible_var, metric, alpha=alpha)
        row["tasks_shared"] = len(shared)
        row["tasks_dropped"] += len(mismatched)
        row["unit_mismatches"] = mismatched
        row["unit"] = declared_unit or ""
        bvalue, vvalue = row["mean_baseline"], row["mean_variant"]
        row["percent_change"] = 100 * (vvalue-bvalue) / abs(bvalue) if bvalue not in (None, 0) and vvalue is not None else None
        row["verdict"] = row["evidence"]["verdict"]
        row["coverage"] = f'{row["tasks_comparable"]}/{len(shared)}'
        row["reason"] = ("Unit mismatch: " + ", ".join(mismatched)) if mismatched else ("No comparable measured pairs" if row["tasks_comparable"] == 0 else "")
        rows.append(row)
    by_metric = {row["metric"]: row for row in rows}
    predictions = [_judge_prediction(prediction, by_metric[prediction["metric"]]) for prediction in bmanifest.get("predictions", []) if prediction.get("metric") in by_metric]
    return {
        "schema": "harness-tuner.comparison", "version": 1, "htp_version": bm["htp_version"],
        "baseline": {"run_id": bm["run_id"], "artifact_sha256": bhashes, "config_digest": bmanifest.get("config_digest")},
        "variant": {"run_id": vm["run_id"], "artifact_sha256": vhashes, "config_digest": vmanifest.get("config_digest")},
        "alpha": alpha, "tasks_paired": len(shared), "tasks_unpaired": unpaired, "warnings": warnings,
        "rows": rows, "pairs": pairs, "predictions_checked": predictions,
        "columns": ["metric", "mean_baseline", "mean_variant", "delta", "percent_change", "unit", "coverage", "verdict", "reason"],
    }


def comparison_html(doc: dict) -> str:
    e = html.escape
    warnings = "".join(f"<li>{e(w)}</li>" for w in doc["warnings"])
    predictions = "".join(f'<li>{e(str(p["proposal_id"]))}: {e(p["metric"])} - {e(p["outcome"])}. {e(p["note"])}</li>' for p in doc["predictions_checked"])
    pairs = "".join(f'<tr><td>{e(p["task_id"])}</td><td>{e(p["metric"])}</td><td>{p["baseline"] if p["baseline"] is not None else "unavailable"}</td><td>{p["variant"] if p["variant"] is not None else "unavailable"}</td><td>{p["delta"] if p["delta"] is not None else "unavailable"}</td></tr>' for p in doc["pairs"])
    provenance = e(json.dumps({"baseline": doc["baseline"], "variant": doc["variant"]}, indent=2))
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>Harness before / after comparison</title><style>{_CSS} pre{{overflow:auto}} .hero{{padding:2rem;background:var(--card);border-radius:12px}}</style></head><body><main>'
        '<header class="hero"><p class="meta">HARNESS TUNER / PAIRED EVIDENCE</p><h1>Did the change help?</h1>'
        f'<p>Before <code>{e(doc["baseline"]["run_id"])}</code> → after <code>{e(doc["variant"]["run_id"])}</code></p>'
        f'<p>{doc["tasks_paired"]} paired task(s) · alpha {doc["alpha"]} · HTP-{e(str(doc["htp_version"]))}</p></header>'
        f'<h2>Read the limits first</h2><ul>{warnings}</ul><p>Means below are per-task paired means, not run totals. Percentage change is unavailable when the baseline is zero. Directions and e-process verdicts use the existing verify engine.</p>'
        + explorer(doc) +
        f'<h2>Predictions</h2><ul>{predictions or "<li>No baseline prediction was recorded.</li>"}</ul>'
        '<details><summary>Inspect the paired task measurements</summary><div class="wrap"><table><thead><tr><th>Task</th><th>Metric</th><th>Before</th><th>After</th><th>Delta</th></tr></thead>'
        f'<tbody>{pairs}</tbody></table></div></details><details><summary>Verify report provenance</summary><pre>{provenance}</pre></details>'
        '<p class="note">Share this single HTML file. It works offline and contains no raw prompts, trace content or filesystem paths. Task IDs may still identify your work; inspect exports before sharing.</p></main></body></html>'
    )


def comparison_csv(doc: dict) -> str:
    out = io.StringIO(newline="")
    writer = csv.writer(out)
    writer.writerow(doc["columns"])
    for row in doc["rows"]:
        values = [row.get(key) for key in doc["columns"]]
        writer.writerow(["'" + v if isinstance(v, str) and v.startswith(("=", "+", "-", "@", "\t", "\r")) else v for v in values])
    return out.getvalue()


def write_report(doc: dict, out: str) -> None:
    root = Path(out)
    root.mkdir(parents=True, exist_ok=False)
    (root/"comparison.json").write_text(json.dumps(doc, indent=2, sort_keys=True, allow_nan=False)+"\n", encoding="utf-8")
    (root/"comparison.csv").write_text(comparison_csv(doc), encoding="utf-8")
    (root/"comparison.html").write_text(comparison_html(doc), encoding="utf-8")


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--baseline", required=True, metavar="RUNDIR")
    parser.add_argument("--variant", required=True, metavar="RUNDIR")
    parser.add_argument("--out", required=True, metavar="NEW_DIRECTORY", help="new directory for portable HTML, JSON and CSV")
    parser.add_argument("--alpha", type=float, default=None)


def main(args: argparse.Namespace) -> int:
    try:
        target = Path(args.out).resolve()
        for source in (Path(args.baseline).resolve(), Path(args.variant).resolve()):
            if target == source or source in target.parents:
                raise VerifyError("comparison output must be outside the input run directories")
        doc = build_comparison(args.baseline, args.variant, alpha=args.alpha)
        write_report(doc, args.out)
    except (VerifyError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"compare failed: {exc}", file=sys.stderr)
        return 2
    print(f'{doc["tasks_paired"]} task(s) paired; report: {Path(args.out)/"comparison.html"}')
    return 0
