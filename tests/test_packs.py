"""Packs and capability gating.

The behaviour worth pinning is what happens to a task the harness cannot run.
It must be skipped and counted, never scored as a failure and never silently
dropped. "18 of 18 passed" reads very differently when nine were never
attempted, and a benchmark that fails a harness for lacking a browser is
ranking harnesses by feature count rather than by how well they use what they
have.
"""

from __future__ import annotations

import pytest

from harness_tuner import packs as PK


def test_every_shipped_pack_parses():
    """A malformed pack must fail loudly at load, not at run time."""
    found = PK.available()
    assert found, "no packs were discovered"
    names = {pack["name"] for pack in found}
    assert {"core", "coding", "memory", "safety", "org"} <= names


def test_task_ids_are_namespaced_by_pack():
    core = PK.load(["core"])[0]
    assert all(task["task_id"].startswith("core/") for task in core["tasks"])


def test_every_task_carries_its_provenance():
    """A leaderboard is meaningless without knowing where a task came from."""
    for pack in PK.available():
        for task in pack["tasks"]:
            prov = task["provenance"]
            assert prov["pack"] == pack["name"]
            assert prov["pack_version"]
            assert prov["source"].endswith(".toml")


def test_unknown_pack_names_are_reported():
    with pytest.raises(PK.PackError, match="unknown pack"):
        PK.load(["core", "does-not-exist"])


def test_a_pack_with_no_tasks_is_rejected(tmp_path):
    directory = tmp_path / "packs" / "bad"
    directory.mkdir(parents=True)
    (directory / "bad.toml").write_text('[pack]\nname = "bad"\n', encoding="utf-8")
    with pytest.raises(PK.PackError, match="at least one"):
        PK.available(str(tmp_path / "packs"))


def test_duplicate_task_ids_are_rejected(tmp_path):
    directory = tmp_path / "packs" / "dup"
    directory.mkdir(parents=True)
    (directory / "dup.toml").write_text(
        '[pack]\nname = "dup"\n\n[[task]]\nid = "a"\nprompt = "x"\n\n[[task]]\nid = "a"\nprompt = "y"\n',
        encoding="utf-8",
    )
    with pytest.raises(PK.PackError, match="duplicate task id"):
        PK.available(str(tmp_path / "packs"))


def test_a_task_without_a_prompt_is_rejected(tmp_path):
    directory = tmp_path / "packs" / "np"
    directory.mkdir(parents=True)
    (directory / "np.toml").write_text('[pack]\nname = "np"\n\n[[task]]\nid = "a"\n', encoding="utf-8")
    with pytest.raises(PK.PackError, match="has no prompt"):
        PK.available(str(tmp_path / "packs"))


# ---------------------------------------------------------------------------
# Capability gating
# ---------------------------------------------------------------------------


def caps(**present):
    return {
        "status": "scanned",
        "capabilities": {name: {"present": value} for name, value in present.items()},
    }


def test_a_missing_capability_skips_rather_than_fails():
    packs = PK.load(["core"])
    runnable, skipped = PK.select(packs, caps(filesystem=False, tools=True))

    assert skipped, "tasks requiring filesystem should have been skipped"
    for entry in skipped:
        assert "filesystem" in entry["unmet"]
        assert "skipped, not failed" in entry["reason"]

    # the one task that needs nothing still runs
    assert any(task["task_id"] == "core/no-op" for task in runnable)


def test_a_present_capability_runs_everything():
    packs = PK.load(["core"])
    runnable, skipped = PK.select(packs, caps(filesystem=True))
    assert skipped == []
    assert len(runnable) == len(packs[0]["tasks"])


def test_without_a_scan_nothing_is_skipped():
    """Guessing a capability is absent would silently shrink the task set."""
    packs = PK.load(["org"])
    runnable, skipped = PK.select(packs, None)
    assert skipped == []
    assert len(runnable) == len(packs[0]["tasks"])

    runnable, skipped = PK.select(packs, {"status": "not_scanned", "capabilities": {}})
    assert skipped == []
    assert len(runnable) == len(packs[0]["tasks"])


def test_a_task_needing_two_capabilities_reports_both_gaps():
    packs = PK.load(["org"])
    _runnable, skipped = PK.select(packs, caps(memory=True, subagents=False))
    concurrent = next(s for s in skipped if s["task_id"].endswith("concurrent-write"))
    assert concurrent["unmet"] == ["subagents"]
    assert set(concurrent["requires"]) == {"memory", "subagents"}


def test_skips_and_runs_account_for_every_task():
    """Nothing may vanish between the pack and the run."""
    packs = PK.load(["core", "coding", "memory", "safety", "org"])
    total = sum(len(pack["tasks"]) for pack in packs)
    runnable, skipped = PK.select(packs, caps(filesystem=True, shell=False, memory=False, tools=True))
    assert len(runnable) + len(skipped) == total


def test_memory_and_safety_packs_carry_the_fingerprint_tags():
    """Two fingerprint dimensions only exist when a task exercised them."""
    memory = PK.load(["memory"])[0]
    safety = PK.load(["safety"])[0]
    assert all("memory" in task["tags"] for task in memory["tasks"])
    assert all("safety" in task["tags"] for task in safety["tasks"])
