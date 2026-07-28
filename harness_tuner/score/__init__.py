"""Deciding whether a task was accomplished.

Three sources, in order of preference:

- a deterministic check declared in the pack, which costs nothing and cannot
  drift,
- a verdict from the judge, obtained through :mod:`.delegated` by default so
  the engine holds no credentials,
- nothing, in which case ``task_success`` reports unavailable.

The third case is the one worth stating plainly. An unjudged task is never
assumed to have passed. An agent that stops early leaves a trace that looks
much like success, so inferring the outcome from the trace is exactly the
mistake that would make every other number in the report untrustworthy.
"""

from . import delegated, direct

__all__ = ["delegated", "direct"]
