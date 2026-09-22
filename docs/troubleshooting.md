# Troubleshooting

[Documentation index](README.md)

Start with `litetraffic doctor --target <url> --json`, then open the run's `result.json` and read `limitations`.

| Symptom | Likely cause | What to do |
|---|---|---|
| `k6 executable not found` | k6 is not on `PATH` | Install it ([installation](installation.md)) or pass `--k6-path` |
| `unsupported k6 version; expected v2.2.0` | A different k6 is installed | Install v2.2.0 alongside it and pass `--k6-path` |
| `target must be an absolute http or https URL` | Missing scheme or host | Use e.g. `http://127.0.0.1:8765` |
| `target URL must not contain credentials` | `user:pass@` in the URL | Move credentials to the scenario's token environment variables |
| `request budget ... is below the journey and lifecycle maximum` | Budgets too small for the schedule | Raise `max_requests`, or lower rates/durations/`max_requests` per journey |
| `scheduled duration ... exceeds max_seconds budget` | Schedule plus fixture/observer deadlines is too long | Raise `max_seconds` or shorten the schedule |
| Limitation `k6 thresholds breached`, verdict `fail` | k6 exited `99`: a threshold in the scenario script was crossed | Check the threshold values in the script against `metrics` in `result.json` |
| Verdict `error`, lifecycle `crashed` | k6 failed to start or exited non-zero (other than `99`) | Read `engine.stderr.log`; often a script syntax error or a connection refused |
| `target unreachable: all N requests failed before an HTTP response` | Every request failed before any HTTP response (connection refused, DNS, TLS, timeout) | Check that the app is running and `--target` host/port is correct; run `litetraffic doctor --target <url>` |
| Verdict `inconclusive`, `missing assertion evidence` | Script never emitted `LT_EVENT` for that assertion, or used the wrong `run_id` | Check `console.log`; make sure each declared assertion is logged |
| `partial assertion evidence (n/m)` | Some journeys did not run or did not log | Check `dropped_iterations`; raise `max_in_flight` or reduce the rate |
| `delivered journeys ... do not match planned journeys` | The generator could not keep up, or the run ended early | Same as above; also check that the target is not overloaded |
| Lifecycle `timed_out` | The app is too slow for the `max_seconds` envelope | Raise `max_seconds` or investigate the slowdown |
| `fixture create HTTP 4xx/5xx` | Fixture endpoint missing, rejected the body, or needs auth | Check `create_path`, `create_body`, `bearer_token_env` |
| `fixture cleanup ...` makes the verdict `error` | Delete failed | Clean up manually; fix `delete_path` |
| `observer HTTP ...` / `unknown` | Observation endpoint not reachable or not 200 JSON | Check `observation.path` and token |
| Example `verify` fails on the first try | A demo server is still running from earlier with a fault flag, or holds old state | Stop it and restart without the fault flag |
| `diff` is `inconclusive` | Different seed, manifest, engine, or schedule; the candidate verdict is `error`/`inconclusive`; the candidate delivered less work (fewer iterations or HTTP requests than the baseline); or, without a p95 gate, the candidate has no latency samples | Read the `reasons` field; compare runs of the same scenario and seed that ran to completion |
| p95 gate inconclusive | Fewer than 200 duration samples in a run | Use a longer or higher-rate schedule |

Still stuck? Open an issue with the command, `result.json`, and `engine.stderr.log` after removing any secrets.
