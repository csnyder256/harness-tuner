# Install Harness Tuner

## Download and install an isolated CLI

Python 3.11+ is required. Download the wheel and `checksums.txt` from the
[latest release](https://github.com/csnyder256/harness-tuner/releases/latest).
Verify it with `sha256sum -c checksums.txt --ignore-missing` (Linux),
`shasum -a 256 <wheel>` (Mac), or `Get-FileHash <wheel> -Algorithm SHA256`
(PowerShell), comparing the result to the checksum file.

```sh
uv tool install ./harness_tuner-0.2.3-py3-none-any.whl
harness-tuner --help
harness-tuner conformance
```

Alternatively use `pipx install ./harness_tuner-0.2.3-py3-none-any.whl` or a
Python virtual environment. Bundled packs and conformance data travel with the
wheel, so the CLI works from a different current directory.

## Container

```sh
docker run --rm ghcr.io/csnyder256/harness-tuner:0.2.3 --help
```

Published images support Linux AMD64 and ARM64. Mount a working directory at
`/work` for commands that read or write your own artifacts. Source downloads are
also provided for development. The engine uses only the Python standard library.

## Upgrade

Install the new wheel with `uv tool install --force <wheel>` or
`pipx install --force <wheel>`. Back up any user output before changing versions.
The release workflow tests conformance and version agreement before publication.
