"""What every run artifact records about the conditions that produced it.

This deliberately does not live in the config package. If it did, the wiring
check in ``tools/check_config_wiring.py`` would count a setting as consumed
merely because the config module echoed it back to itself, and every dead
setting in the project would pass. Keeping the stamp here means the check sees
a real read from outside.

A run has to be self-describing. Six months later, a report is only
interpretable if it says which model was under test, what the run was allowed
to reach, which judge scored it, and where the task set came from. Two runs
that differ in any of those are not comparable, and the artifacts are the only
place that can be established.
"""

from __future__ import annotations

from typing import Any

from .config import Config


def stamp(cfg: Config) -> dict[str, Any]:
    """The settings block written into every run artifact."""
    return {
        "harness": {
            "id": cfg.get("harness.id"),
            "adapter": cfg.get("harness.adapter"),
            "adapter_level": cfg.get("harness.adapter_level"),
            "timeout_s": cfg.get("harness.timeout_s"),
            "workspace": cfg.get("harness.workspace"),
        },
        "mode": {
            "subject": cfg.get("mode.subject"),
            "execution": cfg.get("mode.execution"),
        },
        "models": {
            "harness": cfg.get("models.harness"),
            "judge": cfg.get("models.judge"),
            "diagnostician": cfg.get("models.diagnostician"),
            "generator": cfg.get("models.generator"),
            "call_mode": cfg.get("models.call_mode"),
        },
        "containment": {
            "mechanism": cfg.get("containment.mechanism"),
            "declared_by": cfg.get("containment.declared_by"),
            "reach": cfg.get("containment.reach"),
            "approved": cfg.get("containment.approved"),
        },
        "cost": {
            "cassette": cfg.get("cost.cassette"),
            "cassette_path": cfg.get("cost.cassette_path"),
            "local_endpoint": cfg.get("cost.local_endpoint"),
            "budget": {
                "enabled": cfg.get("cost.budget.enabled"),
                "max_usd": cfg.get("cost.budget.max_usd"),
                "max_tokens": cfg.get("cost.budget.max_tokens"),
            },
        },
        "tasks": {
            "packs": cfg.get("tasks.packs"),
            "generated_dir": cfg.get("tasks.generated_dir"),
            "seed": cfg.get("tasks.seed"),
        },
    }
