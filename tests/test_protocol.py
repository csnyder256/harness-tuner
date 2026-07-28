"""Hand-computed assertions against the HTP-1 protocol.

The conformance suite compares this implementation against expected files that
this implementation generated, which proves nothing on its own. These tests
break that circle: every number below was worked out by hand from the fixture
and written here independently. If a metric definition drifts, conformance and
these tests disagree, and the disagreement is the signal.
"""

from __future__ import annotations

import json
import os

import pytest

from harness_tuner.conformance import evaluate, load_fixtures, suite_root
from harness_tuner.protocol import metrics as M
from harness_tuner.protocol import trace as T

ROOT = suite_root()


def fixture(name: str) -> dict:
    with open(os.path.join(ROOT, "fixtures", f"{name}.json"), "r", encoding="utf-8") as handle:
        return json.load(handle)


def metrics_for(name: str) -> dict:
    return evaluate(fixture(name))


def value(name: str, metric: str):
    entry = metrics_for(name)[metric]
    assert entry["status"] == M.MEASURED, f"{name}.{metric} was {entry['status']}: {entry['reason']}"
    return entry["value"]


def reason(name: str, metric: str) -> str:
    entry = metrics_for(name)[metric]
    assert entry["status"] == M.UNAVAILABLE, f"{name}.{metric} was measured as {entry['value']}"
    assert entry["value"] is None, "an unavailable measure must carry a null value"
    assert entry["reason"], "an unavailable measure must state a reason"
    return entry["reason"]


# ---------------------------------------------------------------------------
# The rule the whole project rests on: unobserved is not zero.
# ---------------------------------------------------------------------------


def test_l0_reports_unavailable_and_never_zero():
    """A text-log adapter must not produce a single fabricated number."""
    m = metrics_for("l0_minimal")
    for name in (
        "tokens_input",
        "tokens_output",
        "tokens_cache_read",
        "tokens_cache_write",
        "cache_hit_ratio",
        "cost_usd",
        "context_peak",
        "context_growth_per_step",
        "wall_ms",
        "time_to_first_action_ms",
        "error_count",
        "error_rate",
        "recovery_rate",
        "redundant_call_count",
        "loop_max_run",
        "rework_ratio",
        "step_efficiency",
        "task_success",
    ):
        assert m[name]["status"] == M.UNAVAILABLE, f"{name} must be unavailable at L0"
        assert m[name]["value"] is None, f"{name} leaked a value at L0"

    # Only what a bare tool-name log genuinely supports.
    assert value("l0_minimal", "step_count") == 4
    assert value("l0_minimal", "tool_call_count") == 4
    assert value("l0_minimal", "distinct_tools") == 4
    assert value("l0_minimal", "human_input_steps") == 0
    assert value("l0_minimal", "autonomy_ratio") == 1.0


def test_measured_zero_is_not_unavailable():
    """cache_cold reports cache_read as an explicit 0 on every call.

    A measured 0.0 is a finding about the harness. An unavailable is a gap in
    the adapter. Collapsing them would let the engine invent a finding.
    """
    entry = metrics_for("cache_cold")["cache_hit_ratio"]
    assert entry["status"] == M.MEASURED
    assert entry["value"] == 0.0

    assert metrics_for("l0_minimal")["cache_hit_ratio"]["status"] == M.UNAVAILABLE


def test_recovery_over_zero_errors_is_undefined_not_perfect():
    assert "no error occurred" in reason("l1_full", "recovery_rate")


# ---------------------------------------------------------------------------
# Arithmetic, computed by hand from the fixture files.
# ---------------------------------------------------------------------------


def test_l1_full_numbers():
    # tokens: input 1200+300+400, output 150+90+200,
    #         cache_read 0+1200+1500, cache_write 1200+0+0
    assert value("l1_full", "tokens_input") == 1900
    assert value("l1_full", "tokens_output") == 440
    assert value("l1_full", "tokens_cache_read") == 2700
    assert value("l1_full", "tokens_cache_write") == 1200

    # 2700 / (2700 + 1900) = 0.58695652...
    assert value("l1_full", "cache_hit_ratio") == 0.586957

    # 0.0060 + 0.0021 + 0.0030
    assert value("l1_full", "cost_usd") == 0.0111

    # least squares over (1, 1350), (3, 1890), (5, 2600):
    # numerator 2500, denominator 8, slope 312.5
    assert value("l1_full", "context_growth_per_step") == 312.5
    assert value("l1_full", "context_peak") == 2600

    # last step starts at 2565 and runs 30ms
    assert value("l1_full", "wall_ms") == 2595
    assert value("l1_full", "time_to_first_action_ms") == 0

    # 3 tool calls, declared optimal 3
    assert value("l1_full", "step_efficiency") == 1.0
    assert value("l1_full", "step_count") == 6
    assert value("l1_full", "tool_call_count") == 3
    assert value("l1_full", "distinct_tools") == 3
    assert value("l1_full", "task_success") is True


def test_looping_numbers():
    # read_file with identical args five times, so four are repeats,
    # over six comparable calls
    assert value("looping", "redundant_call_count") == 4
    assert value("looping", "redundant_call_ratio") == 0.666667
    assert value("looping", "loop_max_run") == 5
    # five read-shaped calls, four of them repeats
    assert value("looping", "rework_ratio") == 0.8


def test_rework_without_looping():
    # a, b, a, c, b -> two repeats, never two identical in a row
    assert value("rework_reads", "redundant_call_count") == 2
    assert value("rework_reads", "rework_ratio") == 0.4
    assert value("rework_reads", "loop_max_run") == 1


