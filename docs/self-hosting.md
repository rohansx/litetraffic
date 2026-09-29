# Self-hosting

[Documentation index](README.md) · [Installation](installation.md) · [CI recipe](ci.md)

LiteTraffic is a CLI that runs on your machine or your CI runner, against an app you already run. Nothing is sent to a LiteTraffic service. The container image bundles the CLI, the `[ai]` extra, and the pinned k6 v2.2.0 (checksum-verified at build time), so you can run it with only Docker installed.

## The image

```bash
docker pull ghcr.io/rohansx/litetraffic:main
```

| Tag | Built from |
|---|---|
| `:main` | The latest commit on `main` |
| `:sha-<short>` | That commit on `main`; pin this in CI for reproducible runs |
| `:X.Y.Z` | A `vX.Y.Z` release tag; `:latest` also moves to it unless it is a pre-release |

Images are published for `linux/amd64` and `linux/arm64`, each with the matching official k6 archive checked against its SHA-256 from the release checksums file. End-to-end `verify` runs have been checked on amd64; the arm64 image builds the same way but has not been run end to end yet.

To build it yourself from a checkout: `docker build -t litetraffic .`

The entrypoint is `litetraffic`, the working directory is `/work`, and the process runs as the non-root user `litetraffic` (uid 1000). Runs are written to `/work/.litetraffic/runs`. Mount a host directory there to keep them; it must be writable by uid 1000, or add `--user "$(id -u):$(id -g)"`.

## Run it with no local install

```bash
# Check k6, Python, and the output directory inside the image
docker run --rm ghcr.io/rohansx/litetraffic:main doctor

# Inspect a scenario (no k6 run, no target needed)
docker run --rm -v "$PWD/scenarios:/work/scenarios:ro" \
  ghcr.io/rohansx/litetraffic:main inspect scenarios/checkout --seed 42

# Verify an app listening on the host's 127.0.0.1:3000 (Linux)
mkdir -p .litetraffic/runs
docker run --rm --network host \
  -v "$PWD/scenarios:/work/scenarios:ro" \
  -v "$PWD/.litetraffic/runs:/work/.litetraffic/runs" \
  ghcr.io/rohansx/litetraffic:main \
  verify scenarios/checkout --target http://127.0.0.1:3000 --seed 42
```

`--network host` lets the container reach services on the host's loopback address; it is Linux-only. On Docker Desktop, target `http://host.docker.internal:3000` without `--network host`, or put LiteTraffic on the same Docker network as your app and use the service name. The exit code is the verdict, as for the installed CLI (`0` pass, `1` fail, `2` inconclusive, `3` error: see [CI exit codes](ci.md#exit-codes)).

Scenarios that read secrets (`secret_env`, `bearer_token_env`, `headers_env`) get them through `-e NAME` (which passes the variable from your shell without writing its value on the command line). `verify --capture docker:NAME` needs a `docker` CLI and daemon socket inside the container, which the image does not include; install LiteTraffic locally for server capture.

## In CI

Run the image as the job container, so there is no Python or k6 setup step:

```yaml
jobs:
  verify:
    runs-on: ubuntu-latest
    container:
      image: ghcr.io/rohansx/litetraffic:main   # pin :sha-<short> for reproducible builds
      options: --user root   # the runner's workspace is not writable by the image's uid 1000
    services:
      app:
        image: your-org/your-app:latest
    steps:
      - uses: actions/checkout@v4
      - run: litetraffic verify scenarios/checkout --target http://app:3000 --seed "${{ github.run_number }}"
      - if: always()
        uses: actions/upload-artifact@v4
        with:
          name: litetraffic-runs
          path: .litetraffic/runs/
```

In a job container, service containers are reachable by their service name. If your app starts on the runner itself instead (for example `docker compose up -d`), keep the normal runner and call the image with `docker run --rm --network host --user "$(id -u):$(id -g)" -v "$PWD:/work" ghcr.io/rohansx/litetraffic:main verify ...`. The [CI recipe](ci.md) covers exit codes, a job summary, and keeping the evidence.

## Docker Compose

[`examples/compose/`](../examples/compose/compose.yaml) runs the checkout example server and a one-shot `verify` against it, both from the image:

```bash
cd examples/compose
mkdir -p .litetraffic/runs
docker compose up --build --exit-code-from verify
```

The `verify` service shares the server's network namespace (`network_mode: service:checkout`), so the server's `127.0.0.1:8765` is reachable unchanged, and `--exit-code-from verify` makes `docker compose up` exit with the verdict. Replace the `checkout` service with your app and the mounted scenario with yours.

## View results

`litetraffic dashboard` serves the run folder on `http://127.0.0.1:8780/`. It binds only to `127.0.0.1` by design and rejects requests whose `Host` is not a loopback name for its port: it is a local, read-only viewer with no login, so it is not meant to be exposed on a network ([details](cli.md#dashboard)).

With the image on Linux, host networking puts that loopback address on your machine:

```bash
docker run --rm -it --network host \
  -v "$PWD/.litetraffic/runs:/work/.litetraffic/runs" \
  ghcr.io/rohansx/litetraffic:main dashboard
```

Then open `http://127.0.0.1:8780/`. **Explain with AI** on a run page uses `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` from the container's environment; pass it with `-e ANTHROPIC_API_KEY`. On macOS or Windows, where `--network host` does not reach the host's loopback, [install the CLI locally](installation.md) and run `litetraffic dashboard` against the same run folder; the dashboard needs no k6.

Each run folder also contains a standalone `report.html` you can open directly.

## Not self-hosted yet

A shared team dashboard with login, run history across machines, and access control is not part of the open-source CLI. It is planned as LiteTraffic Cloud and a future self-hosted edition. Join the waitlist at <https://litetraffic.com/pricing#waitlist>.
