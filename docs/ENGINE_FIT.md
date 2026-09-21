# k6 engine-fit result

- Date: 2026-09-21
- Engine: k6 v2.2.0, Linux amd64
- Archive SHA-256: `b5a8003c86f35f5cd5ceef1490312c48e587696c94d998cefc6d7b3b4cb1597d`

LiteTraffic ran the checkout scenario through the official k6 binary using native [JSON metric output](https://grafana.com/docs/k6/latest/results-output/real-time/json/) and [`--console-output`](https://grafana.com/docs/k6/latest/using-k6/k6-options/reference/#console-output). The controller parsed only versioned `LT_EVENT` records, assigned collector-side sequence numbers, and independently reconciled delivered iterations with the manifest plan.

## Measured conformance

| Target | Planned / delivered journeys | HTTP requests | Intended assertion | Verdict |
|---|---:|---:|---|---|
| Correct idempotent payment server | 12 / 12 | 36 | `one_effect_per_payment` passed 12/12 | `pass` |
| Broken duplicate-on-retry server | 12 / 12 | 36 | `one_effect_per_payment` failed | `fail` |

The negative target still returned successful HTTP responses and passed persistence and total checks. The failure therefore came from observed business state, not an HTTP error shortcut.

## Scheduling finding

Separate back-to-back `constant-arrival-rate` scenarios produced 14 iterations for a 12-journey plan because admissions occurred at phase boundaries. A single `ramping-arrival-rate` scenario produced 11 when the total integral ended exactly on the final boundary. The reviewed script now represents each constant segment as a ramping stage and adds one millisecond per stage, yielding the declared 12 admissions without materially changing the requested rates. LiteTraffic treats any future delivered/planned mismatch as `inconclusive`.

## Proven boundary

This validates stock-k6 subprocess execution, structured console framing, JSONL metrics, complete assertion capture, schedule reconciliation, and a positive/negative stateful idempotency check. It does not yet validate long-duration event loss, cancellation finalization, memory ceilings, E2B execution, fixture provisioning, or an observer plugin interface.

The release archive came from the official [grafana/k6 v2.2.0 release](https://github.com/grafana/k6/releases/tag/v2.2.0). The binary remains an external prerequisite and is not redistributed by LiteTraffic.
