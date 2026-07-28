"""Anytime-valid sequential testing.

The problem this solves is specific and unavoidable. Running the same harness
on the same tasks twice gives different numbers, so any claim that a change
helped is a claim about a difference between two noisy samples. The usual way
to handle that is a fixed sample size chosen in advance and a p-value computed
once at the end. That is a bad fit here for two reasons: trials cost real money,
so you want to stop the moment the answer is obvious, and the person running
this is going to look at the running result whether or not the method permits
it. With a p-value, looking and then deciding to continue inflates the error
rate, silently.

An e-process removes the problem rather than asking people not to peek.

The construction is a betting martingale. Each paired trial is a bet on the
variant. A win multiplies the wealth by ``1 + lambda``, a loss by
``1 - lambda``, a tie leaves it alone::

    E_n = product over trials of (1 + lambda * X_i),  X_i in {+1, 0, -1}

Under the null hypothesis that the variant is not better, wins are no more
likely than losses, so each factor has expectation at most 1 and ``E_n`` is a
non-negative supermartingale. Ville's inequality then says the probability that
it EVER reaches ``1/alpha``, at any stopping time, checked as often as you like,
is at most ``alpha``. So the rule "stop and call it when E >= 1/alpha" has the
error guarantee regardless of when or how often you look.

``lambda`` is not tuned, because tuning it on the data would break the
guarantee. Instead a fixed grid of bets is run in parallel and averaged. An
average of supermartingales is a supermartingale, so the mixture keeps the
guarantee while performing close to whichever fixed bet would have been best.

What is reported is the e-value, never a p-value. An e-value of 20 means the
evidence against the null is worth 20 to 1. That is a betting statement, and
it is the honest one.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

WIN = 1
TIE = 0
LOSS = -1

#: The fixed grid of bets. Every value is strictly below 1 so that a loss can
#: never drive the wealth to zero or negative, which would end the process.
LAMBDA_GRID: tuple[float, ...] = (0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.75, 0.9)

IMPROVEMENT = "improvement"
REGRESSION = "regression"
UNDECIDED = "undecided"


def outcomes_from_pairs(
    pairs: Iterable[tuple[float | bool | None, float | bool | None]],
    *,
    lower_is_better: bool,
) -> list[int]:
    """Turn paired (baseline, variant) measurements into wins, losses and ties.

    A pair where either side is missing is dropped rather than counted as a
    tie. Counting it as a tie would dilute real evidence with absence of data.
    """
    out: list[int] = []
    for baseline, variant in pairs:
        if baseline is None or variant is None:
            continue
        b = float(baseline)
        v = float(variant)
        if b == v:
            out.append(TIE)
        elif (v < b) if lower_is_better else (v > b):
            out.append(WIN)
        else:
            out.append(LOSS)
    return out


def e_value(outcomes: Sequence[int], lambdas: Sequence[float] = LAMBDA_GRID) -> float:
    """The mixture e-value for the hypothesis that the variant IS better.

    Returns 1.0 for an empty sequence, which is exactly right: no trials is no
    evidence, and 1.0 is the neutral wealth.
    """
    if not outcomes:
        return 1.0
    total = 0.0
    for lam in lambdas:
        wealth = 1.0
        for x in outcomes:
            wealth *= 1.0 + lam * x
            if wealth == 0.0:
                break
        total += wealth
    return total / len(lambdas)


def trajectory(outcomes: Sequence[int], lambdas: Sequence[float] = LAMBDA_GRID) -> list[float]:
    """The running e-value after each trial, for plotting or for a log."""
    wealths = [1.0] * len(lambdas)
    out: list[float] = []
    for x in outcomes:
        for i, lam in enumerate(lambdas):
            wealths[i] *= 1.0 + lam * x
        out.append(sum(wealths) / len(wealths))
    return out


def test(outcomes: Sequence[int], *, alpha: float = 0.05) -> dict[str, Any]:
    """Run the test in both directions and report what the evidence licenses.

    Both directions are always computed. A change that made things measurably
    worse is at least as important to report as one that helped, and a tool
    that only ever looked for improvement would find one eventually.
    """
    if not 0 < alpha < 1:
        raise ValueError(f"alpha must be between 0 and 1, got {alpha}")
    threshold = 1.0 / alpha

    forward = e_value(outcomes)
    backward = e_value([-x for x in outcomes])

    wins = sum(1 for x in outcomes if x == WIN)
    losses = sum(1 for x in outcomes if x == LOSS)
    ties = sum(1 for x in outcomes if x == TIE)

    if forward >= threshold and forward >= backward:
        verdict = IMPROVEMENT
        detail = (
            f"the evidence that this change helps is worth {forward:.2f} to 1, which clears "
            f"the {threshold:.0f} to 1 bar set by alpha={alpha}."
        )
    elif backward >= threshold:
        verdict = REGRESSION
        detail = (
            f"the evidence that this change HURTS is worth {backward:.2f} to 1, which clears "
            f"the {threshold:.0f} to 1 bar set by alpha={alpha}."
        )
    else:
        verdict = UNDECIDED
        best = max(forward, backward)
        detail = (
            f"the strongest evidence in either direction is worth {best:.2f} to 1, short of "
            f"the {threshold:.0f} to 1 bar. Nothing is called. Because this is an "
            "anytime-valid test, adding more trials and looking again is legitimate and "
            "does not inflate the error rate."
        )

    return {
        "method": "mixture betting e-process, paired trials",
        "verdict": verdict,
        "detail": detail,
        "alpha": alpha,
        "threshold": threshold,
        "e_value_improvement": round(forward, 6),
        "e_value_regression": round(backward, 6),
        "trials": len(outcomes),
        "wins": wins,
        "losses": losses,
        "ties": ties,
        "lambda_grid": list(LAMBDA_GRID),
        "reports": (
            "an e-value, not a p-value. An e-value of 20 means the evidence is worth 20 to 1 "
            "against the null. Peeking at it after every trial is safe by construction."
        ),
    }
