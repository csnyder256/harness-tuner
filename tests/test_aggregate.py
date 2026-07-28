"""Aggregation across tasks.

Every test in this file exists because the first implementation got it wrong in
a way that looked completely plausible in a rendered table. It inferred each
metric's combiner from its unit string, so a per-task longest-loop-run of 5 and
another of 1 aggregated to 6, and a peak context of 6220 plus a peak of 2600
aggregated to 8820. Both are numbers that describe no run that ever happened,
and nothing failed.
"""

from __future__ import annotations

from harness_tuner.conformance import evaluate, load_fixtures, suite_root
from harness_tuner.protocol import metrics as M
from harness_tuner.report.render import summary_markdown

ROOT = suite_root()
FIXTURES = {f["name"]: f for f in load_fixtures(ROOT)}


def metrics_for(name: str) -> dict:
    return evaluate(FIXTURES[name])


def test_every_registered_metric_declares_a_combiner():
    """A metric with no combiner would crash or, worse, get a silent default."""
    assert set(M.AGGREGATION) == set(M.REGISTRY)
    assert set(M.AGGREGATION.values()) <= {"sum", "max", "mean", "rate"}


def test_maxima_aggregate_by_max_not_sum():
    # looping has a longest run of 5, rework_reads has 1.
    assert metrics_for("looping")["loop_max_run"]["value"] == 5
    assert metrics_for("rework_reads")["loop_max_run"]["value"] == 1

    agg = M.aggregate([metrics_for("looping"), metrics_for("rework_reads")])
    assert agg["loop_max_run"]["value"] == 5, "a longest run must not be summed"
    assert agg["loop_max_run"]["coverage"]["combiner"] == "max"


def test_peaks_aggregate_by_max_not_sum():
    # cache_cold peaks at 6220, l1_full at 2600.
    assert metrics_for("cache_cold")["context_peak"]["value"] == 6220
    assert metrics_for("l1_full")["context_peak"]["value"] == 2600

    agg = M.aggregate([metrics_for("cache_cold"), metrics_for("l1_full")])
    assert agg["context_peak"]["value"] == 6220, "a peak must not be summed"


def test_counts_and_cost_do_sum():
    agg = M.aggregate([metrics_for("l1_full"), metrics_for("looping")])
    # 6 steps + 6 steps
    assert agg["step_count"]["value"] == 12
    assert agg["step_count"]["coverage"]["combiner"] == "sum"
    # only l1_full reported cost
    assert agg["cost_usd"]["value"] == 0.0111


def test_ratios_are_averaged_not_summed():
    agg = M.aggregate([metrics_for("looping"), metrics_for("rework_reads")])
    # 0.8 and 0.4
    assert agg["rework_ratio"]["value"] == 0.6
    assert agg["rework_ratio"]["coverage"]["combiner"] == "mean"


def test_every_aggregate_entry_records_its_coverage():
    agg = M.aggregate([metrics_for("l1_full"), metrics_for("l0_minimal")])
    for name, entry in agg.items():
        cov = entry.get("coverage")
        assert cov is not None, f"{name} carries no coverage record"
        assert cov["total"] == 2
        assert 0 <= cov["measured"] <= 2
        assert cov["combiner"] == M.AGGREGATION[name]


def test_partial_coverage_reason_names_the_combiner():
    """'averaged over 3 of 10' on a summed metric tells the reader the wrong thing."""
    agg = M.aggregate([metrics_for("l1_full"), metrics_for("l0_minimal")])
    assert "summed over the 1 of 2" in agg["cost_usd"]["reason"]
    assert "mean over the 1 of 2" in agg["cache_hit_ratio"]["reason"]


def test_partial_coverage_is_listed():
    agg = M.aggregate([metrics_for("l1_full"), metrics_for("l0_minimal")])
    names = {name for name, _ in M.partial_coverage(agg)}
    assert "cost_usd" in names
    assert "tokens_input" in names
    # measured in both tasks, so not partial
    assert "step_count" not in names


def test_summary_never_claims_full_coverage_when_partial():
    """The headline claim has to agree with the table underneath it."""
    per_task = [metrics_for("l1_full"), metrics_for("l0_minimal")]
    agg = M.aggregate(per_task)
    run = {
        "run_id": "test",
        "task_count": 2,
        "htp_version": "1",
        "config": {
            "harness": {"id": "t", "adapter_level": "L1"},
            "mode": {"subject": "harness", "execution": "observe"},
            "models": {"harness": "m"},
            "containment": {"mechanism": "", "reach": ""},
        },
    }
    fp = {"status": "insufficient_evidence", "reason": "too few tasks"}
    text = summary_markdown(run, agg, fp)

    assert "Nothing. Every registered metric was computable" not in text
    assert "Computed from part of the task set" in text
    assert "cost_usd" in text


def test_summary_may_claim_full_coverage_when_it_is_true():
    per_task = [metrics_for("l1_full"), metrics_for("l1_full")]
    agg = M.aggregate(per_task)
    run = {
        "run_id": "test",
        "task_count": 2,
        "htp_version": "1",
        "config": {
            "harness": {"id": "t", "adapter_level": "L1"},
            "mode": {"subject": "harness", "execution": "observe"},
            "models": {"harness": "m"},
            "containment": {"mechanism": "", "reach": ""},
        },
    }
    fp = {"status": "insufficient_evidence", "reason": "too few tasks"}
    text = summary_markdown(run, agg, fp)
    # recovery_rate is unavailable in both, so it is still a gap, but nothing
    # is PARTIAL: every metric that was measured was measured in both tasks.
    assert "Computed from part of the task set" not in text
    assert "Not computable" in text
