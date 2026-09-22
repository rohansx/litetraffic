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

Run the seeded random-burst reporting example:

```bash
python examples/reporting/server.py --port 8767
litetraffic verify examples/reporting --target http://127.0.0.1:8767 --seed 42 --json
```

For an application already running on an E2B sandbox, pass its sandbox ID and exposed application port instead of constructing a URL:

```bash
litetraffic verify examples/reporting \
  --e2b-sandbox-id <sandbox-id> --e2b-port 8767 --seed 42 --json
```

LiteTraffic resolves this to E2B's external HTTPS host and leaves sandbox creation, application startup, and sandbox destruction with the caller. It does not require the E2B SDK or API key at runtime.

Restart it with `--wrong-partial` to verify that a fast HTTP 200 response still fails when report totals, rows, or regional data are incomplete.
Restart it with `--wrong-ledger` to verify the final read-only observer catches incorrect persisted totals. A bundle may declare one same-origin `observation` with JSON Pointer expectations and an optional `bearer_token_env`; its extra request and five-second deadline must fit the manifest budgets. The token value stays in the environment and is never written to the manifest or run artifacts.

Run the cached-search sequence with an 83/17 hot/cold read split, an update, and a post-update read under a sustained burst:

```bash
python examples/cached_search/server.py --port 8768
litetraffic verify examples/cached_search --target http://127.0.0.1:8768 --seed 42 --json
```

Restart it with `--wrong-stale` to keep the warmed hot-key cache permanently stale. The journey assertions and final cache-state observer both detect the defect.

Run the tenant-isolation example with two identities that both own local record ID `1`:

```bash
python examples/tenant_api/server.py --port 8769
litetraffic verify examples/tenant_api --target http://127.0.0.1:8769 --seed 42 --json
```

Use `--wrong-leak` to expose tenant B's record to tenant A, or `--deny-all` to block legitimate reads too. Positive-access assertions ensure deny-all fails rather than appearing secure.

Use consecutive seeds to expose seed-sensitive or intermittent behavior:

```bash
litetraffic verify examples/reporting --target http://127.0.0.1:8767 --seed 42 --repeat 3 --json
```

Repeat mode preserves every normal run directory and writes a `series_*.json` summary with the aggregate verdict and whether outcomes were consistent.

Compare compatible baseline and candidate runs without contacting the target:

```bash
litetraffic diff .litetraffic/runs/<baseline> .litetraffic/runs/<candidate> --json
litetraffic diff .litetraffic/runs/<baseline> .litetraffic/runs/<candidate> \
  --max-p95-regression-percent 20 --json
```

The optional p95 gate requires at least 200 request samples in both runs. Without a gate, LiteTraffic reports latency, HTTP error rate, throughput, and progress without manufacturing a performance verdict. Scenario hash, seed, engine, and resolved schedule must match before performance is graded.

Each verification writes an owner-restricted directory under `.litetraffic/runs/` containing frozen inputs, raw engine diagnostics, k6 metric JSONL, sequenced assertion events, `result.json`, and a self-contained `report.html`. Bundles with a final observer write `observation.json` containing only the declared expected and observed fields. The optional `fixtures.owned_http` contract creates one run-scoped fixture through a same-origin POST, passes its returned ID to k6 as `LT_FIXTURE_ID`, then deletes that ID after the final observation. Setup and cleanup share the run ID header, reserve two requests, two writes, and ten seconds, and produce `fixture.json`. Failed cleanup makes the verdict an error.

The target must enforce run ownership on its fixture endpoints. LiteTraffic deletes only the ID returned by its create call, including after a k6 crash, timeout, or Ctrl+C. A host crash or `SIGKILL` cannot run cleanup; targets should expire orphaned fixtures with a TTL.

Runs record `finished`, `timed_out`, `cancelled`, or `crashed` independently from the business verdict. Timeout and Ctrl+C terminate the k6 process group on POSIX systems, preserve available evidence, and can never produce a passing verdict. Ctrl+C returns shell status 130 after finalization.

## Scenario safety contract

- Manifests are JSON and reject unknown fields.
- Scenario scripts must remain inside the bundle directory.
- Schedule rates are journey admissions per second, not HTTP requests per second.
- Schedules may contain explicit phases or `spiky`, `random_bursts`, and `sustained_burst` profiles. Sustained bursts ramp to a plateau and drop immediately to a recovery rate.
- Profile resolution is deterministic for a seed and the exact phase sequence is frozen in `run.json`.
- Maximum requests and writes are derived conservatively from every admitted journey.
- A bundle that exceeds its declared duration, request, or write budget is rejected.
- Runtime secrets and target URLs do not belong in portable manifests.
