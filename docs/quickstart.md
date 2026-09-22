# Quickstart

[Documentation index](README.md) · [Installation](installation.md)

Run these commands from the cloned repository after installing Python dependencies and k6 v2.2.0. The examples need no API keys and bind only to loopback.

## 1. Inspect before running

```bash
litetraffic inspect examples/checkout --seed 42 --json
```

This validates the manifest, script location, and declared budgets and shows the resolved schedule. It does not send requests to the application or audit the JavaScript's behavior.

## 2. Run the correct checkout

In terminal A:

```bash
. .venv/bin/activate
python examples/checkout/server.py --port 8765
```

In terminal B:

```bash
. .venv/bin/activate
litetraffic verify examples/checkout \
  --target http://127.0.0.1:8765 --seed 42 --json
```

The journey creates a payment, repeats it with the same idempotency key, and reads the effect. Expect a complete `pass`, 12 journeys, and 36 requests. The printed `run_id` identifies `.litetraffic/runs/<run_id>/report.html` and `result.json`. Timing and generated run IDs vary.

## 3. Prove the test detects a defect

Stop terminal A with Ctrl+C. Restart the server:

```bash
python examples/checkout/server.py --port 8765 --wrong-duplicate
```

Run the same verification in terminal B. Expect `fail`, shell exit 1, and failed `one_effect_per_payment` evidence. This is an intended failure: responses can be HTTP 200 while the business effect is wrong.

## 4. Compare two runs

Replace the placeholders with the `run_id` values printed above:

```bash
litetraffic diff \
  .litetraffic/runs/<correct-run-id> \
  .litetraffic/runs/<broken-run-id> --json
```

Comparison reads local artifacts and does not contact the app. A correct baseline followed by the broken duplicate server should report a correctness regression. A diff `pass` means no regression under its implemented rules; it does not certify that a candidate run passed business verification. Check `correctness.candidate_verdict` as well.

An optional performance gate is available:

```bash
litetraffic diff .litetraffic/runs/<baseline> .litetraffic/runs/<candidate> \
  --max-p95-regression-percent 20 --json
```

It requires at least 200 request-duration samples in **each** run. The small checkout example intentionally has too few samples and cannot establish this latency gate. Use a reviewed larger schedule and compatible target resources. [Comparison semantics](results.md).

## 5. Try the other behaviors

Start one server in terminal A, then run its corresponding verification in terminal B. Stop the server before restarting it with a fault flag.

| Bundle | Server command | Fault flag |
|---|---|---|
| `examples/inventory` | `python examples/inventory/server.py --port 8766` | `--wrong-oversell` |
| `examples/reporting` | `python examples/reporting/server.py --port 8767` | `--wrong-partial` or `--wrong-ledger` |
| `examples/cached_search` | `python examples/cached_search/server.py --port 8768` | `--wrong-stale` |
| `examples/tenant_api` | `python examples/tenant_api/server.py --port 8769` | `--wrong-leak` or `--deny-all` |

For example:

```bash
litetraffic verify examples/reporting --target http://127.0.0.1:8767 --seed 42 --json
```

The reporting, cached-search, and tenant examples use explicit fixture creation, final observation, and cleanup. The checkout and inventory examples use their own script/server state conventions; they do not declare the controller's `owned_http` cleanup contract. Restart those demo servers to clear accumulated state.

## Repeated seeds

With the correct reporting server running:

```bash
litetraffic verify examples/reporting \
  --target http://127.0.0.1:8767 --seed 42 --repeat 3 --json
```

This runs seeds 42, 43, and 44. Every run keeps its own artifact directory, and a `series_*.json` file summarizes verdicts and consistency. It can expose seed-sensitive results; it is not statistical proof that all executions are correct.

## An existing E2B target

Deploy your reviewed app into an E2B sandbox and expose its application port using your existing E2B setup. Then:

```bash
litetraffic verify examples/reporting \
  --e2b-sandbox-id <sandbox-id> --e2b-port 8767 --seed 42 --json
```

The resolver constructs `https://<port>-<sandbox-id>.e2b.app`. You own sandbox creation, app startup, authentication, and teardown. This command does not call the E2B control API. For another reachable deployment or a custom host, supply `--target` instead.

## Your own app

Copy a relevant bundle, adapt its API calls and expected business results, then validate it against both a correct implementation and a controlled fault. Follow the [scenario guide](scenarios.md) before allowing writes.
