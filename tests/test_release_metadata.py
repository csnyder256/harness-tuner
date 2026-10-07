"""The release version is written down in several places, and one copy drifted.

The v0.3.0 release notes link to the deployment instructions *at the v0.3.0
tag*, and that file still told people to install the 0.2.3 wheel and pull the
0.2.3 container tag. Both are the previous release's artifacts, so the versioned
link promised version-specific instructions and delivered the wrong build. Every
test passed, the tag built, and nothing noticed.

These tests pin the two halves of the promise: the metadata check passes on the
real tree, and it fails when DEPLOYMENT.md names a version other than the one
being released. A guard that cannot fail proves nothing, so the failing cases
are tested as deliberately as the passing one.
"""

from __future__ import annotations

import importlib
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

verify_release = importlib.import_module("scripts.verify_release")

DEPLOYMENT_TEMPLATE = (
    "```sh\n"
    "uv tool install ./harness_tuner-{version}-py3-none-any.whl\n"
    "```\n\n"
    "## Container\n\n"
    "```sh\n"
    "docker run --rm ghcr.io/csnyder256/harness-tuner:{version} --help\n"
    "```\n"
)


def _tree(tmp_path, version: str, *, deployment_version: str | None = None):
    """Write the minimum tree ``check`` reads, for ``version``.

    ``deployment_version`` lets a test publish one version while the install
    instructions still name another, which is the drift this file guards.
    """
    root = tmp_path / "repo"
    (root / "harness_tuner").mkdir(parents=True)
    (root / "docs" / "releases").mkdir(parents=True)

    (root / "VERSION").write_text(version + "\n", encoding="utf-8")
    with open(os.path.join(ROOT, "release.json"), "r", encoding="utf-8") as handle:
        release_config = handle.read()
    (root / "release.json").write_text(release_config, encoding="utf-8")
    (root / "harness_tuner" / "__init__.py").write_text(
        f'__version__ = "{version}"\n', encoding="utf-8"
    )
    (root / "docs" / "releases" / f"v{version}.md").write_text(
        "Use the versioned [deployment instructions]"
        f"(https://github.com/csnyder256/harness-tuner/blob/v{version}/DEPLOYMENT.md).\n",
        encoding="utf-8",
    )
    (root / "DEPLOYMENT.md").write_text(
        DEPLOYMENT_TEMPLATE.format(version=deployment_version or version),
        encoding="utf-8",
    )
    return root


def test_the_real_tree_agrees_on_one_version():
    version = verify_release.check(ROOT)
    with open(os.path.join(ROOT, "VERSION"), "r", encoding="utf-8") as handle:
        assert version == handle.read().strip()

    import harness_tuner

    assert harness_tuner.__version__ == version


def test_the_script_exits_zero_the_way_ci_runs_it():
    """release-metadata.yml and release.yml invoke this by path, not as a module."""
    result = subprocess.run(
        [sys.executable, os.path.join(ROOT, "scripts", "verify_release.py")],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Release metadata agrees on" in result.stdout


def test_install_instructions_that_name_the_previous_release_are_caught(tmp_path):
    """The exact v0.3.0 defect: a 0.3.0 release whose DEPLOYMENT.md says 0.2.3."""
    root = _tree(tmp_path, "0.3.0", deployment_version="0.2.3")
    with pytest.raises(AssertionError, match="harness_tuner-0.3.0-py3-none-any.whl"):
        verify_release.check(root)


def test_a_container_tag_that_names_another_release_is_caught(tmp_path):
    root = _tree(tmp_path, "0.3.0")
    # Keep the wheel correct but drift the image tag, so the two assertions are
    # proven independently rather than one masking the other.
    (root / "DEPLOYMENT.md").write_text(
        DEPLOYMENT_TEMPLATE.format(version="0.3.0").replace(
            "ghcr.io/csnyder256/harness-tuner:0.3.0",
            "ghcr.io/csnyder256/harness-tuner:0.2.3",
        ),
        encoding="utf-8",
    )
    with pytest.raises(AssertionError, match="container image"):
        verify_release.check(root)


def test_a_release_notes_file_without_the_versioned_link_is_caught(tmp_path):
    root = _tree(tmp_path, "0.3.0")
    (root / "docs" / "releases" / "v0.3.0.md").write_text(
        "See the deployment instructions.\n", encoding="utf-8"
    )
    with pytest.raises(AssertionError, match="version-specific deployment link"):
        verify_release.check(root)


def test_a_tag_that_disagrees_with_the_version_is_caught(tmp_path):
    root = _tree(tmp_path, "0.3.0")
    with pytest.raises(AssertionError, match="Tag and VERSION disagree"):
        verify_release.check(root, ref="refs/tags/v0.2.3")
