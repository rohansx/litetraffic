# Results, reports, and comparisons

[Documentation index](README.md)

## Where results go

The default output is `.litetraffic/runs/`. Every invocation gets a unique `run_<timestamp>_<suffix>` directory. `verify --json` prints its `run_id`; combine that with your output directory to locate the files.

| File | Contents |
|---|---|
| `run.json` | Target, seed, manifest digest, engine, resolved schedule, timestamps, lifecycle |
| `scenario.lock.json` | Copy of the original manifest bytes |
| `result.json` | Verdict, completeness, assertion summaries, metrics, limitations |
| `report.html` | Standalone readable report; open in a browser |
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
| `error` | Engine/lifecycle or budget/fixture failure prevented a clean evaluation |

Lifecycle is `finished`, `timed_out`, `cancelled`, or `crashed`, independently of the verdict. For example, a timed-out run can retain a definite failing assertion; it can never pass. An observer that cannot respond yields unknown evidence. A failed configured fixture cleanup makes the verdict `error`.

For ordinary journey assertions, each declared assertion must have exactly one sample per planned journey. A final-observer assertion instead receives one aggregate observation. Missing samples and delivered/planned journey mismatches prevent a pass. Inspect `limitations`, `completeness`, and each assertion's sample count when diagnosing a result.

Engine event or metric lines that cannot be parsed (invalid JSON, a non-object value, or a metric record whose `data` field is present but is not an object) are skipped and reported as an `ignored N malformed event record(s)` or `ignored N malformed metric record(s)` limitation; the run is still finalized and `result.json` is still written.

The report includes request duration samples, average/p50/p95/max, HTTP error rate, iterations, and throughput when k6 supplies them. Those are observations, not automatic performance promises. Expected application rejections may contribute to k6's HTTP failure rate even when the business assertion passes.

## Repeat summaries

`--repeat 3 --seed 42` executes seeds 42–44 and writes `series_*.json` alongside the run directories. The summary includes every result, requested/completed counts, aggregate verdict, and whether verdict/lifecycle outcomes were consistent. Cancellation stops the series early.

## Comparison

`diff` requires equal manifest digest, seed, engine version string, and resolved schedule. Incompatible inputs produce an inconclusive comparison. Target environment equivalence, fixture equivalence, and script equality are the operator's responsibility: the current digest covers **manifest bytes only**, not journey code or imports.

The current aggregate comparison fails when a passing baseline becomes a failing candidate, or the optional p95 gate reports a regression. Candidate `error`/`inconclusive` outcomes yield an inconclusive comparison. Individual assertion regressions are also listed, but are not a separate aggregate failure gate. Two failed runs may therefore produce a comparison `pass`: inspect both original verdicts before accepting an application change.

A p95 gate requires a positive baseline p95 and at least 200 duration samples in each run. A smaller sample count produces an inconclusive gate. Run in comparable environments, preserve reviewed scripts, and avoid interpreting proxy or machine noise as a code regression. Without a p95 gate, the comparison reports performance differences without grading them.
