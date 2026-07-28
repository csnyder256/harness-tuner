"""Proposals: what to change, and how you will know whether it worked.

harness-tuner never edits your harness. It writes a proposal, you decide, and
then ``verify`` measures what actually happened. That leaves one obligation on
this module, and it is the whole reason it exists: every proposal must be
falsifiable before you apply it.

So a proposal carries three things.

**A prediction**, as a metric plus a direction. Not a percentage. The engine
knows what a trace contains; it does not know what your harness will do after
you change it, and a forecast dressed as a number would be the exact kind of
unfalsifiable claim this project is a reaction to.

**A bound**, where the data supports one. "At most 9 of 35 calls could be
removed" is arithmetic on the observed trace, not a forecast, and it is the
honest way to say how much is at stake.

**The command that would refute it.** Printed with the proposal, not buried in
documentation, because a prediction nobody can conveniently check is a
prediction nobody checks.

The change itself is described in terms of behaviour rather than configuration.
harness-tuner does not know whether your harness spells its context policy as a
YAML key, a system prompt paragraph, a hook, or a hard-coded constant. The
diagnostician model, which has read your harness, is the thing that turns
"stop re-reading files it already read" into an edit to your actual file.
"""

from __future__ import annotations

from typing import Any

#: kind -> (what to change, what usually causes it)
_REMEDIES: dict[str, tuple[str, str]] = {
    "loop": (
        "Give the harness a way to notice it has just made this exact call. A guard that "
        "compares the current tool and arguments against the previous few steps, and either "
        "blocks the repeat or surfaces the earlier result, is usually enough.",
        "Loops of identical calls almost always mean the result was not retained in a form "
        "the next turn could see, so the model asks again.",
    ),
    "rework": (
        "Keep what has already been read available to later turns, rather than re-reading it. "
        "A per-task record of what was fetched, injected into context or exposed as a tool the "
        "model can consult, removes the need for the repeat.",
        "The model has no memory of a read whose result scrolled out of the window, so from "
        "its point of view the file has not been read yet.",
    ),
    "cache": (
        "Make the stable part of every request byte-identical and put it first, so a prompt "
        "cache can actually hit. Anything that varies per turn, timestamps, a shuffled tool "
        "list, a changing preamble, has to move after the stable block.",
        "A cache miss on every turn usually means something small and volatile sits in front "
        "of a large stable prefix.",
    ),
    "context": (
        "Introduce or tighten compaction, so the window stops growing linearly with the number "
        "of steps. Summarising completed work and dropping superseded tool output is the "
        "usual shape.",
        "A harness that appends every turn's output forever grows until it hits a limit, and "
        "the last steps of a long task are the most expensive ones.",
    ),
    "recovery": (
        "Give failures an explicit retry path with the error text in front of the model, rather "
        "than letting the agent wander to a different tool after a failure.",
        "The work of discovering a problem is expensive; abandoning it means paying for that "
        "discovery twice.",
    ),
    "budget": (
        "Either supply the data the ceiling needs, by reporting per-step cost or tokens from "
        "the adapter, or turn the ceiling off. Leaving it configured and inert is worse than "
        "having none, because an operator is relying on it.",
        "A ceiling is only as real as the measurements feeding it.",
    ),
    "measurement": (
        "Raise the adapter level. Each unavailable metric names the field it needed, so the "
        "gaps are a work list rather than a mystery.",
        "Nothing here is a defect in the harness. It is a limit on what this adapter can see.",
    ),
}


def _bound(finding: dict[str, Any]) -> str | None:
    """A ceiling on the gain, computed from the observed trace. Never a forecast."""
    measured = finding.get("measured") or {}
    kind = finding["kind"]
    if kind == "loop":
        n = measured.get("consecutive_calls")
        if n:
            return (
                f"At most {n - 1} of those {n} calls were avoidable, since one of them had to "
                "happen. This is arithmetic on the observed trace, not a prediction."
            )
    if kind == "rework":
        n = measured.get("repeated_reads")
        if n:
            return (
                f"At most {n} read(s) were avoidable across the run, because each had already "
                "been performed earlier in the same task."
            )
    if kind == "cache":
        tokens = measured.get("tokens_input")
        if tokens:
            return (
                f"{tokens:,} input token(s) were billed uncached. How many of those are "
                "cacheable depends on how much of the prompt is genuinely stable, which the "
                "trace cannot tell you."
            )
    if kind == "context":
        slope = measured.get("slope")
        peak = measured.get("peak")
        if slope and peak:
            return (
                f"Context reached {peak:,} tokens growing at about {slope:,.0f} per step. "
                "Compaction cannot reduce it below what the task genuinely needs in view."
            )
    return None


def from_finding(finding: dict[str, Any], index: int) -> dict[str, Any] | None:
    """Turn one finding into a proposal, or None when there is nothing to propose."""
    remedy = _REMEDIES.get(finding["kind"])
    if remedy is None:
        return None
    change, cause = remedy

    proposal_id = f"P{index:02d}"
    prediction = None
    if finding.get("metric") and finding.get("direction"):
        prediction = {
            "proposal_id": proposal_id,
            "metric": finding["metric"],
            "direction": finding["direction"],
            "note": finding["title"],
        }

    return {
        "proposal_id": proposal_id,
        "finding_id": finding["id"],
        "severity": finding["severity"],
        "title": finding["title"],
        "why": cause,
        "change": change,
        "applies_to_tasks": finding.get("tasks") or [],
        "evidence": finding.get("evidence") or [],
        "measured": finding.get("measured") or {},
        "bound": _bound(finding),
        "prediction": prediction,
        "falsified_by": (
            "Apply the change, re-run the identical task set, then run: "
            "harness-tuner verify --baseline <this run> --variant <the new run>. "
            "If the measured direction contradicts the prediction with evidence past the "
            "alpha bar, the proposal was wrong."
            if prediction
            else "This proposal makes no measurable prediction, so verify has nothing to check. "
            "It is reported as an observation rather than a change to test."
        ),
        "applied_by": "a human. harness-tuner does not edit your harness.",
    }


def build(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    proposals = []
    for finding in findings:
        proposal = from_finding(finding, len(proposals) + 1)
        if proposal is not None:
            proposals.append(proposal)
    return proposals


def predictions(proposals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The prediction block written into the run manifest for verify to score."""
    return [p["prediction"] for p in proposals if p.get("prediction")]
