# Results, reports, and comparisons

[Documentation index](README.md)

## Where results go

The default output is `.litetraffic/runs/`. Every invocation gets a unique `run_<timestamp>_<suffix>` directory. `verify --json` prints its `run_id`; combine that with your output directory to locate the files.

| File | Contents |
|---|---|
| `run.json` | Target, seed, bundle digest (`scenario_sha256`), engine, resolved schedule, run and engine-window timestamps (`engine_started_at`, `engine_finished_at` when k6 was launched), lifecycle |
| `scenario.lock.json` | `{"manifest", "engine", "files"}`: the parsed manifest, the k6 version string, and the sha256 of the script and of every file in its relative import closure, keyed by path inside the bundle |
| `result.json` | Mode (`verify`), seed, verdict, completeness, assertion summaries, metrics, planned vs observed rate, limitations, notes |
| `report.html` | Standalone readable report (seed, target, engine, latency, assertions, limitations, notes); open in a browser |
| `events/000001.jsonl` | Validated, sequenced assertion events |
| `metrics.jsonl` | Raw k6 JSON metrics, if emitted |
| `console.log` | Raw k6 console output, if emitted |
| `engine.stdout.log`, `engine.stderr.log` | Captured engine diagnostics |
| `observation.json` | Optional final observer expectations, selected actual fields, per-pointer `checks` (matcher, actual, pass), and `expressions` (the original `${...}` forms of resolved expected values, only when used) |
| `fixture.json` | Optional fixture outcomes: `owned_http` create/cleanup, or `command` setup/teardown argv, exit code, duration and stderr tail |
| `artifacts.json` | Finalization manifest, written last: `{"schema_version", "run_id", "files", "total_bytes"}`, where `files` lists every other run file as `{path, bytes, sha256}` sorted by path and `total_bytes` is their sum. It excludes itself; the `max_artifact_bytes` budget is summed over the same file set |

The controller restricts permissions on directories and files it creates. Raw engine output may still contain anything the script logs. Treat the entire run directory as potentially sensitive and review it before sharing. Scripts and imported files are recorded by hash in `scenario.lock.json` but **not** copied into the run directory.

## Verdict and lifecycle

| Verdict | Meaning |
|---|---|
| `pass` | Declared assertions passed with complete expected evidence and no reported run limitation |
| `fail` | At least one declared business assertion definitely failed |
| `inconclusive` | Evidence/progress was incomplete without a definitive failure, such as a timeout |
| `error` | Engine/lifecycle, budget/fixture failure, or an unreachable target prevented a clean evaluation |

Lifecycle is `finished`, `timed_out`, `cancelled`, or `crashed`, independently of the verdict. While a run is in progress, `run.json` records its current stage as it happens: `preparing` (fixture setup), `running` (k6 launched), `observing` (final observer and fixture cleanup), then `finalizing`, before the final lifecycle is written. Ctrl-C at any stage finalizes the run as `cancelled` (CLI exit 130): a cancel during fixture create skips the engine; a cancel during the engine or observer still cleans up a created fixture, and the observer assertion becomes unknown; a cancel during fixture cleanup reports `fixture cleanup cancelled; fixture ID may remain` and makes the verdict `error`. For example, a timed-out run can retain a definite failing assertion; it can never pass. A timed-out run reports `engine stopped after its E-second share of the S-second budget`, where E is `max_seconds` minus the fixture (10 s) and observer (5 s) reserves the scenario declares. k6 exit status `99` (a script threshold was crossed) is not a crash: the run stays `finished`, reports the limitation `k6 thresholds breached`, and its verdict is `fail` unless an `error` condition applies. Any other non-zero k6 exit is `crashed`. An observer that cannot respond yields unknown evidence. A failed configured fixture cleanup makes the verdict `error`.

When k6 made requests but every `http_req_failed` point is a transport-level failure (k6 tag `status` of `"0"`, meaning no HTTP response arrived; HTTP 4xx/5xx responses also carry an `error_code` tag but are not counted as transport failures), the verdict is `error`, failing assertions become `unknown`, and `limitations` contains `target unreachable: all N requests failed before an HTTP response`. `metrics.http_req_failed_rate.transport` counts those failures when any occur. A run that receives any HTTP response, including HTTP 500, is judged normally.

For ordinary journey assertions, each declared assertion must have exactly one sample per planned journey. A final-observer assertion instead receives one aggregate observation. Missing samples and delivered/planned journey mismatches prevent a pass. Inspect `limitations`, `completeness`, and each assertion's sample count when diagnosing a result.

Each entry in `result.json` `assertions` has `id`, `status`, and `samples`. A failing journey assertion also has `failures`: up to the first three failing samples, each `{sequence, logical_key, expected, actual, detail}` taken from the event (`null` when the event omitted a field; `sequence` matches `events/000001.jsonl`). A final-observer assertion that was evaluated also carries the observer's `expected` and `actual` values from `observation.json`. When the target is unreachable, failing rows become `unknown` and drop these details. `report.html` shows a "Failing samples" table (sample, logical key, expected, actual, detail) for every failing assertion; all values are HTML-escaped.

Engine event or metric lines that cannot be parsed (invalid JSON, a non-object value, or a metric record whose `data` field is present but is not an object) are skipped and reported as an `ignored N malformed event record(s)` or `ignored N malformed metric record(s)` limitation; the run is still finalized and `result.json` is still written.

