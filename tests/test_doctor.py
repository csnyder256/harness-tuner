"""doctor: the list nobody writes, and the two ways it was wrong.

doctor's whole job is to say what this install cannot measure, before a report
is believed. That makes it the one surface where an overstatement is worse than
silence: an operator reads it to decide what they are giving up, and a setup
agent reads it aloud to them.

It was overstating in both directions at once, which is how it survived.

1. It under-reported what an L0 record costs. An L0 record is "which tools ran,
   in what order", which is doctor's own description of the level, and such a
   record cannot tell two calls to the same tool apart. So the four metrics
   that are statements about repeats report unavailable on it, and doctor
   listed none of them. The shipped ``l0_minimal`` fixture is the proof: it
   reports all four unavailable, while the README and the project page
   promised "loop detection ... still work[s]" at L0.

2. It said ``verify`` was unavailable below L2, on the reasoning that verify
   re-runs an identical task set. It does not: it reads two completed run
   directories and pairs them by task id. The command runs at L0 today, which
   the test at the bottom of this file does rather than describes.

The guard against (1) is mechanical. The metrics doctor blames on the adapter
level have to be the metrics the shipped L0 fixture reports unavailable, minus
the two that depend on what the task set declares rather than on what the
adapter can see. A hand-written list alone would have drifted the same way the
first one did.
"""

from __future__ import annotations

from harness_tuner import conformance as C
from harness_tuner.config import ADAPTER_LEVELS, Config
from harness_tuner.doctor import assess
from harness_tuner.protocol import REGISTRY, is_measured, normalize_trace
from harness_tuner.runner import execute
from harness_tuner.verify import run_verify

#: Metrics an L0 record cannot supply because it carries no arguments. Written
#: out by hand here, because the point of a hand-computed expectation is that it
#: does not come from the code it is checking. Identity is (tool, arguments), so
#: with the arguments unobserved every statement about repeats is unsupported.
ARGUMENT_DEPENDENT = frozenset(
    {"redundant_call_count", "redundant_call_ratio", "loop_max_run", "rework_ratio"}
)

#: Metrics the adapter level cannot supply but the task set can define. An L0
#: record is silent about outcomes, so ``task_success`` needs a deterministic
#: check or a judge verdict, and ``step_efficiency`` needs a declared
#: ``optimal_steps``. Neither is a fact about the adapter, so doctor must not
#: blame the level for them.
TASK_DECLARED = frozenset({"step_efficiency", "task_success"})


def cfg(level: str) -> Config:
    return Config(
        {
            "harness": {"id": "t", "adapter_level": level},
            "mode": {"subject": "harness", "execution": "observe"},
            "report": {"min_tasks_for_fingerprint": 1, "alpha": 0.05},
        },
        source="<test>",
    )


def l0_fixture_metrics() -> dict[str, dict]:
    """The metric block the shipped L0 fixture must produce."""
    fixtures = C.load_fixtures(C.suite_root())
    fixture = next(f for f in fixtures if f["name"] == "l0_minimal")
    return C.evaluate(fixture)


def blocked_metrics(level: str) -> list[dict[str, str]]:
    return [e for e in assess(cfg(level))["blocked"] if e["metric"] in REGISTRY]


# ---------------------------------------------------------------------------
# 1. What an L0 record actually costs
# ---------------------------------------------------------------------------


def test_every_metric_the_l0_fixture_cannot_measure_is_explained():
    """The defect this file exists for, pinned against the shipped fixture.

    doctor's L0 list has to account for every metric an L0 record loses, and it
    may not account for a metric the fixture measured. Both directions matter:
    under-reporting tells an operator they keep something they do not, and
    over-reporting invents a cost the level does not have.
    """
    unavailable = {name for name, entry in l0_fixture_metrics().items() if not is_measured(entry)}
    blamed = {entry["metric"] for entry in blocked_metrics("L0")}

    assert blamed <= unavailable, (
        "doctor blames the adapter level for metric(s) the L0 fixture measured: "
        f"{sorted(blamed - unavailable)}"
    )
    assert unavailable - blamed == TASK_DECLARED, (
        "doctor does not account for metric(s) the L0 fixture could not measure, other than "
        "the two the task set decides: "
        f"{sorted((unavailable - blamed) - TASK_DECLARED)}"
    )


