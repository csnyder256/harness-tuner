"""The specification must actually specify.

AGENT-GUIDE.md Part 2 is the normative protocol, and the third install rung
asks a stranger's agent to implement it on a machine where the reference
implementation cannot run. That only works if the guide is self-sufficient.

The first version of the guide was not. Section 2.2 said "their exact
definitions live in harness_tuner/protocol/metrics.py, which is normative" and
named one metric out of twenty-four. Everything read as complete: the section
existed, it explained the shape of a measure, it explained the combiners, and
the rung-3 instructions pointed confidently at it. A reimplementer following it
would have got as far as needing to know what rework_ratio meant and then had
to read the Python, which is exactly what rung 3 exists because you cannot do.

These tests make that class of drift mechanical. A metric added to the code
without being written into the guide fails here.
"""

from __future__ import annotations

import os
import re

from harness_tuner.protocol import AGGREGATION, ARTIFACT_NAMES, LOWER_IS_BETTER, REGISTRY
from harness_tuner.protocol.trace import RESULT_KINDS, STEP_KINDS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GUIDE_PATH = os.path.join(ROOT, "AGENT-GUIDE.md")

with open(GUIDE_PATH, "r", encoding="utf-8") as _handle:
    GUIDE = _handle.read()


def metric_table() -> dict[str, tuple[str, str, str]]:
    """Parse the metric table out of the guide: name -> (unit, combiner, better)."""
    rows: dict[str, tuple[str, str, str]] = {}
    for line in GUIDE.splitlines():
        match = re.match(
            r"^\|\s*`([a-z_]+)`\s*\|\s*([^|]+?)\s*\|\s*(sum|max|mean|rate)\s*\|\s*(lower|higher)\s*\|",
            line,
        )
        if match:
            rows[match.group(1)] = (match.group(2), match.group(3), match.group(4))
    return rows


def test_every_metric_is_specified_in_the_guide():
    """The defect this file exists for."""
    documented = metric_table()
    missing = sorted(set(REGISTRY) - set(documented))
    assert not missing, (
        f"{len(missing)} metric(s) are computed but not specified in AGENT-GUIDE.md: {missing}. "
        "Rung 3 asks a stranger to implement Part 2 without reading the code, so a metric "
        "that only exists in the code cannot be reimplemented."
    )


def test_the_guide_invents_no_metric():
    documented = metric_table()
    extra = sorted(set(documented) - set(REGISTRY))
    assert not extra, f"the guide specifies metric(s) the engine does not compute: {extra}"


def test_the_guide_agrees_with_the_code_on_combiners():
    """A wrong combiner in the guide would produce a conformant-looking, wrong implementation."""
    documented = metric_table()
    for name, (_unit, combiner, _better) in documented.items():
        assert combiner == AGGREGATION[name], (
            f"{name}: guide says combiner {combiner!r}, code says {AGGREGATION[name]!r}"
        )


def test_the_guide_agrees_with_the_code_on_direction():
    documented = metric_table()
    for name, (_unit, _combiner, better) in documented.items():
        expected = "lower" if name in LOWER_IS_BETTER else "higher"
        assert better == expected, f"{name}: guide says {better!r} is better, code says {expected!r}"


def test_the_guide_agrees_with_the_code_on_units():
    """An implementation reporting the right number in the wrong unit is not conformant."""
    import harness_tuner.conformance as C

    actual_units: dict[str, str] = {}
    for fixture in C.load_fixtures(C.suite_root()):
        for name, entry in C.evaluate(fixture).items():
            actual_units.setdefault(name, entry["unit"])

    documented = metric_table()
    for name, (unit, _combiner, _better) in documented.items():
        assert unit == actual_units[name], (
            f"{name}: guide says unit {unit!r}, engine emits {actual_units[name]!r}"
        )


def test_every_artifact_name_appears_in_the_guide():
    for name in ARTIFACT_NAMES:
        assert name in GUIDE, f"artifact {name} is not named in AGENT-GUIDE.md"


def test_every_step_kind_and_result_kind_is_documented():
    for kind in STEP_KINDS:
        assert f"`{kind}`" in GUIDE, f"step kind {kind} is not documented in AGENT-GUIDE.md"
    for kind in RESULT_KINDS:
        assert f"`{kind}`" in GUIDE, f"result kind {kind} is not documented in AGENT-GUIDE.md"


def test_rung_three_does_not_require_the_reference_implementation():
    """The whole point of rung 3 is a machine where the reference cannot run.

    Telling that reader to "verify by running the reference implementation" is
    telling them to use the thing they do not have. Both places that describe
    rung 3 are checked, because the first fix landed in Appendix C only and the
    identical defect survived untouched in the rung-selection section, where
    most readers meet it first.
    """
    appendix = GUIDE[GUIDE.index("## Appendix C."):]
    assert "you do not need to read the reference implementation" in appendix
    assert "conformance suite is plain data, not a program" in appendix
    assert "needs nothing from this repository" in appendix

    rungs = GUIDE[GUIDE.index("## Before you start: pick a rung"):GUIDE.index("# PART 1.")]
    assert "**Rung 3" in rungs
    rung3 = rungs[rungs.index("**Rung 3"):]
    assert "plain data rather than a program" in rung3
    assert "needs nothing from this repository" in rung3
    assert "cannot run it" in rung3
    assert "python -m harness_tuner conformance" not in rung3, (
        "the rung-3 section tells a reader with no usable Python to run the reference "
        "implementation, which is the one thing that rung exists because they cannot do"
    )


def test_the_fixture_count_in_the_guide_matches_the_suite():
    """A stated count that drifts is how a reimplementer stops early."""
    import harness_tuner.conformance as C

    n = len(C.load_fixtures(C.suite_root()))
    words = {10: "ten", 11: "eleven", 12: "twelve", 13: "thirteen"}
    assert n in words, f"add {n} to the number-word map in this test"
    word = words[n]
    assert f"{word} fixture" in GUIDE or f"{word} files in `conformance/fixtures/`" in GUIDE, (
        f"the suite has {n} fixtures but AGENT-GUIDE.md does not say {word!r}"
    )
    for wrong in set(words.values()) - {word}:
        assert f"all {wrong} fixtures" not in GUIDE, f"stale fixture count {wrong!r} in the guide"
        assert f"{wrong} files in `conformance/fixtures/`" not in GUIDE, (
            f"stale fixture count {wrong!r} in the guide"
        )


def test_the_guide_states_the_null_rule_prominently():
    """The one rule that, if misunderstood, produces a convincing wrong report."""
    assert "is `null`. Never zero" in GUIDE or "is `null`, never zero" in GUIDE.lower()
    assert "never an estimate" in GUIDE


def test_every_config_setting_appears_in_the_reference_appendix():
    from harness_tuner.config import SCHEMA

    appendix = GUIDE[GUIDE.index("## Appendix B."):]
    missing = [key.path for key in SCHEMA if key.path.split(".")[-1] not in appendix]
    assert not missing, f"settings absent from the configuration reference: {missing}"
