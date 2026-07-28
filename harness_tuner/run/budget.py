"""The budget governor.

A ceiling that halts, not a warning that gets ignored. The distinction matters
because the failure this guards against is an agent that loops until the money
is gone, and by the time a warning is read the money is gone.

Two design choices are deliberate.

A halted run still writes all eight artifacts. Stopping is a result, not a
crash, and the partial measurements taken before the ceiling are usually the
most interesting thing about the run. The halt is recorded in ``run.json`` with
what was spent and which task tripped it, so nobody mistakes a truncated run
for a complete one.

The governor charges only what the adapter actually reported. If a harness
reports no cost, the dollar ceiling cannot fire, and the governor says so
rather than pretending the run was free. A ceiling that silently never fires is
worse than no ceiling.
"""

from __future__ import annotations

from typing import Any


class BudgetExceeded(RuntimeError):
    """A ceiling was reached. Carries the state at the moment of the halt."""

    def __init__(self, message: str, state: dict[str, Any]):
        super().__init__(message)
        self.state = state


class Governor:
    """Tracks spend against the configured ceilings."""

    def __init__(self, *, enabled: bool, max_usd: float, max_tokens: int):
        self.enabled = bool(enabled)
        self.max_usd = float(max_usd or 0.0)
        self.max_tokens = int(max_tokens or 0)
        self.spent_usd = 0.0
        self.spent_tokens = 0
        self.tasks_charged = 0
        self.cost_reported = False
        self.tokens_reported = False
        self.halted_at: str | None = None
        self.halt_reason: str | None = None

    @classmethod
    def from_config(cls, cfg) -> "Governor":
        return cls(
            enabled=cfg.get("cost.budget.enabled"),
            max_usd=cfg.get("cost.budget.max_usd"),
            max_tokens=cfg.get("cost.budget.max_tokens"),
        )

    def charge(self, *, cost_usd: float | None, tokens: int | None, task_id: str) -> None:
        """Record what one task cost, then enforce the ceilings.

        Raises :class:`BudgetExceeded` when a ceiling is reached. The charge is
        applied before the check, so the recorded spend includes the task that
        tripped it rather than understating the real total.
        """
        self.tasks_charged += 1
        if cost_usd is not None:
            self.spent_usd += float(cost_usd)
            self.cost_reported = True
        if tokens is not None:
            self.spent_tokens += int(tokens)
            self.tokens_reported = True

        if not self.enabled:
            return

        if self.max_usd > 0 and self.spent_usd >= self.max_usd:
            self.halted_at = task_id
            self.halt_reason = (
                f"spend reached {self.spent_usd:.6f} USD against a ceiling of {self.max_usd:.6f}"
            )
            raise BudgetExceeded(self.halt_reason, self.state())

        if self.max_tokens > 0 and self.spent_tokens >= self.max_tokens:
            self.halted_at = task_id
            self.halt_reason = (
                f"token use reached {self.spent_tokens} against a ceiling of {self.max_tokens}"
            )
            raise BudgetExceeded(self.halt_reason, self.state())

    def blind_spots(self) -> list[str]:
        """Ceilings that are configured but cannot possibly fire.

        Reported rather than silently tolerated. A dollar ceiling on a harness
        whose adapter reports no cost is a promise the operator is relying on
        and the engine cannot keep.
        """
        problems = []
        if not self.enabled:
            return problems
        if self.max_usd > 0 and self.tasks_charged and not self.cost_reported:
            problems.append(
                f"a spend ceiling of {self.max_usd} USD is configured, but no task reported a "
                "cost, so this ceiling cannot fire. Supply per-step cost from the adapter."
            )
        if self.max_tokens > 0 and self.tasks_charged and not self.tokens_reported:
            problems.append(
                f"a token ceiling of {self.max_tokens} is configured, but no task reported "
                "token counts, so this ceiling cannot fire. This adapter is below level L1."
            )
        if self.max_usd <= 0 and self.max_tokens <= 0:
            problems.append(
                "the budget governor is enabled but both ceilings are zero, so nothing is "
                "capped. Set cost.budget.max_usd or cost.budget.max_tokens."
            )
        return problems

    def state(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "max_usd": self.max_usd,
            "max_tokens": self.max_tokens,
            "spent_usd": round(self.spent_usd, 8),
            "spent_tokens": self.spent_tokens,
            "tasks_charged": self.tasks_charged,
            "cost_was_reported": self.cost_reported,
            "tokens_were_reported": self.tokens_reported,
            "halted_at_task": self.halted_at,
            "halt_reason": self.halt_reason,
            "blind_spots": self.blind_spots(),
        }
