"""The run pipeline: task records in, eight artifacts out.

Both execution modes converge here. Drive mode produces its task records by
invoking the harness; observe mode reads records the harness already left
behind. From that point the two are identical, which is what keeps a driven run
and an observed run comparable.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
from typing import Any

from . import __version__
from .config import Config, load as load_config
from .protocol import (
    HTP_VERSION,
    RunDir,
    aggregate,
    canonical_json,
    compute,
    digest,
    envelope,
    partial_coverage,
    trace_digest,
    write_json,
    write_text,
    write_trace,
)
from .report.fingerprint import build as build_fingerprint
from .report.render import report_html, summary_markdown
from .run.observer import collect
from .score import delegated
from .stamp import stamp as build_stamp


def _utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class TaskSetError(ValueError):
    """The task records for a run cannot produce a valid run.

    Raised before any metric is computed or any artifact is written, so a task
    set that could not be honest about itself never reaches disk.
    """


def refuse_duplicate_task_ids(
    task_records: list[dict[str, Any]], *, where: str | None = None
) -> None:
    """Refuse a task set in which one task id names more than one task.

    The per-task trace lives at ``tasks/<task_id>/trace.jsonl``, so two records
    sharing an id cannot both be kept. The second write destroys the first, and
    the run is then internally inconsistent in three ways at once: the per-task
    trace no longer differs from the merged one by nothing but ``x_task_id``,
    the run-level metrics still count a task whose trace is gone, and every
    consumer that pairs tasks by id -- ``verify``, ``compare``, and the
    diagnosis that re-reads each task's trace -- silently treats two tasks as
    one. ``compare`` already refuses this, with the reason that duplicate
    pairing would invent evidence; the same reasoning applies to the run that
    would hold the duplicates in the first place.

    Refused rather than disambiguated. A run in which one id names two tasks has
    no correct reading, and inventing a suffix would pair the two runs being
    compared on a name neither of them ever used.
    """
    positions: dict[str, list[int]] = {}
    for index, record in enumerate(task_records):
        positions.setdefault(str(record["task_id"]), []).append(index)

    collisions = {task_id: at for task_id, at in positions.items() if len(at) > 1}
    if not collisions:
        return

    described = []
    for task_id, at in sorted(collisions.items()):
        places = []
        for index in at:
            source = task_records[index].get("source")
            places.append(f"record {index + 1}" + (f" from {source}" if source else ""))
        described.append(f"{task_id!r} appears {len(at)} times, in " + " and ".join(places))

    raise TaskSetError(
        (f"{where}: " if where else "")
        + "task ids must be unique within a run: "
        + "; ".join(described)
        + ". The per-task trace is written to tasks/<task_id>/trace.jsonl, so a second task "
        "with the same id overwrites the first one's trace while the run-level metrics still "
        "count both. Give every task its own task_id."
    )


def make_run_id(task_records: list[dict[str, Any]], stamp: dict[str, Any]) -> str:
    """A run id that is stable for the same inputs within the same second.

    The leading timestamp makes runs sortable; the trailing digest makes two
    runs of different task sets distinguishable at a glance.
    """
    fingerprint = digest(
        {
            "tasks": [r["task_id"] for r in task_records],
            "traces": [trace_digest(r["steps"]) for r in task_records],
            "config": stamp,
        }
    )
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + fingerprint[7:15]


def load_capabilities(path: str | None) -> dict[str, Any]:
    """Read the capability record the setup agent wrote, or say it is absent.

    Capability discovery is a job for an agent that reads and understands the
    harness, not for a probe script, so this file is an input rather than
    something the engine derives. When it is missing the artifact says so
    plainly instead of implying a scan happened.
    """
    if path and os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as handle:
            record = json.load(handle)
        record.setdefault("source", os.path.abspath(path))
        return record
    return {
        "status": "not_scanned",
        "reason": (
            "no capabilities file was supplied. Capability discovery is performed by the "
            "agent that set harness-tuner up, by reading the harness, and its findings are "
            "written to a capabilities file. Without it, no benchmark was skipped on "
            "capability grounds and none was scored as failed on capability grounds either."
        ),
        "capabilities": {},
        "source": None,
    }


def execute(
    cfg: Config,
    task_records: list[dict[str, Any]],
    out_root: str,
    *,
    capabilities_path: str | None = None,
    judge_response_path: str | None = None,
    extra_run_fields: dict[str, Any] | None = None,
) -> RunDir:
    """Compute every artifact for one run and write all eight files."""
    refuse_duplicate_task_ids(task_records)
    stamp = build_stamp(cfg)
    hints = cfg.get("harness.read_tool_hints")

    # Outcomes the packs could not settle deterministically are judged. By
    # default that happens through a file your agent answers, so the engine
    # holds no credentials. A task left unjudged stays unavailable; it is never
    # assumed to have passed.
    judging = delegated.apply_response(task_records, judge_response_path)

    per_task_metrics: list[dict[str, dict]] = []
    task_runs: list[dict[str, Any]] = []
    for record in task_records:
        task = dict(record.get("task") or {})
        # A harness-wide hint list applies to every task unless the task
        # overrode it, since tool naming is a property of the harness.
        if hints and "read_tool_hints" not in task:
            task["read_tool_hints"] = hints
        metrics = compute(record["steps"], task)
        per_task_metrics.append(metrics)
        task_runs.append(
            {
                "task_id": record["task_id"],
                "tags": record.get("tags") or [],
                "step_count": len(record["steps"]),
                "trace_digest": trace_digest(record["steps"]),
                "metrics": metrics,
                "source": record.get("source"),
            }
        )

    agg = aggregate(per_task_metrics)
    run_id = make_run_id(task_records, stamp)
    rundir = RunDir(os.path.join(out_root, run_id))
    rundir.ensure()

    fp = build_fingerprint(
        agg, task_runs, min_tasks=int(cfg.get("report.min_tasks_for_fingerprint"))
    )

    run_doc: dict[str, Any] = envelope(run_id, "run")
    run_doc.update(
        {
            "created_utc": _utc_now(),
            "htp_version": HTP_VERSION,
            "config": stamp,
            "config_source": cfg.source,
            "task_count": len(task_records),
            "judging": judging,
            "tasks": [
                {
                    "task_id": t["task_id"],
                    "tags": t["tags"],
                    "step_count": t["step_count"],
                    "trace_digest": t["trace_digest"],
                }
                for t in task_runs
            ],
        }
    )
    if extra_run_fields:
        run_doc.update(extra_run_fields)

    # Reproduction manifest. verify re-runs from exactly this.
    manifest = envelope(run_id, "manifest")
    manifest.update(
        {
            "created_utc": run_doc["created_utc"],
            "engine_version": __version__,
            "config": stamp,
            "config_digest": digest(stamp),
            "seed": cfg.get("tasks.seed"),
            "task_set": [
                {"task_id": t["task_id"], "tags": t["tags"], "source": t["source"]}
                for t in task_runs
            ],
            "task_set_digest": digest([t["task_id"] for t in task_runs]),
            "alpha": cfg.get("report.alpha"),
            "predictions": [],
        }
    )

    metrics_doc = envelope(run_id, "metrics")
    metrics_doc.update(
        {
            "aggregate": agg,
            "per_task": [
                {"task_id": t["task_id"], "tags": t["tags"], "metrics": t["metrics"]}
                for t in task_runs
            ],
        }
    )

    fingerprint_doc = envelope(run_id, "fingerprint")
    fingerprint_doc.update(fp)

    caps = envelope(run_id, "capabilities")
    caps.update(load_capabilities(capabilities_path))

    write_json(rundir.path("run"), run_doc)
    write_json(rundir.path("metrics"), metrics_doc)
    write_json(rundir.path("manifest"), manifest)
    write_json(rundir.path("fingerprint"), fingerprint_doc)
    write_json(rundir.path("capabilities"), caps)
    write_text(rundir.path("summary"), summary_markdown(run_doc, agg, fp))
    write_text(rundir.path("report"), report_html(run_doc, agg, fp, metrics_doc["per_task"]))

    # Per-task traces, then the merged view at the run root. Merged records
    # carry x_task_id and keep their within-task step number, so a record in
    # the merged file and in the per-task file differ only by that field.
    merged: list[dict[str, Any]] = []
    for record in task_records:
        task_id = record["task_id"]
        os.makedirs(rundir.task_dir(task_id), exist_ok=True)
        write_trace(rundir.task_path(task_id, "trace"), record["steps"])
        for step in record["steps"]:
            tagged = dict(step)
            tagged["x_task_id"] = task_id
            merged.append(tagged)
    write_trace(rundir.path("trace"), merged)

    # Tasks still without an outcome need a verdict. The request names the
    # model the user chose for judging and goes beside the run, so answering it
    # is one file write rather than an integration.
    if judging["pending"]:
        write_json(
            os.path.join(rundir.root, delegated.REQUEST_FILE),
            delegated.build_request(cfg, task_records),
        )

    missing = rundir.missing()
    if missing:
        raise RuntimeError(f"run {run_id} did not write required artifact(s): {missing}")
    return rundir


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def load_task_set(path: str) -> list[dict[str, Any]]:
    """Read a drive-mode task set: a JSON list, or an object with a 'tasks' key.

    Duplicate task ids are refused here rather than later, because this is the
    last point before the harness is invoked. A collision that only surfaced
    when the artifacts were written would have been discovered after every task
    had already been driven and paid for.
    """
    with open(path, "r", encoding="utf-8") as handle:
        raw = json.load(handle)
    tasks = raw.get("tasks") if isinstance(raw, dict) else raw
    if not isinstance(tasks, list) or not tasks:
        raise ValueError(f"{path}: expected a non-empty list of tasks")
    for i, task in enumerate(tasks):
        if not isinstance(task, dict) or "task_id" not in task:
            raise ValueError(f"{path}: task {i} has no task_id")
    refuse_duplicate_task_ids(tasks, where=path)
    return tasks


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "source",
        help=(
            "in observe mode, an intake file or a directory of them produced by your adapter. "
            "In drive mode, a task set to run the harness against."
        ),
    )
    parser.add_argument("--config", default=None, metavar="FILE", help="path to harness-tuner.toml")
    parser.add_argument("--out", default="results", metavar="DIR", help="where to write the run directory")
    parser.add_argument(
        "--capabilities",
        default=None,
        metavar="FILE",
        help="capability record your setup agent wrote after reading the harness",
    )
    parser.add_argument(
        "--no-cassette",
        action="store_true",
        help="ignore cost.cassette and drive the harness for real, even if a recording exists",
    )


def main(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    problems = cfg.validate()
    if problems:
        print(f"configuration ({cfg.source}) has {len(problems)} problem(s):", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 2

    execution = cfg.get("mode.execution")
    extra: dict[str, Any] = {}

    if execution == "observe":
        try:
            records = collect(args.source)
        except (OSError, ValueError) as exc:
            print(f"run failed: {exc}", file=sys.stderr)
            return 2
    else:
        from .run.budget import Governor
        from .run.cassette import Cassette
        from .run.driver import DriveError, drive

        try:
            tasks = load_task_set(args.source)
        except (OSError, ValueError) as exc:
            print(f"run failed: {exc}", file=sys.stderr)
            return 2
        governor = Governor.from_config(cfg)
        cassette = None
        if cfg.get("cost.cassette") and not args.no_cassette:
            cassette = Cassette(cfg.get("cost.cassette_path"))

        workspace = cfg.get("harness.workspace") or os.getcwd()
        try:
            records, halt = drive(
                cfg,
                tasks,
                workspace=workspace,
                cassette=cassette,
                governor=governor,
                seed=int(cfg.get("tasks.seed")),
                on_event=lambda tid, how: print(f"  {how:9s} {tid}"),
            )
        except DriveError as exc:
            print(f"drive failed: {exc}", file=sys.stderr)
            return 3

        extra["budget"] = governor.state()
        extra["cassette"] = cassette.state() if cassette else {"enabled": False}
        extra["halt"] = halt
        for blind in governor.blind_spots():
            print(f"budget warning: {blind}", file=sys.stderr)
        if halt:
            print(f"run halted at task {halt['at_task']}: {halt['reason']}", file=sys.stderr)
        if not records:
            print("no task produced a trace, so there is nothing to measure", file=sys.stderr)
            return 4

    try:
        rundir = execute(
            cfg, records, args.out, capabilities_path=args.capabilities, extra_run_fields=extra
        )
    except TaskSetError as exc:
        print(f"run failed: {exc}", file=sys.stderr)
        return 2

    print(f"run written to {rundir.root}")
    for name in sorted(os.listdir(rundir.root)):
        full = os.path.join(rundir.root, name)
        if os.path.isfile(full):
            print(f"  {name}  ({os.path.getsize(full)} bytes)")
    with open(rundir.path("metrics"), "r", encoding="utf-8") as handle:
        agg = json.load(handle)["aggregate"]
    gaps = sum(1 for m in agg.values() if m["status"] == "unavailable")
    partial = len(partial_coverage(agg))
    full = len(agg) - gaps - partial
    print(
        f"\n{full} metric(s) measured in every task, {partial} in only some of them, "
        f"{gaps} not measurable at all. summary.md lists the last two first."
    )
    return 0
