"""Locate the data that ships beside the engine: packs, conformance, the guide.

There are two layouts and both are first-class. In a source checkout and in
the container image, ``packs/``, ``conformance/`` and ``AGENT-GUIDE.md`` sit
next to the ``harness_tuner`` package, where a person can read and edit them.
An installed wheel has no such sibling, so the build copies the same files
into ``harness_tuner/_bundled/``. The sibling wins when it exists, so editing
a pack in a checkout takes effect without reinstalling anything.
"""

from __future__ import annotations

import os

_PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
_BUNDLED_DIR = os.path.join(_PACKAGE_DIR, "_bundled")


def resource_path(name: str) -> str:
    """Absolute path to a shipped resource, preferring the editable copy."""
    beside = os.path.join(os.path.dirname(_PACKAGE_DIR), name)
    if os.path.exists(beside):
        return beside
    bundled = os.path.join(_BUNDLED_DIR, name)
    if os.path.exists(bundled):
        return bundled
    # Neither exists: return the checkout path so the caller's own
    # "not found" error names the place a person would look first.
    return beside
