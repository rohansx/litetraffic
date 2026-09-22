# CLI reference

[Documentation index](README.md)

All scenario arguments are **directory paths** containing `manifest.json` and the named script. `inspect` and `verify` take the scenario either positionally or as `--scenario DIR`; the two spellings are equivalent, and giving both or neither exits `3`. Run `litetraffic COMMAND --help` for the installed version's syntax. The following describes `0.1.0.dev0`.

## `doctor`

```text
litetraffic doctor [--target URL] [--k6-path PATH] [--output-dir PATH] [--json]
```

Runs these checks, each reported with `name`, `ok`, and `detail`:

| Check | Passes when |
|---|---|
| `k6` | The executable runs and reports exactly v2.2.0 |
| `python` | The interpreter is Python 3.11 or newer |
| `output_dir` | `--output-dir` (default `.litetraffic/runs`) is writable, or would be creatable under its nearest existing parent; nothing is created |
| `disk` | The filesystem holding the output directory has at least 100 MiB free; `detail` reports free bytes |
| `target` | Only with `--target`: the GET returns a 2xx or 3xx status |

With a target, it first applies the same URL checks as `verify` (link-local and metadata targets exit `3` without a request), then sends one GET with a three-second HTTP timeout and no redirect following. Any other status fails with `reachable but not ready (HTTP N)`. This command does not install dependencies or write to the target.

## `inspect`

```text
litetraffic inspect (SCENARIO | --scenario SCENARIO) [--seed INTEGER] [--json]
```

Validates the manifest and script path and resolves the schedule. `--seed` defaults to `0`. Returns scenario name, schema version, script path, planned journeys, request/write maxima, resolved phases, and assertion IDs. The JSON form also includes `actors` (class, count, auth recipe), all five `budgets`, `fixture` (`recipe` and, for an owned HTTP fixture, its `create_path`/`delete_path`, otherwise `null`), `secret_env` (the sorted `bearer_token_env` names referenced by the fixture and observation; values are never read or printed), `observer`, and `observation_path` (`null` without an observation). Does not execute the scenario or infer routes.

## `verify`

```text
litetraffic verify (SCENARIO | --scenario SCENARIO)
  [--target URL | --e2b-sandbox-id ID --e2b-port PORT]
  [--output-dir PATH] [--k6-path PATH]
  [--seed INTEGER] [--repeat COUNT] [--json]
```

Exactly one target form is required. URL targets must use HTTP/HTTPS, must not contain URL credentials, and must not be a link-local or cloud-metadata address (for example `169.254.169.254`, `fe80::/10`, or `metadata.google.internal`); such targets exit `3`. Loopback targets such as `localhost` and `127.0.0.1` are allowed. Hostnames are not resolved, so this check covers literal addresses and known metadata names only. k6 runs with `--max-redirects 0`. E2B coordinates must include both sandbox ID and a port from 1 through 65535. Prefer an origin URL; the controller appends declared fixture/observation paths to it.

| Option | Default | Meaning |
|---|---|---|
| `--output-dir` | `.litetraffic/runs` | Parent for unique run directories and repeat summaries |
| `--k6-path` | Find `k6` on `PATH` | Executable; `verify` accepts only v2.2.0 |
| `--seed` | `0` | Seed used to resolve timing and supplied to the script |
| `--repeat` | `1` | Positive count; values above 1 run consecutive seeds |
| `--json` | Off | Emit one JSON object to stdout |

One run validates inputs, optionally creates a fixture, runs k6, collects evidence, optionally observes final state, attempts configured cleanup, and writes artifacts. Lifecycle and business verdict are separate fields. Ctrl+C during engine execution finalizes available evidence and exits 130. [Results](results.md) and [safety](safety.md).

## `diff`

```text
litetraffic diff BASELINE_DIR CANDIDATE_DIR
  [--max-p95-regression-percent NUMBER] [--json]
```

Reads `run.json` and `result.json` from both directories. Does not contact a target. Compatibility requires equal manifest digest, seed, full engine version string, and resolved schedule. A supplied p95 gate must be finite and non-negative and needs at least 200 duration samples in each run. Without the gate, timing differences are descriptive. See [results](results.md) for correctness and comparison limitations.

## Exit codes

The current commands have different exit mappings. Integrations should inspect the JSON verdict as well as the shell status.

| Command | Exit 0 | Exit 1 | Exit 2 | Exit 3 | Exit 130 |
|---|---|---|---|---|---|
| `doctor` | Checks succeeded | — | CLI usage error | Failed check/configuration error | — |
| `inspect` | Valid manifest | — | CLI usage error | Invalid scenario | — |
| `verify` | `pass` | `fail` | `inconclusive`; also CLI usage error | `error`; also configuration/engine preflight error | Cancelled run |
| `diff` | Comparison `pass` | Comparison `fail` | Inconclusive comparison or CLI usage error | Invalid arguments/artifacts | — |

With `--repeat`, `verify` applies the same mapping to the aggregate series verdict.

Without `--json`, `verify`, `diff`, and `inspect` print a short text summary meant for people, not parsers:

- `verify`: `verdict: PASS  lifecycle: finished  journeys: 20/20`, then one `ID  STATUS  SAMPLES` line per assertion, each limitation as a `- ...` line, and `report: PATH` to the run's `report.html`. With `--repeat`, a series line (`runs: COMPLETED/REQUESTED  consistent: yes|no`), one `seed N  VERDICT  LIFECYCLE  journeys: D/P` line per run, and `summary: PATH` to the series JSON.
- `diff`: `verdict: ...`, each reason as `- ...`, `compatibility: comparable` or `compatibility: incompatible (FIELDS)`, one `regression: ID` line per assertion that passed in the baseline but not the candidate, and `p95: BASELINEms -> CANDIDATEms (+X%)  status: STATUS` (`n/a` when a value is missing).
- `inspect`: name, script, planned journeys, request/write maxima, one `phase:` line per resolved phase, and one `assertion:` line per assertion ID.

`doctor` and error results print `key: value` lines. The text layout may change; integrations should use `--json`.

`--json` formats handled command results and errors. `argparse` usage errors can still print text to stderr and exit 2 before JSON handling. Do not assume every possible process failure produces JSON.

## Runtime environment passed to k6

| Variable | Value |
|---|---|
| `LT_RUN_ID` | Unique run identifier |
| `LT_TARGET` | Resolved target URL |
| `LT_SEED` | Decimal seed |
| `LT_MAX_IN_FLIGHT` | Declared concurrency envelope; scripts must apply it |
| `LT_SCHEDULE_JSON` | Resolved list of phase objects |
| `LT_FIXTURE_ID` | Present only after successful configured fixture creation |

The controller inherits the caller's environment. Supply only the credentials the reviewed scenario needs. Optional fixture and observation `bearer_token_env` fields name environment variables; their values belong outside manifests and version control.
