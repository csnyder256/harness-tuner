"""The negotiated surface.

Everything here is decided with the user during setup, by an agent that read
their harness. None of it is guessed by the engine and none of it has a
prescribed answer, which is the point: a harness evaluator that assumed your
containment, your models, or your execution mode would be measuring its own
assumptions.

Config is read through :meth:`Config.get` with a dotted path. That is not
decoration. It means every consumer of a setting contains the setting's literal
path as a string, so ``tools/check_config_wiring.py`` can prove mechanically
that no key is accepted, validated, documented, and then read by nothing. A
setting that validates but does nothing is worse than a missing one, because
the operator relies on it.
"""

from __future__ import annotations

import os
import tomllib
from typing import Any, NamedTuple

CONFIG_FILENAME = "harness-tuner.toml"

ADAPTER_LEVELS = ("L0", "L1", "L2", "L3")
SUBJECTS = ("harness", "agent", "both")
EXECUTIONS = ("drive", "observe", "both")


class Key(NamedTuple):
    path: str
    kind: type | tuple[type, ...]
    default: Any
    help: str


#: Every setting harness-tuner understands. Adding a row here without also
#: adding a consumer is a defect the wiring check will fail on.
SCHEMA: tuple[Key, ...] = (
    Key("harness.id", str, "", "A name for the harness under test. Stamped into every artifact."),
    Key("harness.adapter", str, "", "Path to the adapter your agent wrote for this harness."),
    Key("harness.adapter_level", str, "L0", "L0 observe, L1 structured, L2 drivable, L3 controllable."),
    Key("harness.invoke", list, [], "Argv template used to drive the harness on one task. L2 and above."),
    Key("harness.timeout_s", int, 0, "Seconds one driven task may take before it counts as hung. 0 waits forever."),
    Key("harness.workspace", str, "", "Directory a driven run executes in. Whatever your containment answer implies."),
    Key("harness.read_tool_hints", list, [], "Substrings that identify read-shaped tools in this harness."),
    Key("mode.subject", str, "harness", "What is under test: harness, agent, or both."),
    Key("mode.execution", str, "observe", "drive, observe, or both."),
    Key("models.harness", str, "", "The model the harness itself runs on. Comparisons are void across changes to it."),
    Key("models.judge", str, "", "Scores open-ended outcomes. Runs most often, so cost matters most here."),
    Key("models.diagnostician", str, "", "Reads whole traces and writes the findings and proposals."),
    Key("models.generator", str, "", "Turns the harness's real job into a seeded task set."),
    Key("models.call_mode", str, "delegated", "delegated (your agent calls models) or direct (the engine does)."),
    Key("containment.mechanism", str, "", "Whatever isolation this machine actually supports. No default is prescribed."),
    Key("containment.declared_by", str, "user", "agent if the agent proposed it, user if the human chose it."),
    Key("containment.reach", str, "", "Plain description of what a driven run can touch. Stamped into every artifact."),
    Key("containment.approved", bool, False, "Whether the human approved driving under this containment."),
    Key("cost.cassette", bool, True, "Record driven task outcomes once, then replay free and deterministically."),
    Key("cost.cassette_path", str, "harness-tuner.cassette.jsonl", "Where the recording lives."),
    Key("cost.local_endpoint", str, "", "Base URL of a local model server, when a zero-cost tier is available."),
    Key("cost.budget.enabled", bool, True, "Halt a run when the ceiling is reached."),
    Key("cost.budget.max_usd", float, 0.0, "Hard spend ceiling for one run. 0 means no dollar ceiling."),
    Key("cost.budget.max_tokens", int, 0, "Hard token ceiling for one run. 0 means no token ceiling."),
    Key("tasks.packs", list, [], "Shipped packs to run, by name."),
    Key("tasks.generated_dir", str, "", "Directory holding the task set generated for this harness's real job."),
    Key("tasks.seed", int, 0, "Seed recorded in the manifest so verify can reproduce the same task set."),
    Key("report.min_tasks_for_fingerprint", int, 3, "Below this many tasks a fingerprint reports insufficient evidence."),
    Key("report.alpha", float, 0.05, "Error level the e-process must clear before a difference is called."),
)

