"""The installed layout must behave exactly like the checkout.

A wheel has no repo root beside the package, so packs, the conformance suite
and the agent guide are bundled inside it. The failure this guards against is
quiet: an install that finds no packs prints "no packs found" and exits 0, and
a conformance run with no fixtures would look like an implementation problem
rather than a packaging one. CI also builds the real wheel and runs it from
outside the checkout; these tests pin the lookup rules themselves.
"""

from __future__ import annotations

import argparse
import os
import shutil

import pytest

from harness_tuner import _resources as R
from harness_tuner import guide as G

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _fake_install(tmp_path, monkeypatch, *, bundled: bool, beside: bool):
    """Point the resolver at a package dir with the chosen layout."""
    site = tmp_path / "site"
    pkg = site / "harness_tuner"
    (pkg / "_bundled").mkdir(parents=True)
    if bundled:
        shutil.copytree(os.path.join(ROOT, "packs"), pkg / "_bundled" / "packs")
        shutil.copy(os.path.join(ROOT, "AGENT-GUIDE.md"), pkg / "_bundled" / "AGENT-GUIDE.md")
    if beside:
        (site / "packs").mkdir()
    monkeypatch.setattr(R, "_PACKAGE_DIR", str(pkg))
    monkeypatch.setattr(R, "_BUNDLED_DIR", str(pkg / "_bundled"))
    return site, pkg


def test_checkout_resources_resolve_beside_the_package():
    assert R.resource_path("packs") == os.path.join(ROOT, "packs")
    assert R.resource_path("conformance") == os.path.join(ROOT, "conformance")
    assert R.resource_path("AGENT-GUIDE.md") == os.path.join(ROOT, "AGENT-GUIDE.md")


def test_installed_layout_falls_back_to_the_bundled_copy(tmp_path, monkeypatch):
    _, pkg = _fake_install(tmp_path, monkeypatch, bundled=True, beside=False)
    assert R.resource_path("packs") == str(pkg / "_bundled" / "packs")

    from harness_tuner import packs as PK

    names = {pack["name"] for pack in PK.available()}
    assert {"core", "coding", "memory", "safety", "org"} <= names


def test_an_editable_copy_beside_the_package_wins(tmp_path, monkeypatch):
    site, _ = _fake_install(tmp_path, monkeypatch, bundled=True, beside=True)
    assert R.resource_path("packs") == str(site / "packs")


def _guide_args(out, force=False):
    return argparse.Namespace(out=str(out), force=force)


def test_guide_writes_this_versions_guide_byte_for_byte(tmp_path):
    assert G.main(_guide_args(tmp_path)) == 0
    with open(os.path.join(ROOT, "AGENT-GUIDE.md"), "rb") as shipped:
        assert (tmp_path / "AGENT-GUIDE.md").read_bytes() == shipped.read()


def test_guide_is_idempotent_and_never_clobbers_an_edited_copy(tmp_path):
    target = tmp_path / "AGENT-GUIDE.md"
    assert G.main(_guide_args(target)) == 0
    assert G.main(_guide_args(target)) == 0  # identical: nothing to do

    target.write_text("our own notes\n", encoding="utf-8")
    assert G.main(_guide_args(target)) == 1
    assert target.read_text(encoding="utf-8") == "our own notes\n"

    assert G.main(_guide_args(target, force=True)) == 0
    assert target.read_bytes() == G.read_guide()


def test_guide_to_stdout(capfdbinary):
    assert G.main(_guide_args("-")) == 0
    assert capfdbinary.readouterr().out == G.read_guide()


@pytest.mark.parametrize("name", ["packs", "conformance", "AGENT-GUIDE.md"])
def test_every_resource_the_wheel_bundles_exists_in_the_checkout(name):
    """pyproject force-includes these paths; a rename must fail here first."""
    assert os.path.exists(os.path.join(ROOT, name))
