# LiteTraffic

LiteTraffic is a standalone tool for describing believable, bounded application traffic and verifying its effects. The product architecture lives in [docs/TECH_SPEC.md](docs/TECH_SPEC.md).

The M0 vertical slice validates portable scenario bundles, runs stock k6 as a bounded child process, captures structured assertion evidence, and produces a durable JSON verdict.

## Development setup

Python 3.11 or newer and k6 v2.2.0 are required. LiteTraffic checks the engine version before every run.

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
pytest -q
```

Inspect the included scenario:

```bash
litetraffic inspect examples/checkout
litetraffic inspect examples/checkout --json
litetraffic inspect examples/inventory --seed 42 --json
```

Check local prerequisites and an optional running target:

```bash
litetraffic doctor
litetraffic doctor --target http://localhost:3000/health
```

Run the checkout conformance example with a separately installed k6 binary:

```bash
python examples/checkout/server.py --port 8765
litetraffic verify examples/checkout --target http://127.0.0.1:8765 --json
```

Restart the server with `--wrong-duplicate` to confirm the same scenario detects a broken idempotency implementation. `doctor` only makes a GET request and does not install software.

Run the seeded spiky inventory contention example:

```bash
python examples/inventory/server.py --port 8766
litetraffic verify examples/inventory --target http://127.0.0.1:8766 --seed 42 --json
```

Restart it with `--wrong-oversell` to verify that LiteTraffic detects negative inventory and accepted reservations above capacity.

Each verification writes an owner-restricted directory under `.litetraffic/runs/` containing frozen inputs, raw engine diagnostics, k6 metric JSONL, sequenced assertion events, and `result.json`.

Runs record `finished`, `timed_out`, `cancelled`, or `crashed` independently from the business verdict. Timeout and Ctrl+C terminate the k6 process group on POSIX systems, preserve available evidence, and can never produce a passing verdict. Ctrl+C returns shell status 130 after finalization.

## Scenario safety contract

- Manifests are JSON and reject unknown fields.
- Scenario scripts must remain inside the bundle directory.
- Schedule rates are journey admissions per second, not HTTP requests per second.
- Schedules may contain explicit phases or a deterministic `spiky` profile resolved from `--seed`.
- Maximum requests and writes are derived conservatively from every admitted journey.
- A bundle that exceeds its declared duration, request, or write budget is rejected.
- Runtime secrets and target URLs do not belong in portable manifests.