def test_the_repeat_metrics_are_blamed_on_the_missing_arguments():
    """Hand-checked, and the reason has to name the field that closes the gap.

    On an L0 record, five consecutive calls to one tool and five calls to five
    different tools look identical, so a run count, a repeat count and a re-read
    share are all statements the record cannot support. Each reason is what a
    reader acts on, so each has to say which field to supply.
    """
    reasons = {entry["metric"]: entry["reason"] for entry in blocked_metrics("L0")}
    for metric in sorted(ARGUMENT_DEPENDENT):
        assert metric in reasons, f"{metric} needs the arguments and an L0 record has none"
        assert "args_digest" in reasons[metric], (
            f"the reason for {metric} has to name the field that closes it, got {reasons[metric]!r}"
        )
    # they are not lumped in with the metrics lost to missing tokens or timings,
    # which an L0 record also does not carry, because the remedy is different
    assert reasons["tokens_input"] != reasons["loop_max_run"]


def test_the_l0_list_names_only_real_metrics():
    for entry in blocked_metrics("L0"):
        assert entry["metric"] in REGISTRY


def test_levels_above_l0_blame_the_adapter_level_for_nothing():
    """L1 and above report tokens, timings and outcomes, so nothing is lost."""
    for level in ADAPTER_LEVELS:
        if level == "L0":
            continue
        assert blocked_metrics(level) == [], f"{level} should not lose metrics to its level"


# ---------------------------------------------------------------------------
# 2. verify is not gated on the adapter level
# ---------------------------------------------------------------------------


def test_verify_is_never_reported_as_blocked():
    """It reads two runs. It re-runs nothing, so no level gates it."""
    for level in ADAPTER_LEVELS:
        report = assess(cfg(level))
        assert not [e for e in report["blocked"] if "verify" in e["metric"]], (
            f"verify is reported as unavailable at {level}"
        )
        assert report["verify"], "the report must still say what the pairing step needs"


def test_the_verify_note_names_drive_mode_as_what_the_level_lacks():
    """The honest version of the old claim: the pair, not the command."""
    low = assess(cfg("L0"))["verify"]
    assert "re-runs nothing" in low
    assert "same task ids" in low
    assert "L2" in low, "the note has to say where the second run comes from"

    high = assess(cfg("L3"))["verify"]
    assert "can drive" in high


def test_verify_runs_on_two_observe_mode_runs_from_an_l0_adapter(tmp_path):
    """The executed proof that the old claim was false.

    Both runs come from ``execute`` in observe mode under an L0 configuration,
    which is what doctor used to call impossible. The task ids match because
    that is what an observe-mode pair has to have, and the comparison is made.
    """
    steps = normalize_trace(
        [
            {"kind": "tool_call", "tool": "read_file", "args": {"path": "a.py"},
             "result_kind": "unknown", "started_ms": 0, "duration_ms": 10},
            {"kind": "tool_call", "tool": "read_file", "args": {"path": "b.py"},
             "result_kind": "unknown", "started_ms": 10, "duration_ms": 10},
        ]
    )
    records = [
        {"task_id": f"t{i}", "tags": [], "task": {}, "steps": steps, "source": "test"}
        for i in range(4)
    ]

    configuration = cfg("L0")
    assert configuration.get("harness.adapter_level") == "L0"
    assert configuration.get("mode.execution") == "observe"

    baseline = execute(configuration, records, str(tmp_path / "before"))
    variant = execute(configuration, records, str(tmp_path / "after"))

    with open(baseline.path("run"), encoding="utf-8") as handle:
        assert '"L0"' in handle.read(), "the run has to record which level produced it"

    doc = run_verify(baseline.root, variant.root, metrics=["tool_call_count"])
    assert doc["tasks_paired"] == 4
    comparison = doc["comparisons"][0]
    assert comparison["mean_baseline"] == 2
    assert comparison["tasks_comparable"] == 4
    assert comparison["evidence"]["verdict"] in ("improvement", "regression", "undecided")