SCHEMA_BY_PATH: dict[str, Key] = {key.path: key for key in SCHEMA}


class ConfigError(ValueError):
    """The configuration is not usable."""


def _dig(data: dict, path: str) -> tuple[bool, Any]:
    node: Any = data
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return False, None
        node = node[part]
    return True, node


class Config:
    """A loaded configuration. Read it with dotted paths."""

    def __init__(self, data: dict[str, Any], source: str = "<defaults>"):
        self.data = data
        self.source = source

    def get(self, path: str) -> Any:
        """Read a setting. Unknown paths raise rather than returning None."""
        key = SCHEMA_BY_PATH.get(path)
        if key is None:
            raise KeyError(f"{path!r} is not a harness-tuner setting; see SCHEMA in config/__init__.py")
        found, value = _dig(self.data, path)
        if not found:
            return key.default
        return value

    def is_set(self, path: str) -> bool:
        found, value = _dig(self.data, path)
        return found and value not in ("", [], None)

    def validate(self) -> list[str]:
        """Return every problem found, rather than raising on the first."""
        problems: list[str] = []
        for key in SCHEMA:
            found, value = _dig(self.data, key.path)
            if not found:
                continue
            kind = key.kind
            if kind is float and isinstance(value, int) and not isinstance(value, bool):
                value = float(value)
            if kind is bool and not isinstance(value, bool):
                problems.append(f"{key.path}: expected a boolean, got {type(value).__name__}")
                continue
            if not isinstance(value, kind) or (kind is not bool and isinstance(value, bool)):
                problems.append(f"{key.path}: expected {kind.__name__}, got {type(value).__name__}")

        level = self.get("harness.adapter_level")
        if level not in ADAPTER_LEVELS:
            problems.append(f"harness.adapter_level: must be one of {list(ADAPTER_LEVELS)}, got {level!r}")
        subject = self.get("mode.subject")
        if subject not in SUBJECTS:
            problems.append(f"mode.subject: must be one of {list(SUBJECTS)}, got {subject!r}")
        execution = self.get("mode.execution")
        if execution not in EXECUTIONS:
            problems.append(f"mode.execution: must be one of {list(EXECUTIONS)}, got {execution!r}")

        # Coherence between settings, which is where the real mistakes live.
        if execution in ("drive", "both"):
            if level in ("L0", "L1"):
                problems.append(
                    f"mode.execution is {execution!r} but harness.adapter_level is {level!r}. "
                    "Driving needs L2 or above, because the engine has to invoke the harness itself."
                )
            if not self.get("harness.invoke"):
                problems.append("mode.execution requires driving, but harness.invoke is empty.")
            if not self.get("containment.approved"):
                problems.append(
                    "mode.execution requires driving, but containment.approved is false. "
                    "A driven run executes your harness with its real tool access; "
                    "record what it may reach and approve it before driving."
                )
        if self.get("models.call_mode") not in ("delegated", "direct"):
            problems.append("models.call_mode: must be 'delegated' or 'direct'.")
        return problems

    def require_valid(self) -> None:
        problems = self.validate()
        if problems:
            raise ConfigError(
                f"{self.source} has {len(problems)} problem(s):\n  " + "\n  ".join(problems)
            )

    # NOTE: the artifact stamp deliberately lives in harness_tuner/stamp.py
    # rather than here. Echoing every setting back from inside the config
    # module would make tools/check_config_wiring.py count each one as
    # consumed, and every dead setting in the project would pass the check.


def load(path: str | None = None) -> Config:
    """Load configuration from TOML.

    TOML rather than YAML because ``tomllib`` is in the standard library and a
    YAML parser is not. Declaring YAML config would break the zero-dependency
    promise on the first line of the first install.
    """
    if path is None:
        path = CONFIG_FILENAME
    if not os.path.isfile(path):
        return Config({}, source=f"{path} (absent, using defaults)")
    with open(path, "rb") as handle:
        data = tomllib.load(handle)
    return Config(data, source=os.path.abspath(path))
