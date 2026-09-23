# LiteTraffic

**Stateful application traffic. Repeatable runs. Evidence of what changed.**

[Documentation](docs/README.md) · [Installation](docs/installation.md) · [Quickstart](docs/quickstart.md) · [CLI reference](docs/cli.md) · [MIT license](LICENSE)

LiteTraffic runs reviewed HTTP journeys against an application and checks their business effects. It uses stock k6 to apply a seeded traffic schedule, collects assertion evidence, and writes a JSON verdict and a standalone HTML report.

For example, a checkout scenario retries a payment and verifies that the ledger contains exactly one charge. An inventory scenario sends competing reservations and checks that accepted reservations never exceed capacity.

Use it while changing an API, from a coding-agent harness, or in CI. Your application must already be running, and its endpoints must match the scenario you provide.

> **Developer preview — `0.1.0.dev0`.** The current CLI provides `doctor`, `inspect`, `verify`, `up` (repeated bounded slices until Ctrl-C, no verdict), `approve` (local scenario-digest approvals), `diff`, `prune` (run retention), and a local read-only `dashboard` for browsing run artifacts on `127.0.0.1`. Scenarios are handwritten and reviewed. Automatic repository discovery, AI authoring, persistent-user population mode, and sandbox provisioning are planned, not implemented.

## What it does

- Runs stateful k6 journeys with identities, response chaining, retries, and declared business assertions.
- Resolves explicit phases or seeded **spiky**, **random-burst**, and **sustained-burst** traffic profiles.
- Checks declared request, write, and duration budgets before running; after the run, checks requests, writes, k6 `vus_max` against the concurrency budget, and artifact size, and reconciles delivered journeys and collected evidence.
- Supports optional run-owned fixture creation/cleanup and a final same-origin HTTP observer.
- Preserves run metadata, the manifest, metrics, assertion events, diagnostics, and a readable report.
- Repeats across consecutive (or the same) seeds and compares compatible baseline/candidate runs, with an optional p95 regression gate.
- Targets localhost, reachable HTTP/HTTPS previews, or an existing E2B sandbox's application port.

The vision is **users as an API**: give an application a believable population and return evidence about the system they exercised. [Current scope and next steps](docs/roadmap.md) explain how the preview fits that goal.

## Requirements

| Requirement | Details |
|---|---|
| Python | 3.11 or newer; a virtual environment is recommended |
| k6 | **Exactly v2.2.0**, installed separately and available on `PATH` or through `--k6-path` |
| Operating system | Linux is the verified execution environment; other platforms are not yet release-qualified |
| Target | A running, reachable HTTP/JSON app with a reviewed scenario for its API |
| Test data | An isolated target or run-owned fixtures with suitable test credentials |
| Disk | A writable artifact directory; defaults to `.litetraffic/runs/` |

Running existing scenarios needs **no OpenAI key, E2B API key, or hosted LiteTraffic account**. Python dependencies are installed by pip. [Detailed requirements and k6 installation](docs/installation.md).

## First run

Install k6 v2.2.0 using the [installation guide](docs/installation.md), then:

```bash
git clone https://github.com/rohansx/litetraffic.git
cd litetraffic
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
litetraffic doctor
litetraffic inspect examples/checkout --seed 42
```

Start the included demonstration app in this terminal:

```bash
python examples/checkout/server.py --port 8765
```

In a second terminal, from the same checkout:

```bash
. .venv/bin/activate
litetraffic verify examples/checkout \
  --target http://127.0.0.1:8765 --seed 42 --json
```

Expected result: `verdict: "pass"`, 12 completed journeys, and 36 HTTP requests. Each run is saved under `.litetraffic/runs/<run_id>/`; open its `report.html` to read the evidence.

To see a real failure, stop the demo server with Ctrl+C and restart it with `--wrong-duplicate`. Run the same verification again. The HTTP calls still succeed, but the duplicate payment effect produces `verdict: "fail"`, and each failed `one_effect_per_payment` sample records the expected and actual effect count:

```json
{"actual": 2, "detail": null, "expected": 1, "logical_key": "<run_id>-traffic-0", "sequence": 2}
```

The [quickstart](docs/quickstart.md) covers reports, repeated seeds, comparisons, and all five examples. The demo server is intentionally small and is not a production service.

## Included scenarios

| Scenario | Behavior exercised | Deliberate fault |
|---|---|---|
| [Checkout](examples/checkout) | Payment retry and readback | Duplicate charge on retry |
| [Inventory](examples/inventory) | Concurrent reservations under spiky arrivals | Overselling |
| [Reporting](examples/reporting) | Complete rows/totals under random bursts | Partial response or wrong final ledger |
| [Cached search](examples/cached_search) | Hot/cold reads, update, then read again | Permanently stale cache |
| [Tenant API](examples/tenant_api) | Positive own-record reads and forbidden cross-tenant reads | Data leak or deny-all shortcut |

## Before using your own app

The bundled scenarios exercise specific demonstration APIs. Changing `--target` alone does not adapt them to a different app. Review the [scenario authoring guide](docs/scenarios.md) and implement the journeys, test authentication, data setup, and business expectations your application requires.

Secrets stay in environment variables: a manifest names them (`secret_env`, `bearer_token_env`, `headers_env`) and never holds a value. For HS256 JWT auth, an actor's `auth` recipe makes the controller sign a token per actor class from the named secret and pass it to k6 as `LT_TOKEN_<CLASS>`; the token and secret are replaced with `[redacted]` in the k6 logs LiteTraffic keeps. See [actor auth](docs/scenarios.md#actor-auth).

Scenarios are executable, trusted code. Manifest budgets are not a sandbox for arbitrary JavaScript; target-side ownership and isolation still matter. The scenario digest covers the manifest, the script, and its relative imports, but the files are recorded by hash, not copied, so preserve the reviewed scripts separately. [Safety and current limitations](docs/safety.md).

## Documentation

Start at **[docs/README.md](docs/README.md)** for installation, usage, CLI options and exit codes, profiles, fixture/observer contracts, reports, architecture, troubleshooting, and the roadmap. This is a CLI project; `/docs` is the repository documentation entry point, not an HTTP endpoint.

## Development

```bash
python -m pip install -e '.[dev]'
python -m pytest -q
```

The test suite uses controlled engine doubles and does not require a live target or real k6. The `real_k6`-marked conformance test (`tests/test_conformance.py`) starts every example server, reference and each wrong flag, and expects `pass` and `fail` respectively; it is skipped unless k6 v2.2.0 is on `PATH`, and default CI runs `pytest -m "not real_k6"`. Run it alone with `python -m pytest -q -m real_k6`. See [CONTRIBUTING.md](CONTRIBUTING.md) for development and packaging checks.

## License

LiteTraffic is licensed under [MIT](LICENSE). k6 is a separately installed tool with its own AGPL-3.0 license and is not bundled here. [Third-party notices](THIRD_PARTY_NOTICES.md) explain the dependency boundaries.