`metrics.write_attempts` counts write requests (k6 `http_reqs` tagged `POST`, `PUT`, `PATCH` or `DELETE`, plus fixture create and cleanup), and `metrics.vus_max` is the highest k6 `vus_max` point when k6 reports one. Exceeding `max_requests`, `max_write_attempts` or `max_in_flight` adds a `... budget exceeded: N > M` limitation and makes the verdict `error`; see [safety](safety.md).

`result.json` `metrics.http_req_duration_ms` holds `samples`, `average`, `p50`, `p95`, and `max` when k6 supplies durations. `metrics.by_operation` breaks HTTP points down by the k6 `operation` request tag: each entry holds `samples` (duration count), `p95` (ms), and `failed_rate` (share of `http_req_failed` points), with `null` when the operation had no points of that kind. Points without an `operation` tag are grouped under `_untagged`. The key is absent when k6 recorded no HTTP duration or failure points. `metrics.overlap` maps each operation (same `operation` tag grouping) to the peak number of its requests that were open at the same instant, computed from each timestamped `http_req_duration` point as the interval `[time - duration, time]`; a request ending exactly when another starts does not count as overlapping. The key is absent when no duration point carried a timestamp. When a journey declares `min_overlap` and the observed peak for an operation (0 if it never ran) is below the minimum, the run gets the limitation `concurrency not achieved for OP: observed M < N`, so it cannot pass (it is `inconclusive` unless something else makes it `fail` or `error`). `report.html` shows the seed, target, engine version, HTTP avg/p50/p95/max (or `Unavailable`), the latency sample count, HTTP error rate, journeys, throughput, and a Concurrency table of peak in-flight requests per operation. The dashboard run page lists `overlap` with the other metrics.

`planned_journeys_per_second` is the resolved schedule's admitted journeys divided by its total seconds; compare it with the observed `metrics.iterations_per_second` (iterations divided by the engine window only, from `engine_started_at` to `engine_finished_at` in `run.json`; fixture setup, observation and cleanup are excluded, as they are from `metrics.http_reqs_per_second`, while `metrics.elapsed_seconds` stays the whole run's wall-clock time). When k6 reports a nonzero `dropped_iterations` metric, `limitations` contains `k6 dropped N iterations (under-delivered load)` and the run cannot pass.

Every result also has `notes`, which list what this preview never measures: `per-arrival lateness not measured` and `workload is synthetic (no traces supplied)`. Notes are separate from `limitations` and do not affect completeness or the verdict. Those are observations, not automatic performance promises. Expected application rejections may contribute to k6's HTTP failure rate even when the business assertion passes.

## Repeat summaries

`--repeat 3 --seed 42` executes seeds 42–44 and writes `series_*.json` alongside the run directories. The summary includes every result, requested/completed counts, aggregate verdict, and whether verdict/lifecycle outcomes were consistent. Cancellation stops the series early.

`--repeat 5 --seed 42 --same-seed` runs seed 42 five times instead, to measure run-to-run noise with the timing held fixed; the summary records `same_seed: true`. `--same-seed` needs `--repeat` of at least 2.

Every summary has `dispersion` with `p95_ms` (from `metrics.http_req_duration_ms.p95`), `http_req_failed_rate` (from `metrics.http_req_failed_rate.rate`), and `http_reqs_per_second`. Each holds `min`, `max`, `mean`, and sample `stdev` over the runs that reported that metric, computed with Python's `statistics` module. `stdev` is `null` when only one run reported the metric, and the entry is `null` when none did.

## Comparison

`diff` requires equal bundle digest, seed, engine version string, and resolved schedule. Incompatible inputs produce an inconclusive comparison. The bundle digest is the sha256 of the canonical manifest JSON (sorted keys, compact separators, so reformatting whitespace does not change it) plus the sha256 of the script and each file it reaches through relative imports (`./` or `../` specifiers in `import`/`export ... from`, `import()` and `require()`), so editing journey code or a local helper makes runs incompatible. An import of the bundled `./litetraffic/runtime.js` helper hashes the helper shipped with LiteTraffic, so upgrading to a different helper also makes runs incompatible. Module imports such as `k6/http` and remote URLs are not hashed. Target environment and fixture equivalence remain the operator's responsibility.

The comparison fails whenever the candidate verdict is `fail` (including when the baseline also failed), or the optional p95 gate reports a regression. It is inconclusive when the candidate verdict is `error`/`inconclusive`, when the candidate completed fewer iterations or HTTP requests than the baseline (`candidate delivered less work`), when the p95 gate is inconclusive or unavailable, or, without a gate, when the candidate has no latency samples. Otherwise it passes. `reasons` lists why the verdict is not `pass`; for incompatible runs it contains only the compatibility mismatch, and `correctness.assertion_regressions` is empty. Individual assertion regressions (baseline `pass`, candidate not `pass`) are listed for compatible runs but are not a separate aggregate gate.

`performance.by_operation` reports `baseline_p95_ms`, `candidate_p95_ms`, and `change_percent` for each operation present with a p95 in both runs (`change_percent` is `null` when the baseline p95 is not positive). Per-operation changes are descriptive and do not affect the verdict or the p95 gate, which uses the overall p95.

A p95 gate requires a positive baseline p95 and at least 200 duration samples in each run. A smaller sample count produces an inconclusive gate. Run in comparable environments, preserve reviewed scripts, and avoid interpreting proxy or machine noise as a code regression. Without a p95 gate, the comparison reports performance differences without grading them.
