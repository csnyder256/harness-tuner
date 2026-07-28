"""Delegated judging: the engine asks, your agent answers.

Some task outcomes cannot be checked deterministically. Whether a summary is
faithful, whether a plan was reasonable, whether an explanation was correct,
these need a model. The default path here never calls one.

Instead the engine writes a request naming every task that needs a verdict,
your agent answers it with whatever provider and key you already have, and the
engine reads the answers back as a file. The engine holds no credentials, ships
no provider code that will date, and works identically against a frontier API,
a local server, or a human filling in the file by hand.

The cost of this is one extra round trip through your agent. The benefit is
that the tool has no opinion about who you buy models from, which is the only
way "harness and model agnostic" survives contact with a real install.

A task with no verdict stays unavailable. It is never assumed to have passed.
"""

from __future__ import annotations

import json
import os
from typing import Any

REQUEST_FILE = "judge-request.json"
RESPONSE_FILE = "judge-response.json"


def needs_verdict(task_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Tasks whose outcome no deterministic check settled."""
    return [r for r in task_records if (r.get("task") or {}).get("success") is None]


def build_request(cfg, task_records: list[dict[str, Any]]) -> dict[str, Any]:
    """The judging request. Names the model the user chose for this job."""
    pending = needs_verdict(task_records)
    return {
        "instructions": (
            "For each task below, decide whether the harness accomplished what the prompt "
            "asked. Answer only about the outcome. Do not score style, efficiency or cost: "
            "those are measured from the trace and a model's opinion about them would "
            "silently overwrite a measurement. If the trace does not let you tell, answer "
            "null rather than guessing, and the metric will report unavailable."
        ),
        "model_requested": cfg.get("models.judge") or "(models.judge is unset)",
        "answer_format": {
            "file": RESPONSE_FILE,
            "shape": {"verdicts": [{"task_id": "...", "success": True, "why": "..."}]},
        },
        "tasks": [
            {
                "task_id": record["task_id"],
                "prompt": (record.get("task") or {}).get("prompt"),
                "steps": len(record.get("steps") or []),
                "trace_file": f"tasks/{record['task_id']}/trace.jsonl",
            }
            for record in pending
        ],
    }


def apply_response(task_records: list[dict[str, Any]], path: str | None) -> dict[str, Any]:
    """Fold judge verdicts into the task records. Missing verdicts stay missing."""
    if not path or not os.path.isfile(path):
        pending = needs_verdict(task_records)
        return {
            "applied": 0,
            "pending": len(pending),
            "source": None,
            "note": (
                f"{len(pending)} task(s) have no outcome. task_success will report "
                "unavailable for them rather than being assumed true."
            )
            if pending
            else "every task carried a deterministic outcome; no judging was needed.",
        }

    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    verdicts = {v["task_id"]: v for v in payload.get("verdicts", []) if v.get("task_id")}

    applied = 0
    for record in task_records:
        verdict = verdicts.get(record["task_id"])
        if not verdict or verdict.get("success") is None:
            continue
        record.setdefault("task", {})["success"] = bool(verdict["success"])
        record["task"]["success_source"] = "judge"
        record["task"]["success_why"] = verdict.get("why")
        applied += 1

    return {
        "applied": applied,
        "pending": len(needs_verdict(task_records)),
        "source": os.path.abspath(path),
        "note": f"{applied} verdict(s) applied from the delegated judge.",
    }