def test_recovery_window_is_enforced():
    # both errors retried on the very next step
    assert value("errors_recovered", "recovery_rate") == 1.0
    assert value("errors_recovered", "error_rate") == 0.5

    # the retry lands 4 steps later, outside the window of 3
    assert value("errors_unrecovered", "recovery_rate") == 0.0
    assert value("errors_unrecovered", "error_rate") == 0.2
    assert M.RECOVERY_WINDOW == 3


def test_context_slope():
    # 1000, 2000, 3000, 4000 at steps 1..4: numerator 5000, denominator 5
    assert value("context_growth", "context_growth_per_step") == 1000.0
    # 5120, 5660, 6220 at steps 1..3: numerator 1100, denominator 2
    assert value("cache_cold", "context_growth_per_step") == 550.0


def test_autonomy_counts_human_input():
    # 5 steps, 2 of them human_input
    assert value("human_in_loop", "autonomy_ratio") == 0.6
    assert value("human_in_loop", "human_input_steps") == 2


def test_redacted_args_still_detects_repeats():
    """Loop detection needs sameness, not contents."""
    assert value("redacted_args", "redundant_call_count") == 2
    assert value("redacted_args", "loop_max_run") == 2
    # nothing leaked: the fixture supplies no args at all
    steps = T.normalize_trace(fixture("redacted_args")["trace"])
    assert all(step["args"] is None for step in steps)


# ---------------------------------------------------------------------------
# Schema enforcement
# ---------------------------------------------------------------------------


def test_unknown_field_is_rejected():
    with pytest.raises(T.TraceError, match="unknown field"):
        T.normalize_trace([{"kind": "tool_call", "tool": "x", "sneaky": 1}])


def test_vendor_extension_is_preserved():
    steps = T.normalize_trace([{"kind": "tool_call", "tool": "x", "x_harness_id": "abc"}])
    assert steps[0]["x_harness_id"] == "abc"


def test_unknown_kind_is_rejected():
    with pytest.raises(T.TraceError, match="kind must be one of"):
        T.normalize_trace([{"kind": "teleport"}])


def test_tool_call_must_name_its_tool():
    with pytest.raises(T.TraceError, match="must name its tool"):
        T.normalize_trace([{"kind": "tool_call"}])


def test_negative_tokens_are_rejected():
    with pytest.raises(T.TraceError, match="must not be negative"):
        T.normalize_trace([{"kind": "model_call", "tokens": {"input": -5}}])


def test_step_numbers_are_assigned_not_trusted():
    steps = T.normalize_trace(
        [{"kind": "message", "step": 99}, {"kind": "message", "step": 99}]
    )
    assert [s["step"] for s in steps] == [1, 2]


def test_step_hash_ignores_timing_and_cost():
    a = T.normalize_trace([{"kind": "tool_call", "tool": "t", "args": {"p": 1}, "duration_ms": 10}])
    b = T.normalize_trace([{"kind": "tool_call", "tool": "t", "args": {"p": 1}, "duration_ms": 9999}])
    assert a[0]["step_hash"] == b[0]["step_hash"]

    c = T.normalize_trace([{"kind": "tool_call", "tool": "t", "args": {"p": 2}}])
    assert a[0]["step_hash"] != c[0]["step_hash"]


def test_canonical_json_is_key_order_independent():
    assert T.digest({"a": 1, "b": 2}) == T.digest({"b": 2, "a": 1})


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def test_aggregate_marks_partial_coverage():
    per_task = [metrics_for("l1_full"), metrics_for("l0_minimal")]
    agg = M.aggregate(per_task)
    # counts sum across both tasks: 6 + 4
    assert agg["step_count"]["value"] == 10
    # cost was measurable in only one of the two, and the entry says so
    assert agg["cost_usd"]["status"] == M.MEASURED
    assert "1 of 2" in agg["cost_usd"]["reason"]


def test_aggregate_keeps_unavailable_when_no_task_measured_it():
    agg = M.aggregate([metrics_for("l0_minimal")])
    entry = agg["cost_usd"]
    assert entry["status"] == M.UNAVAILABLE
    assert entry["value"] is None
    assert "unavailable in all 1 task" in entry["reason"]


def test_aggregate_turns_bools_into_a_rate():
    agg = M.aggregate([metrics_for("l1_full"), metrics_for("errors_unrecovered")])
    # one task succeeded, one did not
    assert agg["task_success"]["value"] == 0.5
    assert agg["task_success"]["unit"] == "ratio"


# ---------------------------------------------------------------------------
# The suite itself
# ---------------------------------------------------------------------------


def test_every_fixture_has_an_expected_file():
    names = {f["name"] for f in load_fixtures(ROOT)}
    have = {
        os.path.splitext(n)[0]
        for n in os.listdir(os.path.join(ROOT, "expected"))
        if n.endswith(".json")
    }
    assert names == have, f"fixtures and expected files disagree: {names ^ have}"


def test_every_registered_metric_appears_in_every_expected_file():
    """A metric added to the registry without a conformance value is invisible."""
    for name in sorted({f["name"] for f in load_fixtures(ROOT)}):
        with open(os.path.join(ROOT, "expected", f"{name}.json"), "r", encoding="utf-8") as fh:
            recorded = set(json.load(fh)["metrics"])
        assert recorded == set(M.REGISTRY), f"{name} expected file is out of date"
