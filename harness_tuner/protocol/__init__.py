"""HTP-1: the fixed surface.

Everything else in harness-tuner is negotiated with the user at setup time.
These three modules are not, because a report nobody can compare against
another report is not evidence of anything.

- :mod:`.trace`     what a step record contains
- :mod:`.metrics`   exactly how each number is computed from those records
- :mod:`.artifacts` exactly which files a run emits and what they are called

An independent implementation is HTP-1 conformant when it reproduces the
metric values in ``conformance/expected`` from the traces in
``conformance/fixtures``.
"""

from .artifacts import ARTIFACT_NAMES, ARTIFACTS, RunDir, envelope, write_json, write_text
from .metrics import (
    AGGREGATION,
    LOWER_IS_BETTER,
    REGISTRY,
    aggregate,
    compute,
    is_measured,
    measured,
    partial_coverage,
    unavailable,
)
from .trace import (
    HTP_VERSION,
    RESULT_KINDS,
    STEP_KINDS,
    TraceError,
    canonical_json,
    digest,
    normalize_trace,
    read_trace,
    trace_digest,
    write_trace,
)

__all__ = [
    "AGGREGATION",
    "ARTIFACTS",
    "ARTIFACT_NAMES",
    "HTP_VERSION",
    "LOWER_IS_BETTER",
    "partial_coverage",
    "REGISTRY",
    "RESULT_KINDS",
    "RunDir",
    "STEP_KINDS",
    "TraceError",
    "aggregate",
    "canonical_json",
    "compute",
    "digest",
    "envelope",
    "is_measured",
    "measured",
    "normalize_trace",
    "read_trace",
    "trace_digest",
    "unavailable",
    "write_json",
    "write_text",
]
