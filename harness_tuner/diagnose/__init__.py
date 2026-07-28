"""Diagnosis: findings the engine proved, and proposals a human applies.

The ``diagnose`` command reads a completed run, detects findings from the
traces deterministically, writes proposals with falsifiable predictions, and
records those predictions into the run's manifest so ``verify`` can score them
later.

It also writes a request file for the diagnostician model. That is the whole of
the model's involvement in this step, and the boundary is deliberate: the
engine holds no credentials and calls nothing by default. Your agent answers
the request using whatever provider and key you already have, writes the answer
back as a file, and the engine folds it in. The findings stand with or without
that answer, because they were arithmetic on a trace before any model saw them.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from ..protocol import RunDir, envelope, write_json, write_text
from . import findings as F
from . import proposals as P

DIAGNOSIS_FILE = "diagnosis.json"
REQUEST_FILE = "diagnosis-request.json"
RESPONSE_FILE = "diagnosis-response.json"


def _load_run(root: str) -> tuple[RunDir, dict[str, Any], dict[str, Any], dict[str, Any]]:
    rundir = RunDir(root)
    missing = rundir.missing()
    if missing:
        raise FileNotFoundError(f"{root} is not a complete run; missing {missing}")
    with open(rundir.path("metrics"), "r", encoding="utf-8") as handle:
        metrics = json.load(handle)
    with open(rundir.path("run"), "r", encoding="utf-8") as handle:
        run = json.load(handle)
    with open(rundir.path("manifest"), "r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    return rundir, run, metrics, manifest


def _task_runs_with_steps(rundir: RunDir, metrics: dict[str, Any]) -> list[dict[str, Any]]:
    """Rehydrate per-task metrics together with their traces."""
    from ..protocol import read_trace

    out = []
    for entry in metrics["per_task"]:
        task_id = entry["task_id"]
        path = rundir.task_path(task_id, "trace")
        steps = read_trace(path) if os.path.isfile(path) else []
        out.append({"task_id": task_id, "tags": entry.get("tags") or [],
                    "metrics": entry["metrics"], "steps": steps})
    return out


def build_request(run: dict[str, Any], found: list[dict[str, Any]], model: str) -> dict[str, Any]:
    """What the diagnostician model is asked, and explicitly what it is not."""
    return {
        "instructions": (
            "You are the diagnostician. Every finding below was detected deterministically "
            "from trace records and each carries the exact steps it came from. Do NOT re-judge "
            "whether a finding is real, and do not add findings of your own: you cannot see "
            "the traces, and a model asked to find problems will find them in any input. "
            "For each finding, say which specific part of THIS harness would be changed to "
            "address it, naming the file, setting, prompt section, or hook. If you do not know "
            "the harness well enough to name one, say so for that finding rather than guessing."
        ),
        "answer_format": {
            "file": RESPONSE_FILE,
            "shape": {"findings": [{"finding_id": "...", "where": "...", "how": "...",
                                    "confidence": "high | medium | low | unknown"}]},
        },
        "model_requested": model or "(models.diagnostician is unset)",
        "harness": run.get("config", {}).get("harness", {}),
        "findings": [
            {
                "finding_id": f["id"],
                "severity": f["severity"],
                "title": f["title"],
                "detail": f["detail"],
                "measured": f.get("measured"),
            }
            for f in found
        ],
    }


def merge_response(proposals: list[dict[str, Any]], response_path: str | None) -> int:
    """Fold the diagnostician's harness-specific advice into the proposals."""
    if not response_path or not os.path.isfile(response_path):
        return 0
    with open(response_path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    by_finding = {entry.get("finding_id"): entry for entry in payload.get("findings", [])}
    merged = 0
    for proposal in proposals:
        entry = by_finding.get(proposal["finding_id"])
        if not entry:
            continue
        proposal["harness_specific"] = {
            "where": entry.get("where"),
            "how": entry.get("how"),
            "confidence": entry.get("confidence", "unknown"),
            "source": "models.diagnostician, via the delegated call path",
        }
        merged += 1
    return merged


def render_markdown(run: dict[str, Any], found: list[dict[str, Any]], props: list[dict[str, Any]]) -> str:
    lines = [f"# Diagnosis for run {run['run_id']}", ""]
    if not found:
        lines.append("No findings. Every detector came back clean for this run.")
        return "\n".join(lines) + "\n"

    lines.append(
        f"{len(found)} finding(s), {len(props)} of them with something to change. "
        "Each finding was detected by counting things in the traces, and lists the steps it "
        "came from. harness-tuner does not apply any of this."
    )
    lines.append("")
    for proposal in props:
        lines.append(f"## [{proposal['severity']}] {proposal['proposal_id']}: {proposal['title']}")
        lines.append("")
        lines.append(f"**Why this happens.** {proposal['why']}")
        lines.append("")
        lines.append(f"**What to change.** {proposal['change']}")
        specific = proposal.get("harness_specific")
        if specific and specific.get("where"):
            lines.append("")
            lines.append(
                f"**In your harness** ({specific['confidence']} confidence): "
                f"{specific['where']}. {specific.get('how') or ''}".strip()
            )
        if proposal.get("bound"):
            lines.append("")
            lines.append(f"**Bound.** {proposal['bound']}")
        if proposal.get("prediction"):
            pred = proposal["prediction"]
            lines.append("")
            lines.append(
                f"**Prediction.** `{pred['metric']}` should {pred['direction']}. "
                f"{proposal['falsified_by']}"
            )
        lines.append("")

    observations = [f for f in found if not any(p["finding_id"] == f["id"] for p in props)]
    if observations:
        lines.append("## Observations with nothing to propose")
        lines.append("")
        for finding in observations:
            lines.append(f"- **{finding['title']}**. {finding['detail']}")
        lines.append("")
    return "\n".join(lines)


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("run", help="a completed run directory")
    parser.add_argument(
        "--response",
        default=None,
        metavar="FILE",
        help=f"the diagnostician's answer, if you have one (default: {RESPONSE_FILE} in the run)",
    )
    parser.add_argument(
        "--no-request",
        action="store_true",
        help="skip writing the diagnostician request file",
    )


def main(args: argparse.Namespace) -> int:
    try:
        rundir, run, metrics, manifest = _load_run(args.run)
    except FileNotFoundError as exc:
        print(f"diagnose failed: {exc}", file=sys.stderr)
        return 2

    agg = metrics["aggregate"]
    task_runs = _task_runs_with_steps(rundir, metrics)
    found = F.analyze(agg, task_runs, run)
    props = P.build(found)

    response = args.response or os.path.join(rundir.root, RESPONSE_FILE)
    merged = merge_response(props, response)

    doc = envelope(run["run_id"], "diagnosis")
    doc.update({"findings": found, "proposals": props,
                "diagnostician_response_merged": merged})
    write_json(os.path.join(rundir.root, DIAGNOSIS_FILE), doc)
    write_text(os.path.join(rundir.root, "diagnosis.md"), render_markdown(run, found, props))

    # verify scores these later, so they live with the run that produced them.
    manifest["predictions"] = P.predictions(props)
    write_json(rundir.path("manifest"), manifest)

    if not args.no_request:
        model = run.get("config", {}).get("models", {}).get("diagnostician", "")
        write_json(os.path.join(rundir.root, REQUEST_FILE), build_request(run, found, model))

    by_severity: dict[str, int] = {}
    for finding in found:
        by_severity[finding["severity"]] = by_severity.get(finding["severity"], 0) + 1
    order = [F.CRITICAL, F.HIGH, F.MEDIUM, F.LOW, F.INFO]
    summary = ", ".join(f"{by_severity[s]} {s}" for s in order if s in by_severity) or "none"

    print(f"{len(found)} finding(s): {summary}")
    print(f"{len(props)} proposal(s) written to {os.path.join(rundir.root, 'diagnosis.md')}")
    print(f"{len(manifest['predictions'])} prediction(s) recorded in manifest.json for verify")
    if merged:
        print(f"{merged} proposal(s) carry harness-specific advice from the diagnostician")
    elif not args.no_request:
        print(
            f"\nNo diagnostician response found. To add harness-specific advice, have your "
            f"agent answer {os.path.join(rundir.root, REQUEST_FILE)} and write "
            f"{RESPONSE_FILE} beside it, then run diagnose again. The findings above do not "
            "depend on it."
        )
    return 0
