# Safety and current limitations

[Documentation index](README.md)

LiteTraffic sends real requests, including writes, to the target you give it. Treat every run as you would a test against that environment.

## Trust boundaries

- **Scenarios are trusted code.** The journey script runs in k6 with your environment variables and network access. Review scripts as you would any test code. Do not run scenarios from untrusted sources.
- **Target only what you own.** Use a local app, an isolated preview, or a sandbox. Do not aim writes at production or shared data unless the scenario only touches run-owned fixtures.
- **No metadata or link-local targets.** `verify` and `doctor` reject targets whose host is a literal link-local address (including `169.254.169.254` and `fe80::/10`) or a known cloud-metadata name, and k6 runs with `--max-redirects 0`. Hostnames are not resolved, so a DNS name that points at such an address is not caught.
- **Secrets stay outside bundles.** Target URLs with embedded credentials are rejected. Fixture and observer tokens are referenced by environment-variable name. The whole caller environment is passed to k6, so run with only the credentials the scenario needs.

## What budgets enforce

| Budget | Enforced how |
|---|---|
| `max_seconds` | Before the run, the schedule plus fixture/observer deadlines must fit. At run time, k6 is stopped (SIGTERM, then SIGKILL) when its share expires; the run becomes `timed_out` |
| `max_requests` | Before the run, the worst case from journey maxima must fit. After the run, observed requests are compared, and an overrun makes the verdict `error` |
| `max_write_attempts` | Static check against declared journey `max_writes` only. Writes are not counted at run time |
| `max_in_flight` | Passed to the script as `LT_MAX_IN_FLIGHT`; the script must apply it |
| `max_artifact_bytes` | Checked after the run; an overrun makes the verdict `error` |

Budgets are a declared envelope, not a sandbox: a script that ignores its declared maxima can still send more traffic before the controller notices. Keep isolation on the target side.

## Cleanup

Only `owned_http` fixtures are cleaned up by the controller, and a failed cleanup produces `error` so it is never hidden. The checkout and inventory examples keep state in their demo servers; restart those servers to reset them.

## Artifacts

When a run finishes, every directory in the run directory is set to `0700` and every file to `0600`, including the `console.log` and `metrics.jsonl` that k6 creates. While k6 is still running, those two files keep the mode k6 gave them. `verify --repeat` also sets the output directory that holds the series summary to `0700`. They contain raw k6 output and whatever the script logs, so review them before sharing.

## Known limitations of this preview

- Real-engine execution is validated on Linux. Other platforms are not release-qualified.
- No automatic scenario authoring, repository discovery, or sandbox provisioning yet. See the [roadmap](roadmap.md).

Found a security problem? See [SECURITY.md](../SECURITY.md).
