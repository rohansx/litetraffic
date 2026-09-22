# k6 engine-fit result

- Date: 2026-09-21
- Engine: k6 v2.2.0, Linux amd64
- Archive SHA-256: `b5a8003c86f35f5cd5ceef1490312c48e587696c94d998cefc6d7b3b4cb1597d`

LiteTraffic ran the checkout, inventory, and reporting scenarios through the official k6 binary using native [JSON metric output](https://grafana.com/docs/k6/latest/results-output/real-time/json/) and [`--console-output`](https://grafana.com/docs/k6/latest/using-k6/k6-options/reference/#console-output). The controller parsed only versioned `LT_EVENT` records, assigned collector-side sequence numbers, and independently reconciled delivered iterations with the manifest plan.

## Measured conformance

| Target | Planned / delivered journeys | HTTP requests | Intended assertion | Verdict |
|---|---:|---:|---|---|
| Correct idempotent payment server | 12 / 12 | 36 | `one_effect_per_payment` passed 12/12 | `pass` |
| Broken duplicate-on-retry server | 12 / 12 | 36 | `one_effect_per_payment` failed | `fail` |
| Correct atomic inventory server | 8 / 8 | 40 | Four inventory assertions passed 8/8 | `pass` |
| Broken overselling inventory server | 8 / 8 | 40 | Negative inventory and excess acceptance detected | `fail` |
| Correct sales report server | 6 / 6 | 6 | Totals, rows, regions, and response assertions passed 6/6 | `pass` |
| Fast but partial sales report server | 6 / 6 | 6 | Incorrect total, missing row, and missing region detected despite HTTP 200 | `fail` |
| Sustained-burst sales report server | 19 / 19 | 19 | Ramp, plateau, and recovery schedule preserved complete evidence | `pass` |
| Three-seed reporting series | 18 / 18 total | 18 | Seeds 42–44 produced three consistent complete passes | `pass`, consistent |
| Controlled latency comparison | 200 / 200 per run | 200 per run | p95 increased 5.675 ms → 20.693 ms; 20% gate detected +264.634% | `fail`, regression |
| Correct final ledger observation | 6 / 6 | 6 + 1 observer | Journey assertions and final fixture assertion passed | `pass` |
| Wrong final ledger observation | 6 / 6 | 6 + 1 observer | Journey assertions passed; final fixture assertion detected wrong persisted total | `fail` |
| Run-owned reporting fixture | 6 / 6 | 6 + 1 observer + 2 lifecycle | Fixture created, scoped through journey/observer, then deleted | `pass` |
| Run-owned fixture with wrong ledger | 6 / 6 | 6 + 1 observer + 2 lifecycle | Final assertion failed; fixture still deleted | `fail` |
| E2B external target | 4 / 4 | 4 | Seeded spiky profile reached a short-lived sandbox through its exposed HTTPS port | `pass` |
| Correct cached search | 12 / 12 | 84 + 1 observer + 2 lifecycle | Hot/cold reads and all post-update reads passed | `pass` |
| Permanently stale cache | 12 / 12 | 84 + 1 observer + 2 lifecycle | Journey and final observer detected the stale hot key | `fail` |
| Correct tenant isolation | 10 / 10 | 30 + 1 observer + 2 lifecycle | Both identities read their own overlapping local ID; cross-tenant read blocked | `pass` |
| Cross-tenant leak | 10 / 10 | 30 + 1 observer + 2 lifecycle | Tenant A read tenant B's local record ID | `fail` |
| Deny-all tenant API | 10 / 10 | 30 + 1 observer + 2 lifecycle | Isolation check passed but both required positive reads failed | `fail` |
| Real k6 timeout probe | 1 planned / incomplete | 0 | One pre-timeout event preserved | `inconclusive`, `timed_out` |

The negative targets still returned valid HTTP responses. Their failures came from observed business state rather than an HTTP error shortcut.

## Scheduling finding

Separate back-to-back `constant-arrival-rate` scenarios produced 14 iterations for a 12-journey plan because admissions occurred at phase boundaries. A single `ramping-arrival-rate` scenario produced 11 when the total integral ended exactly on the final boundary. The reviewed script now represents each constant segment as a ramping stage and adds one millisecond per stage, yielding the declared 12 admissions without materially changing the requested rates. LiteTraffic treats any future delivered/planned mismatch as `inconclusive`.

## Proven boundary

This validates stock-k6 subprocess execution, process-group timeout termination, direct-SIGINT cancellation with shell status 130, partial-evidence finalization, structured console framing, JSONL metrics, complete assertion capture, schedule reconciliation, seeded spiky and random-burst profiles, ramp/plateau/recovery sustained bursts, repeated-seed summaries, compatible-run checks, latency regression gates, stateful idempotency, finite-resource contention, report completeness, read-only final observation, run-owned HTTP fixture creation and cleanup, E2B external-target execution, and self-contained HTML reports. Engine-crash, timeout, and cancellation cleanup behavior is covered by executable tests. It does not yet validate long-duration event loss, memory ceilings, E2B sandbox provisioning, host-crash cleanup, or an observer plugin interface.

The release archive came from the official [grafana/k6 v2.2.0 release](https://github.com/grafana/k6/releases/tag/v2.2.0). The binary remains an external prerequisite and is not redistributed by LiteTraffic.
