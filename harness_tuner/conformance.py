"""The HTP-1 conformance suite.

An implementation of HTP-1 is conformant when, for every fixture in
``conformance/fixtures``, it reproduces the metric values recorded in
``conformance/expected``.

This is the mechanism that makes the third install rung safe. When a machine
cannot run the container and has no suitable runtime, the setup agent
reimplements the protocol in whatever that machine does have. Without this
suite that reimplementation would be trusted; with it, it is tested. A
reimplementation that computes rework_ratio subtly differently produces a
report that looks exactly as convincing as a correct one, and nothing else in
the system would catch it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from ._resources import resource_path
from .protocol import canonical_json, compute, normalize_trace

FIXTURES_DIRNAME = "fixtures"
EXPECTED_DIRNAME = "expected"


def suite_root(explicit: str | None = None) -> str:
    """Locate the shipped conformance directory."""
    if explicit:
        return os.path.abspath(explicit)
    return resource_path("conformance")


def load_fixtures(root: str) -> list[dict[str, Any]]:
    fixtures_dir = os.path.join(root, FIXTURES_DIRNAME)
    if not os.path.isdir(fixtures_dir):
        raise FileNotFoundError(f"no fixtures directory at {fixtures_dir}")
    out = []
    for name in sorted(os.listdir(fixtures_dir)):
        if not name.endswith(".json"):
            continue
        with open(os.path.join(fixtures_dir, name), "r", encoding="utf-8") as handle:
            fixture = json.load(handle)
        fixture.setdefault("name", os.path.splitext(name)[0])
        out.append(fixture)
    if not out:
        raise FileNotFoundError(f"no fixtures found in {fixtures_dir}")
    return out


def evaluate(fixture: dict[str, Any]) -> dict[str, dict]:
    """Compute the metric block a conformant implementation must produce."""
    steps = normalize_trace(fixture["trace"])
    return compute(steps, fixture.get("task") or {})


def expected_path(root: str, name: str) -> str:
    return os.path.join(root, EXPECTED_DIRNAME, f"{name}.json")


def _diff(expected: dict[str, dict], actual: dict[str, dict]) -> list[str]:
    problems: list[str] = []
    for key in sorted(set(expected) | set(actual)):
        want = expected.get(key)
        got = actual.get(key)
        if want is None:
            problems.append(f"  {key}: produced but not expected -> {canonical_json(got)}")
            continue
        if got is None:
            problems.append(f"  {key}: expected but not produced -> {canonical_json(want)}")
            continue
        if canonical_json(want) != canonical_json(got):
            problems.append(
                f"  {key}:\n"
                f"      expected {canonical_json(want)}\n"
                f"      actual   {canonical_json(got)}"
            )
    return problems


def run_suite(root: str, *, write: bool = False, stream=sys.stdout) -> int:
    """Run every fixture. Returns a process exit code."""
    fixtures = load_fixtures(root)
    os.makedirs(os.path.join(root, EXPECTED_DIRNAME), exist_ok=True)

    failures = 0
    for fixture in fixtures:
        name = fixture["name"]
        actual = evaluate(fixture)
        path = expected_path(root, name)

        if write:
            with open(path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps({"name": name, "metrics": actual}, indent=2, sort_keys=True))
                handle.write("\n")
            print(f"wrote  {name}", file=stream)
            continue

        if not os.path.isfile(path):
            print(f"FAIL   {name}: no expected file at {path}", file=stream)
            failures += 1
            continue

        with open(path, "r", encoding="utf-8") as handle:
            expected = json.load(handle).get("metrics", {})

        problems = _diff(expected, actual)
        if problems:
            print(f"FAIL   {name}", file=stream)
            for line in problems:
                print(line, file=stream)
            failures += 1
        else:
            measured_n = sum(1 for m in actual.values() if m["status"] == "measured")
            print(
                f"ok     {name}  ({measured_n} measured, {len(actual) - measured_n} unavailable)",
                file=stream,
            )

    if write:
        print(f"\nwrote {len(fixtures)} expected file(s)", file=stream)
        return 0

    total = len(fixtures)
    if failures:
        print(f"\n{failures} of {total} fixture(s) FAILED conformance", file=stream)
        return 1
    print(f"\nHTP-1 conformance: {total} of {total} fixtures matched", file=stream)
    return 0


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--suite",
        default=None,
        metavar="DIR",
        help="conformance directory to run (defaults to the one shipped with this install)",
    )
    parser.add_argument(
        "--write-expected",
        action="store_true",
        help=(
            "regenerate the expected files from this implementation. "
            "Only ever correct when you are the reference implementation and a metric "
            "definition changed on purpose."
        ),
    )


def main(args: argparse.Namespace) -> int:
    return run_suite(suite_root(args.suite), write=args.write_expected)
