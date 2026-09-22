# Architecture

[Documentation index](README.md)

LiteTraffic is a Python controller around a stock k6 child process. There is no server, database, or background daemon.

```text
            litetraffic verify
                    │
   manifest.json ──▶│ validate + resolve schedule (seed)
                    │
                    ├── POST fixture ───────────────▶┐
                    │                                │
                    ├── k6 run journeys.js ─────────▶│  your running app
                    │     (LT_* env, LT_EVENT logs)  │
                    ├── GET observation ────────────▶│
                    ├── DELETE fixture ─────────────▶┘
                    │
                    ▼
       .litetraffic/runs/<run_id>/  →  result.json, report.html
```

## Run sequence

1. **Load** — `scenario.py` parses the manifest with Pydantic (`models.py`), checks the script and its relative import closure stay inside the bundle, computes the bundle digest, and checks budgets against the worst case.
2. **Preflight** — `runner.py` requires k6 v2.2.0 and a credential-free HTTP(S) target. `e2b.py` can resolve an E2B sandbox ID and port to a URL.
3. **Fixture** — optional `POST` via `fixture.py`; the returned ID is passed to k6.
4. **Engine** — k6 runs in its own process group with a deadline derived from `max_seconds`. Console output and JSON metrics go to files in the run directory.
5. **Evidence** — `LT_EVENT` lines are parsed and validated against the run ID; metrics are aggregated (p50/p95/max, error rate, iterations).
6. **Observation** — optional final `GET` via `observation.py`, compared by JSON Pointer.
7. **Cleanup** — optional fixture `DELETE`.
8. **Verdict** — assertions, completeness, lifecycle, and budgets combine into `pass`/`fail`/`inconclusive`/`error`. `report.py` renders the HTML report.

`compare.py` implements `diff` over two finished run directories. `doctor.py` implements prerequisite checks. `cli.py` maps all of it to commands and exit codes.

## Source layout

| Path | Purpose |
|---|---|
| `src/litetraffic/cli.py` | Argument parsing, JSON/text output, exit codes |
| `src/litetraffic/models.py` | Manifest schema, profiles, schedule resolution |
| `src/litetraffic/scenario.py` | Bundle loading and static budget checks |
| `src/litetraffic/runner.py` | Run lifecycle, evidence, verdict, repeat series |
| `src/litetraffic/process.py` | Engine subprocess waits and process-group termination |
| `src/litetraffic/fixture.py` | Run-owned fixture create/cleanup |
| `src/litetraffic/observation.py` | Final observation and JSON Pointer lookup |
| `src/litetraffic/compare.py` | Baseline/candidate comparison |
| `src/litetraffic/report.py` | Standalone HTML report |
| `src/litetraffic/doctor.py` | Prerequisite checks |
| `src/litetraffic/e2b.py` | E2B target URL resolution |
| `examples/` | Five conformance scenarios with correct and faulty demo servers |
| `tests/` | Unit and CLI tests using engine doubles |

## Design choices

- **Stock k6, pinned.** No custom engine build; exact version pinning keeps metrics and behavior comparable.
- **Evidence through logs.** Scripts report assertions on stdout, so no k6 extension is needed.
- **Verdict separate from lifecycle.** A run can finish and fail, or time out and still record a definite failure.
- **Local artifacts.** Everything a verdict depends on is written to disk for review and comparison.

The [original technical specification](TECH_SPEC.md) describes the wider product direction, which includes parts that are not implemented yet.
