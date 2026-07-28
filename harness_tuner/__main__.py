"""Command line entry point.

Commands are registered in :data:`COMMANDS` and imported lazily, so ``--help``
stays fast and a command that needs an optional module does not break the rest
of the tool.

A command appears here only when it is implemented. There are no placeholder
subcommands: a command that parses its arguments, validates them, and then does
nothing is worse than a missing one, because the exit code says it worked.
"""

from __future__ import annotations

import argparse
import importlib
import sys

from . import __version__

#: name -> (module, callable, one-line help)
#:
#: The callable takes the parsed namespace and returns a process exit code.
COMMANDS: dict[str, tuple[str, str, str]] = {}


def _register(name: str, module: str, func: str, help_text: str) -> None:
    COMMANDS[name] = (module, func, help_text)


_register(
    "run",
    "harness_tuner.runner",
    "main",
    "measure a harness from adapter-produced traces and write the eight HTP-1 artifacts",
)
_register(
    "doctor",
    "harness_tuner.doctor",
    "main",
    "report what this install cannot measure, and why",
)
_register(
    "tasks",
    "harness_tuner.tasks",
    "main",
    "show the task set for this configuration, or request one generated for your harness",
)
_register(
    "packs",
    "harness_tuner.packs",
    "main",
    "list the evaluation packs available to this install",
)
_register(
    "diagnose",
    "harness_tuner.diagnose",
    "main",
    "read a completed run, report findings with their trace evidence, and propose changes",
)
_register(
    "verify",
    "harness_tuner.verify",
    "main",
    "compare two runs task by task and report whether an applied change actually helped",
)
_register(
    "conformance",
    "harness_tuner.conformance",
    "main",
    "run the HTP-1 conformance suite against this implementation",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="harness-tuner",
        description=(
            "Measure an agent harness, report where it wastes the model's capability, "
            "propose changes, and prove whether an applied change helped."
        ),
        epilog=(
            "harness-tuner never edits your harness. It proposes diffs and you apply them. "
            "See AGENT-GUIDE.md for the setup protocol and the HTP-1 specification."
        ),
    )
    parser.add_argument("--version", action="version", version=f"harness-tuner {__version__} (HTP-1)")
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")

    for name, (module_path, func_name, help_text) in sorted(COMMANDS.items()):
        module = importlib.import_module(module_path)
        add_arguments = getattr(module, "add_arguments", None)
        sub = subparsers.add_parser(name, help=help_text, description=help_text)
        if add_arguments is not None:
            add_arguments(sub)
        sub.set_defaults(_handler=(module_path, func_name))

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = getattr(args, "_handler", None)
    if handler is None:
        parser.print_help()
        return 0
    module_path, func_name = handler
    module = importlib.import_module(module_path)
    return int(getattr(module, func_name)(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
