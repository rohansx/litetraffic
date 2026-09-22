# Results, reports, and comparisons

[Documentation index](README.md)

## Where results go

The default output is `.litetraffic/runs/`. Every invocation gets a unique `run_<timestamp>_<suffix>` directory. `verify --json` prints its `run_id`; combine that with your output directory to locate the files.

| File | Contents |
|---|---|
| `run.json` | Target, seed, manifest digest, engine, resolved schedule, run and engine-window timestamps (`engine_started_at`, `engine_finished_at` when k6 was launched), lifecycle |
| `scenario.lock.json` | Copy of the original manifest bytes |
| `result.json` | Mode (`verify`), seed, verdict, completeness, assertion summaries, metrics, planned vs observed rate, limitations, notes |
| `report.html` | Standalone readable report (seed, target, engine, latency, assertions, limitations, notes); open in a browser |
| `events/000001.jsonl` | Validated, sequenced assertion events |
| `metrics.jsonl` | Raw k6 JSON metrics, if emitted |
| `console.log` | Raw k6 console output, if emitted |
| `engine.stdout.log`, `engine.stderr.log` | Captured engine diagnostics |
| `observation.json` | Optional final observer expectations and selected actual fields |
| `fixture.json` | Optional fixture create/cleanup outcomes |

The controller restricts permissions on directories and files it creates. Raw engine output may still contain anything the script logs. Treat the entire run directory as potentially sensitive and review it before sharing. Scripts and imported dependencies are **not** copied into the run directory by this preview.

## Verdict and lifecycle

| Verdict | Meaning |
|---|---|
| `pass` | Declared assertions passed with complete expected evidence and no reported run limitation |
| `fail` | At least one declared business assertion definitely failed |
| `inconclusive` | Evidence/progress was incomplete without a definitive failure, such as a timeout |
| `error` | Engine/lifecycle, budget/fixture failure, or an unreachable target prevented a clean evaluation |

Lifecycle is `finished`, `timed_out`, `cancelled`, or `crashed`, independently of the verdict. For example, a timed-out run can retain a definite failing assertion; it can never pass. A timed-out run reports `engine stopped after its E-second share of the S-second budget`, where E is `max_seconds` minus the fixture (10 s) and observer (5 s) reserves the scenario declares. k6 exit status `99` (a script threshold was crossed) is not a crash: the run stays `finished`, reports the limitation `k6 thresholds breached`, and its verdict is `fail` unless an `error` condition applies. Any other non-zero k6 exit is `crashed`. An observer that cannot respond yields unknown evidence. A failed configured fixture cleanup makes the verdict `error`.

When k6 made requests but every `http_req_failed` point is a transport-level failure (k6 tag `status` of `"0"` or an `error_code` tag, meaning no HTTP response arrived), the verdict is `error`, failing assertions become `unknown`, and `limitations` contains `target unreachable: all N requests failed before an HTTP response`. `metrics.http_req_failed_rate.transport` counts those failures when any occur. A run that receives any HTTP response, including HTTP 500, is judged normally.

For ordinary journey assertions, each declared assertion must have exactly one sample per planned journey. A final-observer assertion instead receives one aggregate observation. Missing samples and delivered/planned journey mismatches prevent a pass. Inspect `limitations`, `completeness`, and each assertion's sample count when diagnosing a result.

Each entry in `result.json` `assertions` has `id`, `status`, and `samples`. A failing journey assertion also has `failures`: up to the first three failing samples, each `{sequence, logical_key, expected, actual, detail}` taken from the event (`null` when the event omitted a field; `sequence` matches `events/000001.jsonl`). A final-observer assertion that was evaluated also carries the observer's `expected` and `actual` values from `observation.json`. When the target is unreachable, failing rows become `unknown` and drop these details. `report.html` shows a "Failing samples" table (sample, logical key, expected, actual, detail) for every failing assertion; all values are HTML-escaped.

Engine event or metric lines that cannot be parsed (invalid JSON, a non-object value, or a metric record whose `data` field is present but is not an object) are skipped and reported as an `ignored N malformed event record(s)` or `ignored N malformed metric record(s)` limitation; the run is still finalized and `result.json` is still written.

`result.json` `metrics.http_req_duration_ms` holds `samples`, `average`, `p50`, `p95`, and `max` when k6 supplies durations. `report.html` shows the seed, target, engine version, HTTP avg/p50/p95/max (or `Unavailable`), the latency sample count, HTTP error rate, journeys, and throughput.

`planned_journeys_per_second` is the resolved schedule's admitted journeys divided by its total seconds; compare it with the observed `metrics.iterations_per_second` (iterations divided by the engine window only, from `engine_started_at` to `engine_finished_at` in `run.json`; fixture setup, observation and cleanup are excluded, as they are from `metrics.http_reqs_per_second`, while `metrics.elapsed_seconds` stays the whole run's wall-clock time). When k6 reports a nonzero `dropped_iterations` metric, `limitations` contains `k6 dropped N iterations (under-delivered load)` and the run cannot pass.

Every result also has `notes`, which list what this preview never measures: `per-arrival lateness not measured` and `workload is synthetic (no traces supplied)`. Notes are separate from `limitations` and do not affect completeness or the verdict. Those are observations, not automatic performance promises. Expected application rejections may contribute to k6's HTTP failure rate even when the business assertion passes.

## Repeat summaries

`--repeat 3 --seed 42` executes seeds 42–44 and writes `series_*.json` alongside the run directories. The summary includes every result, requested/completed counts, aggregate verdict, and whether verdict/lifecycle outcomes were consistent. Cancellation stops the series early.

## Comparison

`diff` requires equal manifest digest, seed, engine version string, and resolved schedule. Incompatible inputs produce an inconclusive comparison. Target environment equivalence, fixture equivalence, and script equality are the operator's responsibility: the current digest covers **manifest bytes only**, not journey code or imports.

The comparison fails whenever the candidate verdict is `fail` (including when the baseline also failed), or the optional p95 gate reports a regression. It is inconclusive when the candidate verdict is `error`/`inconclusive`, when the candidate completed fewer iterations or HTTP requests than the baseline (`candidate delivered less work`), when the p95 gate is inconclusive or unavailable, or, without a gate, when the candidate has no latency samples. Otherwise it passes. `reasons` lists why the verdict is not `pass`; for incompatible runs it contains only the compatibility mismatch, and `correctness.assertion_regressions` is empty. Individual assertion regressions (baseline `pass`, candidate not `pass`) are listed for compatible runs but are not a separate aggregate gate.

A p95 gate requires a positive baseline p95 and at least 200 duration samples in each run. A smaller sample count produces an inconclusive gate. Run in comparable environments, preserve reviewed scripts, and avoid interpreting proxy or machine noise as a code regression. Without a p95 gate, the comparison reports performance differences without grading them.
