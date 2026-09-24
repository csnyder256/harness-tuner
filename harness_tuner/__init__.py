"""harness-tuner: measure an agent harness, then prove a change to it helped.

The reference implementation of HTP-1. Standard library only, so it runs
wherever Python 3.11 or newer runs and a stranger's coding agent can read and
extend it in place.

The optional direct-model-call path in :mod:`harness_tuner.score.direct` is the
only module permitted to import anything outside the standard library, and it
is written so that its absence degrades to the delegated path rather than
breaking the install.
"""

__version__ = "0.2.1"
__all__ = ["__version__"]
