"""Check release metadata without importing optional application dependencies.

A release version is written down in more than one place, and the failure mode
when one copy drifts is quiet: the tag builds, the tests pass, and a published
document tells the next person to install the previous release. This script
compares every copy that matters, including the install instructions, so the
drift fails CI instead of shipping.

Exposed as :func:`check` rather than only as a script so the test suite can run
it against a copy of the tree and prove it can actually fail.
"""
import json
import os
import re
from pathlib import Path

#: Repository that owns the published release assets and the container image.
REPO_OWNER = "csnyder256"


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def check(root: Path, *, ref: str | None = None) -> str:
    """Validate every recorded version, and return it.

    ``ref`` overrides the git ref (defaults to ``GITHUB_REF``); it is only
    inspected when it names a tag.
    """
    root = Path(root)
    version = (root / "VERSION").read_text().strip()
    assert re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version), "Invalid release version"

    if ref is None:
        ref = os.environ.get("GITHUB_REF", "")
    if ref.startswith("refs/tags/"):
        assert ref == "refs/tags/v" + version, "Tag and VERSION disagree"

    config = json.loads((root / "release.json").read_text())
    for filename in config.get("package_json", []):
        assert json.loads((root / filename).read_text())["version"] == version, filename
    for filename, pattern in config.get("version_patterns", {}).items():
        match = re.search(pattern, (root / filename).read_text())
        assert match and match.group(1) == version, filename

    notes = root / "docs/releases" / ("v" + version + ".md")
    assert notes.is_file(), "Release notes missing"
    deployment_link = (
        "https://github.com/" + REPO_OWNER + "/" + config["name"] + "/blob/v" + version
        + "/DEPLOYMENT.md"
    )
    assert "(" + deployment_link + ")" in notes.read_text(), (
        "Release notes need a version-specific deployment link"
    )

    # The release notes link to DEPLOYMENT.md *at this release's tag*, so that
    # file is the install instructions for this release and nothing else. When
    # it still names the previous release's wheel and image tag, the versioned
    # link points at instructions that install the wrong build. That is exactly
    # what the v0.3.0 release did: its notes linked to a DEPLOYMENT.md that told
    # people to install the 0.2.3 wheel, which is not among its own assets. The
    # version-specific link is only meaningful if the file is version-specific
    # too, so both halves are checked here.
    deployment = (root / "DEPLOYMENT.md").read_text()
    wheel = f"harness_tuner-{version}-py3-none-any.whl"
    image = f"ghcr.io/{REPO_OWNER}/{config['name']}:{version}"
    assert wheel in deployment, (
        f"DEPLOYMENT.md does not name this release's wheel ({wheel}); the versioned "
        "install instructions would point at the wrong build"
    )
    assert image in deployment, (
        f"DEPLOYMENT.md does not name this release's container image ({image}); the "
        "versioned container instructions would pull the wrong tag"
    )
    return version


def main() -> int:
    version = check(repo_root())
    print("Release metadata agrees on", version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
