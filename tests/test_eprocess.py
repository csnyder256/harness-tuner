"""The e-process, including the guarantee it exists to provide.

The important test in this file is the calibration one. Everything else checks
that the arithmetic behaves; that one checks that the promise on the tin is
kept. If a tool claims you can watch a running comparison after every trial and
stop whenever you like without inflating the error rate, that claim has to be
measured, not asserted in a docstring.
"""

from __future__ import annotations

import random

from harness_tuner.stats import eprocess as E

ALPHA = 0.05
THRESHOLD = 1 / ALPHA


def test_no_trials_is_neutral_evidence():
    assert E.e_value([]) == 1.0
    result = E.test([], alpha=ALPHA)
    assert result["verdict"] == E.UNDECIDED
    assert result["trials"] == 0


def test_ties_move_nothing():
    assert E.e_value([E.TIE] * 50) == 1.0


def test_consistent_wins_accumulate_evidence():
    result = E.test([E.WIN] * 12, alpha=ALPHA)
    assert result["verdict"] == E.IMPROVEMENT
    assert result["e_value_improvement"] >= THRESHOLD
    assert result["wins"] == 12 and result["losses"] == 0


def test_consistent_losses_are_called_a_regression():
    """A tool that only ever looked for improvement would eventually find one."""
    result = E.test([E.LOSS] * 12, alpha=ALPHA)
    assert result["verdict"] == E.REGRESSION
    assert result["e_value_regression"] >= THRESHOLD


def test_a_coin_flip_is_not_called():
    outcomes = [E.WIN, E.LOSS] * 100
    result = E.test(outcomes, alpha=ALPHA)
    assert result["verdict"] == E.UNDECIDED
    assert result["e_value_improvement"] < THRESHOLD
    assert result["e_value_regression"] < THRESHOLD


def test_evidence_is_monotone_in_a_run_of_wins():
    traj = E.trajectory([E.WIN] * 10)
    assert traj == sorted(traj)
    assert traj[-1] > traj[0]


def test_the_running_value_matches_the_final_value():
    outcomes = [E.WIN, E.WIN, E.LOSS, E.WIN, E.TIE, E.WIN]
    assert E.trajectory(outcomes)[-1] == E.e_value(outcomes)


def test_a_loss_can_never_zero_the_wealth():
    """Every bet in the grid must stay strictly below 1."""
    assert all(0 < lam < 1 for lam in E.LAMBDA_GRID)
    assert E.e_value([E.LOSS] * 500) > 0


# ---------------------------------------------------------------------------
# The guarantee. This is the test the whole module exists to pass.
# ---------------------------------------------------------------------------


def test_peeking_after_every_trial_does_not_inflate_the_error_rate():
    """Measured calibration under the null, checked at every prefix.

    The null here is the honest one for a paired comparison: the variant is no
    better than the baseline, so wins and losses are equally likely. The test
    counts a false positive if the e-value EVER crosses the threshold at any
    point in the sequence, which is precisely the freedom the method claims to
    give. Checking only the final value would test a much weaker property and
    would pass even for a method that has no anytime guarantee at all.

    Ville's inequality bounds this at alpha. The assertion allows headroom for
    simulation noise over 1000 runs while still failing loudly if the rate is
    anywhere near, say, the 30 percent a naively-peeked p-value would produce.
    """
    rng = random.Random(20260728)
    trials_per_run = 150
    runs = 1000

    crossed = 0
    for _ in range(runs):
        outcomes = [rng.choice((E.WIN, E.LOSS)) for _ in range(trials_per_run)]
        if max(E.trajectory(outcomes)) >= THRESHOLD:
            crossed += 1

    rate = crossed / runs
    assert rate <= ALPHA * 2, (
        f"false positive rate under the null was {rate:.3f} over {runs} runs, "
        f"which is not consistent with an alpha of {ALPHA}"
    )


def test_a_real_effect_is_detected_and_usually_early():
    """A variant that wins 80 percent of paired trials should be called."""
    rng = random.Random(4242)
    detections = 0
    stopping_points = []
    for _ in range(200):
        outcomes = [E.WIN if rng.random() < 0.8 else E.LOSS for _ in range(60)]
        traj = E.trajectory(outcomes)
        for i, value in enumerate(traj, start=1):
            if value >= THRESHOLD:
                detections += 1
                stopping_points.append(i)
                break

    assert detections >= 190, f"only {detections} of 200 real effects were detected"
    median = sorted(stopping_points)[len(stopping_points) // 2]
    assert median <= 30, (
        f"median stopping point was {median} trials; the point of stopping early is to "
        "spend less than a fixed-sample design would"
    )


# ---------------------------------------------------------------------------
# Turning measurements into trials
# ---------------------------------------------------------------------------


def test_direction_is_respected():
    pairs = [(10.0, 5.0), (10.0, 20.0), (10.0, 10.0)]
    assert E.outcomes_from_pairs(pairs, lower_is_better=True) == [E.WIN, E.LOSS, E.TIE]
    assert E.outcomes_from_pairs(pairs, lower_is_better=False) == [E.LOSS, E.WIN, E.TIE]


def test_a_missing_side_is_dropped_not_counted_as_a_tie():
    """Counting absence of data as a tie would dilute real evidence with nothing."""
    pairs = [(1.0, None), (None, 2.0), (5.0, 3.0)]
    outcomes = E.outcomes_from_pairs(pairs, lower_is_better=True)
    assert outcomes == [E.WIN]
    assert len(outcomes) == 1


def test_booleans_work_as_outcomes():
    pairs = [(False, True), (True, False), (True, True)]
    assert E.outcomes_from_pairs(pairs, lower_is_better=False) == [E.WIN, E.LOSS, E.TIE]


def test_alpha_is_validated():
    import pytest

    for bad in (0, 1, -0.1, 1.5):
        with pytest.raises(ValueError, match="alpha must be between"):
            E.test([E.WIN], alpha=bad)


def test_the_result_says_it_reports_an_e_value():
    result = E.test([E.WIN] * 3, alpha=ALPHA)
    assert "e-value, not a p-value" in result["reports"]
    assert "p_value" not in result
