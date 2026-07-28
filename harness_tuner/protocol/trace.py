"""HTP-1 trace records.

This module is normative. A trace record is the unit every metric is computed
from, and two independent implementations of HTP-1 must agree byte for byte on
the normalized form produced here.

The rule that matters most: a field whose value the adapter could not observe is
``None``. It is never zero, never an estimate, and never a default. Downstream,
``None`` becomes an ``unavailable`` measure with a stated reason, which is the
only way a report can be honest about a harness it can only partly see.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

HTP_VERSION = "1"

#: Kinds of step an adapter may report. Anything else is rejected, because a
#: silently-accepted unknown kind would be excluded from every metric while
#: still inflating the step count.
STEP_KINDS = (
    "tool_call",
    "model_call",
    "message",
    "human_input",
    "error",
    "checkpoint",
)

#: How a step turned out. ``unknown`` is legitimate at adapter level L0, where
#: the harness leaves no machine-readable outcome behind.
RESULT_KINDS = ("ok", "error", "empty", "truncated", "unknown")

TOKEN_KINDS = ("input", "output", "cache_read", "cache_write")

#: Tools whose purpose is to read state. Used by the rework metric. An adapter
#: may override this per harness through the task set, since tool naming is the
#: one thing no protocol can standardize across harnesses.
DEFAULT_READ_TOOL_HINTS = ("read", "get", "fetch", "list", "search", "grep", "cat", "view")

_REQUIRED = ("kind",)

_OPTIONAL = (
    "step",
    "tool",
    "args",
    "args_digest",
    "result_kind",
    "result_digest",
    "started_ms",
    "duration_ms",
    "tokens",
    "cost_usd",
    "model",
    "context_tokens",
    "step_hash",
)

_ALLOWED = frozenset(_REQUIRED + _OPTIONAL)


class TraceError(ValueError):
    """A trace record violates HTP-1."""


def canonical_json(obj: Any) -> str:
    """Deterministic JSON text used for every digest and every artifact.

    Sorted keys and no incidental whitespace, so the same logical value always
    produces the same bytes. Cassette replay byte-identity depends on this.
    """
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def digest(obj: Any) -> str:
    """Stable content digest of any JSON-shaped value."""
    payload = canonical_json(obj).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _check_int(value: Any, field: str, *, allow_negative: bool = False) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise TraceError(f"{field} must be an integer or null, got {type(value).__name__}")
    if value < 0 and not allow_negative:
        raise TraceError(f"{field} must not be negative, got {value}")
    return value


def _check_float(value: Any, field: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TraceError(f"{field} must be a number or null, got {type(value).__name__}")
    if value < 0:
        raise TraceError(f"{field} must not be negative, got {value}")
    return float(value)


def _normalize_tokens(raw: Any) -> dict[str, int | None]:
    if raw is None:
        return {kind: None for kind in TOKEN_KINDS}
    if not isinstance(raw, dict):
        raise TraceError("tokens must be an object or null")
    unknown = set(raw) - set(TOKEN_KINDS)
    if unknown:
        raise TraceError(f"unknown token kinds: {sorted(unknown)}")
    return {kind: _check_int(raw.get(kind), f"tokens.{kind}") for kind in TOKEN_KINDS}


def normalize_step(raw: dict[str, Any], index: int) -> dict[str, Any]:
    """Validate one adapter-supplied record and fill its derived fields.

    ``index`` is the zero-based position in the trace. The stored ``step`` is
    one-based and is always overwritten from the position, so an adapter cannot
    produce a trace with duplicate or missing step numbers.
    """
    if not isinstance(raw, dict):
        raise TraceError(f"step {index + 1}: record must be an object")

    extensions = {k: v for k, v in raw.items() if k.startswith("x_")}
    core = {k: v for k, v in raw.items() if not k.startswith("x_")}

    unknown = set(core) - _ALLOWED
    if unknown:
        raise TraceError(
            f"step {index + 1}: unknown field(s) {sorted(unknown)}. "
            "Vendor extensions must use an x_ prefix."
        )

    kind = core.get("kind")
    if kind not in STEP_KINDS:
        raise TraceError(f"step {index + 1}: kind must be one of {list(STEP_KINDS)}, got {kind!r}")

    tool = core.get("tool")
    if tool is not None and not isinstance(tool, str):
        raise TraceError(f"step {index + 1}: tool must be a string or null")
    if kind == "tool_call" and not tool:
        raise TraceError(f"step {index + 1}: a tool_call must name its tool")

    result_kind = core.get("result_kind") or "unknown"
    if result_kind not in RESULT_KINDS:
        raise TraceError(
            f"step {index + 1}: result_kind must be one of {list(RESULT_KINDS)}, got {result_kind!r}"
        )

    args = core.get("args")
    if args is not None and not isinstance(args, dict):
        raise TraceError(f"step {index + 1}: args must be an object or null")

    # An adapter that redacts arguments can still supply the digest, so loop
    # detection keeps working on a harness whose arguments must not be stored.
    args_digest = core.get("args_digest")
    if args_digest is None and args is not None:
        args_digest = digest(args)
    if args_digest is not None and not isinstance(args_digest, str):
        raise TraceError(f"step {index + 1}: args_digest must be a string or null")

    result_digest = core.get("result_digest")
    if result_digest is not None and not isinstance(result_digest, str):
        raise TraceError(f"step {index + 1}: result_digest must be a string or null")

    model = core.get("model")
    if model is not None and not isinstance(model, str):
        raise TraceError(f"step {index + 1}: model must be a string or null")

    step = {
        "step": index + 1,
        "kind": kind,
        "tool": tool,
        "args": args,
        "args_digest": args_digest,
        "result_kind": result_kind,
        "result_digest": result_digest,
        "started_ms": _check_int(core.get("started_ms"), f"step {index + 1}: started_ms"),
        "duration_ms": _check_int(core.get("duration_ms"), f"step {index + 1}: duration_ms"),
        "tokens": _normalize_tokens(core.get("tokens")),
        "cost_usd": _check_float(core.get("cost_usd"), f"step {index + 1}: cost_usd"),
        "model": model,
        "context_tokens": _check_int(core.get("context_tokens"), f"step {index + 1}: context_tokens"),
    }
    step.update(extensions)

    # Identity of the action, deliberately excluding timings, token counts and
    # cost. Two steps that did the same thing hash the same even if one was
    # slower or cheaper, which is what makes repeat detection meaningful.
    step["step_hash"] = digest(
        {"kind": kind, "tool": tool, "args_digest": args_digest}
    )
    return step


def normalize_trace(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize a whole trace. Raises on the first violation."""
    if not isinstance(records, list):
        raise TraceError("a trace must be a list of records")
    return [normalize_step(raw, i) for i, raw in enumerate(records)]


def read_trace(path) -> list[dict[str, Any]]:
    """Read and normalize a trace.jsonl file."""
    records: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as handle:
        for lineno, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise TraceError(f"{path}:{lineno}: not valid JSON: {exc}") from exc
    return normalize_trace(records)


def write_trace(path, steps: list[dict[str, Any]]) -> None:
    """Write a normalized trace deterministically, one record per line."""
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        for step in steps:
            handle.write(canonical_json(step))
            handle.write("\n")


def trace_digest(steps: list[dict[str, Any]]) -> str:
    """Digest of the action sequence, ignoring timing and cost.

    Two runs with the same trace_digest took the same actions in the same order.
    This is what the verify command compares when asking whether a proposed
    change altered behaviour at all.
    """
    return digest([step["step_hash"] for step in steps])
