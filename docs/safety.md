# Safety and current limitations

[Documentation index](README.md)

LiteTraffic sends real requests, including writes, to the target you give it. Treat every run as you would a test against that environment.

## Trust boundaries

- **Scenarios are trusted code.** The journey script runs in k6 with your environment variables and network access. Review scripts as you would any test code. Do not run scenarios from untrusted sources.
- **Target only what you own.** Use a local app, an isolated preview, or a sandbox. Do not aim writes at production or shared data unless the scenario only touches run-owned fixtures.
- **No metadata or link-local targets.** `verify`, `up`, `doctor` and `approve` reject targets, and manifest validation rejects `allowed_origins` entries and an observation `origin`, whose host is a link-local address (`169.254.0.0/16`, `fe80::/10`), the AWS IPv6 metadata address `fd00:ec2::254`, or a known cloud-metadata name (`metadata.google.internal`, `metadata.goog`), and k6 runs with `--max-redirects 0`. Addresses are compared after parsing, so these forms are caught too: any IPv6 spelling (`fd00:0ec2:0:0:0:0:0:254`, upper case, a `%zone` suffix), IPv4-mapped IPv6 (`::ffff:169.254.169.254`), a trailing dot (`169.254.169.254.`, `metadata.google.internal.`), and integer IPv4 hosts in decimal, octal or hex, whole or dotted (`2852039166`, `0xA9FEA9FE`, `0251.0376.0251.0376`, `169.254.43518`). Not covered: hostnames are not resolved, so a DNS name that points at such an address passes, and other private ranges (loopback, `10.0.0.0/8` and so on) are allowed on purpose.
- **Script imports stay in the bundle.** The scenario loader scans the script and every file it imports for `import`/`require` specifiers. Remote URLs, `k6/x/` extensions, absolute paths (`/etc/lib.js`), `file:` URLs and protocol-relative `//host/lib.js` specifiers are rejected, as are relative imports that resolve outside the scenario directory; an import that cannot be read fails with a scenario error. Not covered: the scan is a regex, not a JS parser, so computed import paths are not checked, and k6 `open()` data-file reads are not scanned at all; the script can read any file your user can. Treat scripts as trusted code (above).
- **A second origin is opt-in.** An observation reads the target unless it sets an `origin` that the manifest lists in `allowed_origins`, or an `origin_env` whose run-time value matches `allowed_origins` or the `allowed_origins_env` list; the run and fixture headers, the observer's `bearer_token_env` token as `Authorization: Bearer`, and any `headers_env` values are sent to that origin.
- **Command fixtures run on the controller host.** `fixtures.command` setup and teardown execute as your user, in the scenario directory, with your environment, and without a shell. Review them like the script; `inspect` shows the exact argv.
- **Secrets stay outside bundles.** Target URLs with embedded credentials are rejected. Fixture and observer tokens, observation `headers_env` values, and actor `auth` signing keys are referenced by environment-variable name; `inspect` lists the names under `secret_env` without reading them. Those values and minted `LT_TOKEN_*` JWTs are replaced with `[redacted]` in every artifact LiteTraffic keeps and in `verify --json`, including JSON-escaped occurrences and values an observed endpoint echoes back; signing keys shorter than 8 characters are refused, and shorter `bearer_token_env` or `headers_env` values are sent but not scrubbed. Raw logs are matched on the literal value and its common JSON spellings (Python's, and Go's `\u003c`/`\u003e`/`\u0026` escapes), not every possible `\uXXXX` spelling. The whole caller environment is passed to k6, so run with only the credentials the scenario needs.

## What budgets enforce

| Budget | Enforced how |
|---|---|
| `max_seconds` | Before the run, the schedule plus fixture/observer deadlines must fit. At run time, k6 is stopped (SIGTERM, then SIGKILL) when its share expires; the run becomes `timed_out` |
| `max_requests` | Before the run, the worst case from journey maxima must fit. After the run, observed requests are compared, and an overrun makes the verdict `error` |
| `max_write_attempts` | Before the run, declared journey `max_writes` plus fixture create/cleanup must fit. After the run, `metrics.write_attempts` counts k6 `http_reqs` points tagged with method `POST`, `PUT`, `PATCH` or `DELETE`, plus the fixture create and cleanup requests; an overrun makes the verdict `error` (`write budget exceeded: N > M`) |
| `max_in_flight` | Passed to the script as `LT_MAX_IN_FLIGHT`; the script must apply it (the examples use it as `preAllocatedVUs` and `maxVUs`). After the run, the highest k6 `vus_max` point is recorded as `metrics.vus_max`; a value above the budget makes the verdict `error` (`in-flight budget exceeded: N > M`). `vus_max` is the VU capacity k6 allocated, not a count of concurrent requests |
| `max_artifact_bytes` | Checked after the run; an overrun makes the verdict `error` |

Budgets are a declared envelope, not a sandbox: a script that ignores its declared maxima can still send more traffic before the controller notices. Keep isolation on the target side.

## Cleanup

The controller cleans up `owned_http` fixtures and runs `command` fixture teardowns; a failed cleanup or teardown produces `error` so it is never hidden. The checkout and inventory examples keep state in their demo servers; restart those servers to reset them.

## Artifacts

Files LiteTraffic writes itself (`run.json`, `result.json`, reports, logs, events, the series summary) are written to a `0600` temporary file in the same directory and then renamed over the target, so they are never readable by others and a crash leaves either the previous file or the new one, never a truncated one. When a run finishes, every directory in the run directory is set to `0700` and every file to `0600` (symlinks are skipped, never followed, so a link cannot change the mode of a file outside the run directory), including the `console.log` and `metrics.jsonl` that k6 creates. While k6 is still running, those two files keep the mode k6 gave them. `verify --repeat` also sets the output directory that holds the series summary to `0700`. They contain raw k6 output and whatever the script logs, so review them before sharing.

## Known limitations of this preview

- Real-engine execution is validated on Linux. Other platforms are not release-qualified.
- No automatic scenario authoring, repository discovery, or sandbox provisioning yet. See the [roadmap](roadmap.md).

Found a security problem? See [SECURITY.md](../SECURITY.md).
