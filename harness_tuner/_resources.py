"""Locate the data that ships with the engine: packs, conformance, the guide.

There are two layouts and both are first-class. An installed wheel carries its
own copy under ``harness_tuner/_bundled/`` and always uses it: its parent
directory is a shared site-packages, where an unrelated top-level ``packs`` or
``conformance`` directory from some other distribution could otherwise shadow
it. A source checkout and the container image have no ``_bundled/``, so they
use ``packs/``, ``conformance/`` and ``AGENT-GUIDE.md`` beside the package,
where a person can read and edit them and see the change immediately.
"""

from __future__ import annotations

import os

_PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
_BUNDLED_DIR = os.path.join(_PACKAGE_DIR, "_bundled")


def resource_path(name: str) -> str:
    """Absolute path to a shipped resource for this install's layout."""
    bundled = os.path.join(_BUNDLED_DIR, name)
    if os.path.exists(bundled):
        return bundled
    # A checkout or the container. If this is missing too, returning the
    # checkout path lets the caller's own "not found" error name the place a
    # person would look first.
    return os.path.join(os.path.dirname(_PACKAGE_DIR), name)
