"""Prove that every setting is read by something.

A configuration key can be defined, type-checked, validated against its legal
values, documented at length, printed in a stamp, and read by absolutely
nothing. It looks finished from every angle except the one that matters, and
the failure is invisible because the setting appears everywhere you would think
to look.

This check closes that hole mechanically. Every setting is read through
``Config.get("dotted.path")``, so a consumer necessarily contains the setting's
literal path as a string. If that string appears nowhere outside the config
module, the setting is decorative: either wire it or delete it.

Run it from the repository root::

    py -3 tools/check_config_wiring.py

Exit code 0 means every key has a consumer. Exit code 1 lists the orphans.
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from harness_tuner.config import SCHEMA  # noqa: E402

CONFIG_PACKAGE = os.path.join("harness_tuner", "config")
SEARCH_DIRS = ("harness_tuner", "tests", "tools")
SKIP_DIRS = {"__pycache__", ".git", "results"}


def source_files() -> list[str]:
    out = []
    for base in SEARCH_DIRS:
        for dirpath, dirnames, filenames in os.walk(os.path.join(ROOT, base)):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            rel_dir = os.path.relpath(dirpath, ROOT)
            if rel_dir.startswith(CONFIG_PACKAGE):
                continue
            for name in filenames:
                if name.endswith(".py"):
                    out.append(os.path.join(dirpath, name))
    return sorted(out)


def main() -> int:
    files = source_files()
    contents = {}
    for path in files:
        with open(path, "r", encoding="utf-8") as handle:
            contents[os.path.relpath(path, ROOT)] = handle.read()

    orphans: list[str] = []
    consumers: dict[str, list[str]] = {}

    for key in SCHEMA:
        needle = f'"{key.path}"'
        alt = f"'{key.path}'"
        found = [rel for rel, text in contents.items() if needle in text or alt in text]
        # A test alone is not a consumer: it proves the key can be read, not
        # that anything in the product reads it.
        product = [rel for rel in found if not rel.startswith("tests")]
        consumers[key.path] = product
        if not product:
            orphans.append(key.path)

    width = max(len(k.path) for k in SCHEMA)
    for key in SCHEMA:
        where = consumers[key.path]
        status = "ok  " if where else "DEAD"
        summary = ", ".join(sorted({os.path.dirname(w) or w for w in where})) or "nothing reads it"
        print(f"{status} {key.path:<{width}}  {summary}")

    print()
    if orphans:
        print(f"{len(orphans)} setting(s) are accepted and validated but read by nothing:")
        for path in orphans:
            print(f"  {path}")
        print(
            "\nEither wire each one to a consumer outside harness_tuner/config, or delete it "
            "and stop documenting it. A setting that validates but does nothing is worse than "
            "a missing one, because an operator relies on it."
        )
        return 1

    print(f"all {len(SCHEMA)} settings have a consumer outside harness_tuner/config")
    return 0


if __name__ == "__main__":
    sys.exit(main())
