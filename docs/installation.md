# Installation and requirements

[Documentation index](README.md) · [Next: quickstart](quickstart.md)

## Supported environment

Use Python **3.11+**, Git, and the official **k6 v2.2.0** executable. Real-engine behavior has been validated on Linux amd64. CI tests Python 3.11–3.13 on Linux. macOS is not yet release-qualified; Windows users should use WSL2 for the POSIX process-group behavior covered by the Linux tests.

`inspect` and `diff` do not need k6 or a running target. `verify` requires both. Your app must already be running and expose the endpoints used by the scenario. Authenticated apps need test credentials supplied by your journey or observer recipe. No LLM credentials or E2B account are required for local runs.

## Install k6

Download the archive for your platform from the [official v2.2.0 release](https://github.com/grafana/k6/releases/tag/v2.2.0). Verify its SHA-256 against the release checksums, extract it, and put the executable on your `PATH`.

For Linux amd64, the following uses a user-owned directory:

```bash
mkdir -p "$HOME/.local/bin"
curl --fail --location --output /tmp/litetraffic-k6-v2.2.0.tar.gz \
  https://github.com/grafana/k6/releases/download/v2.2.0/k6-v2.2.0-linux-amd64.tar.gz
printf '%s  %s\n' \
  b5a8003c86f35f5cd5ceef1490312c48e587696c94d998cefc6d7b3b4cb1597d \
  /tmp/litetraffic-k6-v2.2.0.tar.gz | sha256sum --check -
tar -xzf /tmp/litetraffic-k6-v2.2.0.tar.gz -C /tmp
install -m 755 /tmp/k6-v2.2.0-linux-amd64/k6 "$HOME/.local/bin/k6"
export PATH="$HOME/.local/bin:$PATH"
k6 version
```

This archive and checksum are specifically for Linux amd64. For Linux arm64 or macOS, use the corresponding asset and checksum from the release page. You may instead leave k6 outside `PATH` and pass `--k6-path /absolute/path/to/k6` to `doctor` and `verify`.

LiteTraffic rejects engine versions other than v2.2.0: `doctor` reports them as a failed `k6` check and `verify` refuses to run.

## Install LiteTraffic from source

There is no claim of a published PyPI release. Install from this repository:

```bash
git clone https://github.com/rohansx/litetraffic.git
cd litetraffic
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
```

For development, use `python -m pip install -e '.[dev]'` to include pytest. Runtime dependencies are HTTPX and Pydantic; setuptools builds the package. No database, Docker daemon, Node.js, or hosted LiteTraffic service is required. Journey scripts execute in k6's JavaScript runtime, not Node.js.

Keep the checkout for the example bundles: they are not installed as package resources in the wheel. Commands in the guides assume the repository root as the working directory.

## Check the installation

```bash
litetraffic --help
litetraffic doctor --json
litetraffic inspect examples/checkout --seed 42 --json
```

Once your app is running, optionally check a safe health URL:

```bash
litetraffic doctor --target http://127.0.0.1:8765 --json
```

`doctor` sends one GET without following redirects. Only a 2xx or 3xx response passes; any other status, such as 404 or 503, fails as `reachable but not ready`. It never installs binaries or writes to the target.

Run artifacts are written under `.litetraffic/runs/` unless overridden. `doctor` checks that this directory (or `--output-dir`) is writable and that its filesystem has at least 100 MiB free. The preview has no calibrated minimum CPU/RAM requirement; allocate enough resources for the target and the declared virtual-user count, and inspect dropped/undelivered journeys rather than treating an overloaded generator as a valid pass.
