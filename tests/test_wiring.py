"""No setting may be accepted, validated, documented, and read by nothing.

This runs the same check as ``tools/check_config_wiring.py`` so that adding a
decorative setting fails the test suite rather than shipping. The first time
this check ran against this project it found twelve of twenty-nine settings
with no consumer at all, every one of which type-checked, validated, appeared
in the run artifacts, and did nothing.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from harness_tuner.config import SCHEMA, ADAPTER_LEVELS, EXECUTIONS, SUBJECTS, Config

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_every_setting_has_a_consumer_outside_the_config_package():
    result = subprocess.run(
        [sys.executable, os.path.join(ROOT, "tools", "check_config_wiring.py")],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert result.returncode == 0, (
        "at least one setting is decorative:\n" + result.stdout + result.stderr
    )
    assert f"all {len(SCHEMA)} settings have a consumer" in result.stdout


def test_the_check_actually_detects_a_dead_setting(tmp_path, monkeypatch):
    """A check that cannot fail proves nothing."""
    import harness_tuner.config as C

    dead = C.Key("tasks.invented_key", str, "", "a setting nothing reads")
    monkeypatch.setattr(C, "SCHEMA", C.SCHEMA + (dead,))

    sys.path.insert(0, ROOT)
    import importlib

    module = importlib.import_module("tools.check_config_wiring")
    importlib.reload(module)
    monkeypatch.setattr(module, "SCHEMA", C.SCHEMA + (dead,))
    assert module.main() == 1


def test_every_setting_is_documented():
    for key in SCHEMA:
        assert key.help, f"{key.path} has no help text"
        assert len(key.help) > 20, f"{key.path} help is too thin to be useful"


def test_schema_paths_are_unique():
    paths = [key.path for key in SCHEMA]
    assert len(paths) == len(set(paths))


def test_unknown_setting_paths_are_rejected_at_read_time():
    """A typo in a consumer must fail loudly, not silently return a default."""
    cfg = Config({}, source="<test>")
    with pytest.raises(KeyError, match="is not a harness-tuner setting"):
        cfg.get("cost.buget.max_usd")


def test_enumerations_are_validated():
    cfg = Config(
        {
            "harness": {"adapter_level": "L9"},
            "mode": {"subject": "everything", "execution": "sideways"},
            "models": {"call_mode": "psychic"},
        },
        source="<test>",
    )
    problems = " ".join(cfg.validate())
    assert "adapter_level" in problems and str(list(ADAPTER_LEVELS)) in problems
    assert "mode.subject" in problems and str(list(SUBJECTS)) in problems
    assert "mode.execution" in problems and str(list(EXECUTIONS)) in problems
    assert "call_mode" in problems


def test_wrong_types_are_reported_not_coerced():
    cfg = Config(
        {"cost": {"budget": {"max_usd": "five dollars", "enabled": "yes"}}},
        source="<test>",
    )
    problems = " ".join(cfg.validate())
    assert "cost.budget.max_usd" in problems
    assert "cost.budget.enabled" in problems


def test_an_int_is_accepted_where_a_float_is_declared():
    """TOML writes 5 where a float is meant, and rejecting that would be pedantry."""
    cfg = Config({"cost": {"budget": {"max_usd": 5}}}, source="<test>")
    assert not [p for p in cfg.validate() if "max_usd" in p]
