# CLI reference

[Documentation index](README.md)

All scenario arguments are **directory paths** containing `manifest.json` and the named script. `inspect`, `verify`, `up`, and `approve` take the scenario either positionally or as `--scenario DIR`; the two spellings are equivalent, and giving both or neither exits `3`. Run `litetraffic COMMAND --help` for the installed version's syntax. The following describes `0.1.0.dev0`.

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

Validates the manifest and script path and resolves the schedule. `--seed` defaults to `0`. Returns scenario name, schema version, script path, planned journeys, request/write maxima, resolved phases, and assertion IDs. The JSON form also includes `scenario_sha256` (the bundle digest), `actors` (class, count, auth recipe, and the `auth` object when declared), all five `budgets`, `fixture` (`recipe`, and `owned_http` holding `create_path`/`delete_path` for an owned HTTP fixture, otherwise `null`, and `command` holding `setup`, `teardown`, `timeout_seconds` and `cwd` for a command fixture, otherwise `null`), `secret_env` (the sorted `bearer_token_env` names referenced by the fixture and observations, plus the observations' `headers_env` variable names and each actor `auth.secret_env`; values are never read or printed), `observer`, `observation_path` (the first observation's path, `null` without one), `observation_expected` (the first observation's `expected` values with `${...}` expressions resolved for `--seed`; `null` without one), and `observations` (`{assertion, path, expected}` for every observation, resolved the same way; each is one `observation ID expected: ...` line in the human output). `maximum_observation_requests` is the number of observations. Does not execute the scenario or infer routes.

## `verify`

```text
litetraffic verify (SCENARIO | --scenario SCENARIO)
  [--target URL | --e2b-sandbox-id ID --e2b-port PORT]
  [--output-dir PATH] [--k6-path PATH]
  [--seed INTEGER] [--repeat COUNT [--same-seed]]
  [--require-approval] [--approved-digest SHA] [--json]
```

Exactly one target form is required. URL targets must use HTTP/HTTPS, must not contain URL credentials, and must not be a link-local or cloud-metadata address (for example `169.254.169.254`, `fe80::/10`, or `metadata.google.internal`); such targets exit `3`. Loopback targets such as `localhost` and `127.0.0.1` are allowed. Hostnames are not resolved, so this check covers literal addresses and known metadata names only. k6 runs with `--max-redirects 0`. E2B coordinates must include both sandbox ID and a port from 1 through 65535. Prefer an origin URL; the controller appends declared fixture/observation paths to it.

| Option | Default | Meaning |
|---|---|---|
| `--output-dir` | `.litetraffic/runs` | Parent for unique run directories and repeat summaries |
| `--k6-path` | Find `k6` on `PATH` | Executable; `verify` accepts only v2.2.0 |
| `--seed` | `0` | Seed used to resolve timing and supplied to the script |
| `--repeat` | `1` | Positive count; values above 1 run consecutive seeds |
| `--same-seed` | Off | With `--repeat` of 2 or more, run `--seed` every time instead of consecutive seeds |
| `--require-approval` | Off | Exit `3` before running unless `.litetraffic/approvals.json` (in the working directory) has a record with the scenario's current digest and the target's origin |
| `--approved-digest` | None | Exit `3` before running unless SHA equals the scenario's current digest; when it matches, the approvals file is not consulted (for CI) |
| `--json` | Off | Emit one JSON object to stdout |

One run validates inputs, optionally creates a fixture, runs k6, collects evidence, optionally observes final state, attempts configured cleanup, and writes artifacts. Lifecycle and business verdict are separate fields. Ctrl+C during engine execution finalizes available evidence and exits 130. [Results](results.md) and [safety](safety.md).

## `up`

```text
litetraffic up (SCENARIO | --scenario SCENARIO) --target URL [--output-dir DIR]
  [--k6-path PATH] [--seed N] [--max-slices N] [--json]
```

Runs a background activity in the foreground: the same bounded run as `verify`, repeated as slices until Ctrl+C or, with `--max-slices N`, after `N` slices. Slice seeds count up from `--seed` (default `0`). Target, scenario and k6 are checked before anything is created; a failure exits `3`. `--max-slices` below 1 exits `3`.

Each activity gets `OUTPUT_DIR/activity_<UTC>_<hex>/` (mode `0700`) holding `activity.json` and one ordinary run directory per slice. `activity.json` is rewritten after every slice and once more when the activity ends:

| Field | Meaning |
|---|---|
| `mode` | Always `background` |
| `status` | `running`, then `completed` (reached `--max-slices`), `stopped` (Ctrl+C, or a slice was cancelled) or `error` (a slice raised; the command exits `3`) |
| `artifact_dir` | The activity directory |
| `slices` | Per slice: `run_id`, `seed`, `lifecycle`, `finished_at`, `iterations`, `http_reqs` |

Also recorded: `activity_id`, `scenario`, `scenario_sha256`, `target`, `starting_seed`, `max_slices`, `started_at`, `finished_at`. There is no `verdict` field: slice verdicts stay in each slice's own `result.json`, and a failing slice does not stop the activity or change the exit status. Progress goes to stderr: `activity: PATH` first, one `slice N: RUN_ID LIFECYCLE` line per slice, and `status: STATUS` last. `--json` prints the final `activity.json` content on stdout. Ctrl+C finalizes `activity.json` and exits `0`. Activities are not seen by `diff` or `prune`.

## `approve`

```text
litetraffic approve (SCENARIO | --scenario SCENARIO) --target-profile NAME --target URL [--json]
```

Validates the scenario like `inspect` and records `{digest, profile, target_origin, approved_at}` in `.litetraffic/approvals.json` under the working directory (as `{"approvals": [...]}`), then prints the record. `digest` is the bundle digest over the canonical manifest and every file in the script's relative import closure, so editing `journeys.js` (or anything it imports, or the manifest) after approval invalidates it. `target_origin` is `scheme://host[:port]` in lower case, with the path and a default port dropped; the target must be an HTTP/HTTPS URL without credentials. Re-approving the same digest, profile and origin replaces that record; other records are kept. `verify --require-approval` matches on digest and origin only. An approvals file that is not JSON, lacks an `approvals` list, or holds a record without string `digest`, `profile` and `target_origin` is invalid and exits `3` for both `approve` and `verify --require-approval`. Nothing is sent to the target and no key signs the file.

## `diff`

```text
litetraffic diff BASELINE CANDIDATE [--runs-dir DIR]
  [--max-p95-regression-percent NUMBER] [--json]
```

`BASELINE` and `CANDIDATE` are each a run directory or a bare run ID such as `run_20260101T000000Z_ab12cd34`. An argument naming an existing directory is used as-is; otherwise it is looked up as a subdirectory of `--runs-dir` (default `.litetraffic/runs`). An unknown run ID, or one naming a symlink under `--runs-dir`, exits 3. Reads `run.json` and `result.json` from both directories and exits 3 if either is a symlink. Does not contact a target. Compatibility requires equal manifest digest, seed, full engine version string, and resolved schedule. A supplied p95 gate must be finite and non-negative and needs at least 200 duration samples in each run. Without the gate, timing differences are descriptive. See [results](results.md) for correctness and comparison limitations.

## `dashboard`

```text
litetraffic dashboard [--runs-dir DIR] [--port PORT]
```

Serves a read-only web page over `--runs-dir` (default `.litetraffic/runs`) on `http://127.0.0.1:PORT/` (default port `8780`, clear of the example servers' ports 8765-8769; `0` picks a free port; a port outside 0-65535 is a usage error and exits 2). It always binds to `127.0.0.1`; there is no `--host` option. It prints the URL, uses only the Python standard library, and does not contact a target.

| Route | Shows |
|---|---|
| `/` | Runs, series and `up` activities, newest first: a selection checkbox (runs only), run ID, kind, scenario (linked to its trend page), lifecycle (an activity's status), seed, finished time, and a verdict badge with the verdict as text (an activity shows a `background` badge instead). Ticking exactly two runs enables **Compare**, which opens `/diff` with the older ticked run as baseline. Filters: `?scenario=NAME`, `?verdict=VERDICT` (`pass`, `fail`, `inconclusive`, `error`, `unreadable`, `background`) and `?seed=N`, combinable; each must match exactly. The table refreshes from `/api/runs` (with the same filters) every 5 seconds without reloading the page, keeping ticked boxes, and rebuilds the **Scenario** filter options from `/api/scenarios` so newly recorded scenarios appear; untick **Auto-refresh** to stop |
| `/runs/ACTIVITY_ID` | An activity's `background` badge, status, target, starting seed and slice table; never a verdict |
| `/runs/RUN_ID` | Verdict badge, lifecycle, seed, limitations, assertion table, failing samples with expected/actual values (a value longer than 300 characters is collapsed into an expandable preview of its first 80), an operations table (per-operation samples, p95, failed rate from `metrics.by_operation` and peak in flight from `metrics.overlap`), all metrics, a link to `report.html` when present, and a link to every file in the run directory |
| `/runs/RUN_ID/PATH` | A file under the run directory (for example `report.html`, `result.json`, `events/000001.jsonl`): `.html` as HTML, `.json` as JSON, anything else as plain text |
| `/scenarios/NAME` | For runs whose scenario is `NAME`, oldest first: an inline SVG chart of p95 HTTP latency per run with a marker per run coloured by verdict (runs spaced evenly, not on a time scale; runs without a p95 are left off the chart), and a table of run, finished time, seed, verdict and p95. 404 when no run has that scenario |
| `/diff?a=BASELINE&b=CANDIDATE` | The `diff` text summary for two run IDs; incompatible runs are `INCONCLUSIVE`. `/diff?run=NEWER&run=OLDER` (what **Compare** sends) is the same with the second ID as baseline |
| `/api/runs` | The run index as JSON; accepts the same `scenario`, `verdict` and `seed` filters as `/` |
| `/api/scenarios` | Every scenario name in the run index as a sorted JSON list, ignoring filters |

Every route also answers `HEAD` with the same status and headers and no body. Links percent-encode run IDs and scenario names, so names containing `#` or `?` work. Files are served as their raw bytes, so a `report.html` that is not valid UTF-8 is still delivered. A run directory deleted while the index is being built does not fail the page. The trend chart's y-axis label sits above the plot area, and chart text is enlarged on screens up to 560px wide so it renders at 11px or more at 375px.

Pages follow the system light/dark preference; the **Theme** button switches between them and the choice is kept in the browser's `localStorage` (when storage is unavailable the button still works for the current page). Pages fit a 375px-wide screen, with wide tables scrolling inside their own box, and every control is a native link, button, checkbox, select or input reachable by keyboard.

To block DNS rebinding, every request must carry a `Host` header of exactly `127.0.0.1:PORT`, `localhost:PORT` or `[::1]:PORT` (the bound port, name case-insensitive); any other `Host`, including a missing one or one without the port, gets `421 Misdirected Request` and no run data. Run IDs must be plain subdirectory names of `--runs-dir`; an ID containing `/`, `\`, or `..`, an unknown ID, and any other path return 404. A file path under a run must not contain `.` or `..` segments, symlinks are not followed, and nothing outside the run directory is served. Symlinks are never followed when reading run data either: a symlinked run directory or `series_*.json` under `--runs-dir` is not listed, and a `run.json`, `result.json` or `activity.json` that is a symlink is treated as missing, so such a run shows as `unreadable` and `diff` refuses it. All values are HTML-escaped, including scenario names in pages, links and the chart. A `result.json` field with the wrong type (for example `"metrics": null`), including an assertion's `failures` list and any non-object sample in it, is shown as empty rather than failing the page. A `verdict` that is not one of the known strings (for example `[]`, `{}`, `5` or `null`) makes that run or series `unreadable` in the index, and `diff` refuses it as an invalid result artifact. Ctrl+C stops the server and exits 130. A port that cannot be bound exits 3.

## `prune`

```bash
litetraffic prune [--runs-dir DIR] [--keep N] [--older-than DAYS] [--dry-run] [--json]
```

Deletes old run directories under `--runs-dir` (default `.litetraffic/runs`). At least one of `--keep` and `--older-than` is required; giving neither, a negative `--keep`, or a negative or non-finite `--older-than` exits `3`. An `--older-than` too large to represent as a date deletes nothing. When both are given, a run is deleted if either rule selects it.

| Option | Meaning |
|---|---|
| `--keep N` | Keep the newest `N` runs; delete the rest |
| `--older-than DAYS` | Delete runs that finished more than `DAYS` ago (fractions allowed) |
| `--dry-run` | List what would be deleted; delete nothing |
| `--json` | Emit `{"ok": true, "dry_run": ..., "pruned": [PATH, ...]}` |

Only real directories directly under `--runs-dir` that contain a real (non-symlink) `run.json` are candidates; a symlinked `result.json` is ignored and deleting a run removes the link, never its target. Nested directories, symlinks, other directories, and `series_*.json` summaries are never deleted. Runs are ordered by `finished_at` in `result.json`; a run without a readable `finished_at` uses its directory modification time. Text output prints one `deleted: PATH` (or `would delete: PATH`) line per run, newest first, or `nothing to prune`. A missing `--runs-dir` prunes nothing. It does not check whether a run is still in progress.

## Exit codes

The current commands have different exit mappings. Integrations should inspect the JSON verdict as well as the shell status.

| Command | Exit 0 | Exit 1 | Exit 2 | Exit 3 | Exit 130 |
|---|---|---|---|---|---|
| `doctor` | Checks succeeded | — | CLI usage error | Failed check/configuration error | — |
| `inspect` | Valid manifest | — | CLI usage error | Invalid scenario | — |
| `verify` | `pass` | `fail` | `inconclusive`; also CLI usage error | `error`; also configuration/engine preflight error, missing/mismatched approval or invalid approvals file | Cancelled run |
| `up` | Activity completed or stopped with Ctrl+C | — | CLI usage error | Configuration/engine preflight error, invalid `--max-slices`, or a slice raised | — |
| `approve` | Approval recorded | — | CLI usage error | Invalid scenario, target or approvals file | — |
| `diff` | Comparison `pass` | Comparison `fail` | Inconclusive comparison or CLI usage error | Invalid arguments/artifacts | — |
| `dashboard` | — | — | CLI usage error | Port cannot be bound | Stopped with Ctrl+C |
| `prune` | Pruned (or listed with `--dry-run`) | — | CLI usage error | Invalid retention options or deletion failed (runs deleted before the failure stay deleted) | — |

With `--repeat`, `verify` applies the same mapping to the aggregate series verdict.

Without `--json`, `verify`, `diff`, and `inspect` print a short text summary meant for people, not parsers:

- `verify`: `verdict: PASS  lifecycle: finished  journeys: 20/20`, then one `ID  STATUS  SAMPLES` line per assertion (followed by `  (REASON)` when an unknown final observation recorded one), an `unexpected HTTP failure rate: X% (FAILED/SAMPLES)` line when the run has `metrics.unexpected_http_failure_rate`, each limitation as a `- ...` line, and `report: PATH` to the run's `report.html`. With `--repeat`, a series line (`verdict: VERDICT  lifecycle: LIFECYCLE  runs: COMPLETED/REQUESTED  consistent: yes|no`), one `seed N  VERDICT  LIFECYCLE  journeys: D/P` line per run, and `summary: PATH` to the series JSON.
- `diff`: `verdict: ...`, each reason as `- ...`, `compatibility: comparable` or `compatibility: incompatible (FIELDS)`, one `regression: ID` line per assertion that passed in the baseline but not the candidate, and `p95: BASELINEms -> CANDIDATEms (+X%)  status: STATUS` (`n/a` when a value is missing), `unexpected HTTP failure rate: B% -> C% (+Npp)` when either run has that metric, then one `p95 OPERATION: BASELINEms -> CANDIDATEms (+X%)` line per operation present in both runs.
- `inspect`: name, script, planned journeys, request/write maxima, one `phase:` line per resolved phase, one `assertion:` line per assertion ID, and `fixture setup:`/`fixture teardown:` lines with the space-joined argv of a command fixture.

`doctor` and error results print `key: value` lines. The text layout may change; integrations should use `--json`.

An `OSError` raised while a command runs (for example an unreadable file) is reported as an error result and exits `3`. `--json` formats handled command results and errors. `argparse` usage errors can still print text to stderr and exit 2 before JSON handling. Do not assume every possible process failure produces JSON.

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
