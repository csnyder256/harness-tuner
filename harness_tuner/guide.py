"""Put AGENT-GUIDE.md where a coding agent will read it.

Setup starts with one step: copy the agent guide into your project and tell
your coding agent to follow it. From a clone that is a ``cp``. From a pip,
pipx or uvx install, or from the container image, there is nothing obvious to
copy, so this command writes the exact guide this install implements. It
writes the guide that matches this version, not whatever happens to be on
the default branch today.
"""

from __future__ import annotations

import os
import sys

from ._resources import resource_path

GUIDE_NAME = "AGENT-GUIDE.md"


def add_arguments(parser) -> None:
    parser.add_argument(
        "--out",
        default=GUIDE_NAME,
        metavar="PATH",
        help=f"where to write the guide (default: ./{GUIDE_NAME}); '-' prints it to stdout",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="overwrite an existing file that differs from this version's guide",
    )


def read_guide() -> bytes:
    # Bytes, not text: the copy must be identical to the shipped guide, line
    # endings included, on every platform.
    with open(resource_path(GUIDE_NAME), "rb") as handle:
        return handle.read()


def main(args) -> int:
    try:
        text = read_guide()
    except FileNotFoundError:
        print(f"this install does not include {GUIDE_NAME}", file=sys.stderr)
        return 1

    if args.out == "-":
        sys.stdout.flush()
        sys.stdout.buffer.write(text)
        sys.stdout.buffer.flush()
        return 0

    # A trailing separator means a directory, as with `run --out results/`,
    # whether or not it exists yet. Missing parent directories are created.
    names_directory = args.out.endswith(("/", os.sep)) or os.path.isdir(args.out)
    target = os.path.abspath(args.out)
    if names_directory:
        target = os.path.join(target, GUIDE_NAME)
    if os.path.isdir(target):
        print(f"{target} is a directory; name the file to write", file=sys.stderr)
        return 1
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
    except OSError as exc:
        print(f"cannot create the directory for {target}: {exc.strerror or exc}", file=sys.stderr)
        return 1
    if os.path.exists(target) and not args.force:
        with open(target, "rb") as handle:
            if handle.read() == text:
                print(f"{target} is already this version's guide")
                return 0
        # Someone may have annotated their copy; never clobber it silently.
        print(f"{target} exists and differs; pass --force to replace it", file=sys.stderr)
        return 1

    try:
        with open(target, "wb") as handle:
            handle.write(text)
    except OSError as exc:
        print(f"cannot write {target}: {exc.strerror or exc}", file=sys.stderr)
        return 1
    print(f"wrote {target}")
    print('next: tell your coding agent "Follow the agent protocol, and set this up for our harness."')
    return 0
