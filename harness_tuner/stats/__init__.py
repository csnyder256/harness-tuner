"""Deciding whether a difference is real.

One module, :mod:`.eprocess`, implementing an anytime-valid sequential test.
The guarantee it provides is that looking at the running result after every
single trial, and stopping whenever you like, does not inflate the error rate.
That is not a convenience. It is the only honest way to run a test whose trials
cost money and whose operator is going to watch it.
"""

from .eprocess import (
    IMPROVEMENT,
    LAMBDA_GRID,
    REGRESSION,
    UNDECIDED,
    e_value,
    outcomes_from_pairs,
    test,
    trajectory,
)

__all__ = [
    "IMPROVEMENT",
    "LAMBDA_GRID",
    "REGRESSION",
    "UNDECIDED",
    "e_value",
    "outcomes_from_pairs",
    "test",
    "trajectory",
]
