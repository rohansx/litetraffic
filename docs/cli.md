# CLI reference

[Documentation index](README.md)

All scenario arguments are **directory paths** containing `manifest.json` and the named script. `inspect`, `verify`, `up`, and `approve` take the scenario either positionally or as `--scenario DIR`; the two spellings are equivalent, and giving both or neither exits `3`. `report issue` also takes `--scenario DIR`, but only as a flag and only to include the bundle the run used, checked against the run's `scenario.lock.json`. Run `litetraffic COMMAND --help` for the installed version's syntax; `litetraffic --version` prints the installed version. The following describes `0.1.0.dev0`.

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
| `docker` | Informational, never fails: listed only when `docker` is on `PATH`, with its `--version` output (used by `verify --capture`) |

With a target, it first applies the same URL checks as `verify` (link-local and metadata targets exit `3` without a request), then sends one GET with a three-second HTTP timeout and no redirect following. Any other status fails with `reachable but not ready (HTTP N)`. This command does not install dependencies or write to the target.

## `init`

```text
litetraffic init tenant-isolation --config KIT_JSON --out DIR [--json]
```

Generates a scenario bundle in `DIR` (created if missing; `manifest.json` and `journeys.js` are overwritten) from a kit config, then loads it the way `inspect` does. Returns `ok`, `scenario`, `files`, `scenario_sha256`, and `warnings`: one line per identity that declares no `markers` (`identity 'NAME' declares no markers: status-only checks cannot detect data returned in denial bodies`), otherwise empty. An unreadable or invalid config exits `3` with `cannot read kit config: ...` or `invalid kit config: field.path: message`, and writes nothing. The same config always produces the same files. The config format and the generated checks are described in [Tenant isolation in 10 minutes](scenarios.md#tenant-isolation-in-10-minutes).

## `inspect`

```text
litetraffic inspect (SCENARIO | --scenario SCENARIO) [--seed INTEGER] [--json]
```

Validates the manifest and script path and resolves the schedule. `--seed` defaults to `0`. Returns scenario name, schema version, script path, planned journeys, request/write maxima, resolved phases, and assertion IDs. The JSON form also includes `scenario_sha256` (the bundle digest), `actors` (class, count, auth recipe, and the `auth` object when declared), all five `budgets`, `fixture` (`recipe`, and `owned_http` holding `create_path`/`delete_path` for an owned HTTP fixture, otherwise `null`, and `command` holding `setup`, `teardown`, `timeout_seconds`, the resolved `setup_timeout_seconds`/`teardown_timeout_seconds` and `cwd` for a command fixture, otherwise `null`), `secret_env` (the sorted `bearer_token_env` names referenced by the fixture and observations, plus the observations' `headers_env` variable names and each actor `auth.secret_env`; values are never read or printed), `observer`, `allowed_origins_env` (the variable name, `null` without one; one `allowed origins env:` line in the human output), `observation_path` (the first observation's path, `null` without one), `observation_expected` (the first observation's `expected` values with `${...}` expressions resolved for `--seed`; `null` without one), and `observations` (`{assertion, path, origin_env, expected}` for every observation, resolved the same way; each is one `observation ID expected: ...` line in the human output, plus an `observation ID origin env: NAME` line when `origin_env` is set; only variable names are shown, never their values). `maximum_observation_requests` is the most observer requests the observations can send: one per observation, or its most possible polling attempts (`ceil(deadline_seconds / interval_seconds) + 1`) when it has `until`. Does not execute the scenario or infer routes.

## `verify`

```text
litetraffic verify (SCENARIO | --scenario SCENARIO)
  [--target URL | --e2b-sandbox-id ID --e2b-port PORT]
  [--output-dir PATH] [--k6-path PATH]
  [--seed INTEGER] [--repeat COUNT [--same-seed]]
  [--require-approval] [--approved-digest SHA]
  [--capture docker:NAME[,NAME...]] [--json]
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
| `--capture` | Off | `docker:NAME[,NAME...]`: record these local containers' logs and resource use during the run (see below). Each name must match `^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$`; anything else exits `3`. With `--repeat`, each run captures its own window |
| `--json` | Off | Emit one JSON object to stdout |

With `--capture`, capture starts right before k6 and stops after the final observations. For each container that `docker inspect` finds, `docker logs`, followed with timestamps from the capture start, is written to `server/NAME.log` (capped at 5 MiB; the rest is still read and counted, and the container is marked `truncated`), and a one-shot `docker stats` sample of every container is taken every 2 seconds into `server/stats.jsonl`. The summary goes to `server.json` ([results](results.md#server-capture)). Capture never changes the verdict: a missing `docker`, an unknown container, or a daemon permission error is listed in `server.json` `problems` and as one `Server capture incomplete: ...` limitation, added after the verdict is decided. Server files are scrubbed of the run's secrets and do not count toward `max_artifact_bytes`. Every docker child process is stopped (SIGTERM, then SIGKILL after 3 seconds) however the run ends.

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

Also recorded: `activity_id`, `scenario`, `scenario_sha256`, `target`, `starting_seed`, `max_slices`, `started_at`, `finished_at`. There is no `verdict` field: slice verdicts stay in each slice's own `result.json`, and a failing slice does not stop the activity or change the exit status. Progress goes to stderr: `activity: PATH` first, one `slice N: RUN_ID LIFECYCLE` line per slice, and `status: STATUS` last. `--json` prints the final `activity.json` content on stdout. Ctrl+C finalizes `activity.json` and exits `0`. If a slice raises any error, the activity stops: `activity.json` gets `status: "error"` and an `error` field with the exception type and message, `status: error` goes to stderr, the error is printed as `{"ok": false, "error": ...}` (or `error: ...` without `--json`), and the exit status is `3`. Activities are not seen by `diff` or `prune`.

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

Serves a read-only dashboard over `--runs-dir` (default `.litetraffic/runs`) on `http://127.0.0.1:PORT/` (default port `8780`, clear of the example servers' ports 8765-8769; `0` picks a free port; a port outside 0-65535 is a usage error and exits 2). It always binds to `127.0.0.1`; there is no `--host` option. It prints the URL, uses only the Python standard library at runtime, and does not contact a target. The UI is a prebuilt single-page app shipped inside the package (`src/litetraffic/dashboard_ui/`), so no Node is needed to run it; a checkout without the bundle serves a plain page on `/` explaining how to build it (`pnpm -C web install && pnpm -C web build`), and the JSON API still works.

Pages (client-side routes; the server returns the app for any path outside `/api/`):

| Page | Shows |
|---|---|
| `/` Overview | Runs total, pass rate, failing scenarios, last run time, a per-scenario status grid, and the most recent runs that did not pass |
| `/runs` | Runs, series and `up` activities, newest first, with search and scenario, verdict and seed filters. Tick exactly two runs and press **Compare** (the older is the baseline). **Auto-refresh** reloads the list every 5 seconds |
| `/runs/RUN_ID` | Verdict, lifecycle, seed, scenario, digest, target, then tabs: Assertions (failing ones expand to their first failing samples, expected vs actual), Observations, Metrics (p50/p95/max, per-operation table, client-side overlap), Limitations, Artifacts (every file, plus `report.html`). An `up` activity shows its status and slice table, never a verdict |
| `/compare?baseline=ID&candidate=ID` | Whether the runs are comparable, correctness regression, per-assertion regressions, p95 change and per-operation p95 |
| `/scenarios`, `/scenarios/NAME` | Scenario list; per scenario a chart of p95 per run over time with verdict-coloured markers, and the same data as a table |
| `/about` | What the dashboard is |

JSON API:

| Route | Returns |
|---|---|
| `/api/meta` | `{runs_dir, version}` |
| `/api/runs?scenario=&verdict=&seed=` | The run index; filters combine and must match exactly |
| `/api/scenarios` | Every scenario name, sorted |
| `/api/runs/ID` | `{run_id, run, result, observation?, fixture?, activity?, artifacts: [{path, size}]}` |
| `/api/runs/ID/artifacts/PATH` | A raw file under the run directory, served with a sandboxing Content-Security-Policy (`report.html` as `text/html`) |
| `/api/diff?baseline=ID&candidate=ID` | The `diff --json` comparison; 409 `{error}` when the runs cannot be read |
| `/api/scenarios/NAME/trend` | `[{run_id, finished_at, p95, verdict}]`, oldest first; 404 when no run has that scenario |

Unknown `/api/` paths and unknown runs are 404 JSON. A request whose `Host` is not a loopback name for this port gets 421 and no data. Symlinks and paths outside the runs folder are never followed. Every response carries a strict Content-Security-Policy, `X-Content-Type-Options: nosniff` and `Referrer-Policy: no-referrer`; every route also answers `HEAD`.

The dashboard follows the system light/dark preference; the theme menu in the sidebar footer picks light, dark or system and remembers it in `localStorage`. It fits a 375px-wide screen (the sidebar becomes a sheet), and every control is reachable by keyboard.

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
| `--json` | Emit `{"ok": true, "dry_run": ..., "pruned": [PATH, ...]}`; after a deletion failure, `ok` is `false` and `failed` and `error` are added |

Only real directories directly under `--runs-dir` that contain a real (non-symlink) `run.json` are candidates; a symlinked `result.json` is ignored and deleting a run removes the link, never its target. Nested directories, symlinks, other directories, and `series_*.json` summaries are never deleted. Runs are ordered by `finished_at` in `result.json`; a run without a readable `finished_at` uses its directory modification time. Text output prints one `deleted: PATH` (or `would delete: PATH`) line per run, newest first, or `nothing to prune`. Deletion stops at the first run that cannot be deleted: `pruned` lists the runs deleted before it, `failed` names its path, text output adds `failed: PATH` and `error: MESSAGE` after the `deleted:` lines, and the exit status is `3`. A missing `--runs-dir` prunes nothing. It does not check whether a run is still in progress.

## `report issue`

```bash
litetraffic report issue RUN [--runs-dir DIR] [--scenario DIR] [--repo OWNER/NAME] [--submit] [--comment]
    [--security] [--public] [--attach-url URL] [--no-video] [--label NAME]... [--title TEXT]
    [--target-version TEXT] [--remove-local] [--json]
```

Drafts a maintainer-facing issue from a failing run into `RUN/issue/` (mode `0700`; files `0600`, `repro.sh` `0700`). By default nothing leaves the machine: the command prints the `gh` command it would run.

The run must be intact and failing. Every file listed in the run's `artifacts.json` must still match its sha256, and `artifacts.json` must exist; otherwise it exits `3` with `run artifacts changed after the run; refusing to report`. A `pass` run prints `run ID passed; nothing to report` and exits `0` without writing anything. An `inconclusive` or `error` run exits `3`: missing evidence is not a bug report. `artifacts.json` itself is never modified; `evidence.json` records the source hashes instead.

A failure is classified as `security` or `correctness`, failing closed: a failing isolation assertion (`cross_tenant_read_blocked` and the other kit checks), an assertion id that looks like an authorization check (matched after normalising the id, so `victimUnchanged`, `victim-unchanged` and `auth_bypass` all count), a tenant-isolation kit scenario (or one whose observer, journeys or auth recipes are named like one) failing anything other than `own_access`, a failing sample shaped like a kit sample (an identity and a status) or carrying markers, or `--security` all make it security-class. A correctness failure is drafted as `issue.md`; a security-class one as `security.md` plus `report.json` (the private vulnerability report payload), and no `issue.md` is written.

| Option | Meaning |
|---|---|
| `--runs-dir DIR` | Where to resolve a run ID, and where the filing ledger `issues.jsonl` lives (default `.litetraffic/runs`) |
| `--scenario DIR` | Scenario bundle whose files must match the run's `scenario.lock.json`; scrubbed copies go to `issue/scenario/` and kit markers become `[marker:IDENTITY#N]`. Never inferred |
| `--repo OWNER/NAME` | Repository to file in; required with `--submit`, otherwise only fills in the previewed command |
| `--submit` | File the issue (or, for security-class failures, a private vulnerability report) through `gh` |
| `--comment` | When an open issue with the same fingerprint exists, add this run as a comment instead of refusing (public issues only) |
| `--security` | Treat the failure as security-class; it never downgrades one |
| `--public` | Assert public filing; a security-class failure then exits `3` |
| `--attach-url URL` | Link to the evidence bundle (for example a CI artifact); must be a plain `https://` URL. In GitHub Actions the run URL is used when this is absent |
| `--no-video` | Skip the replay video |
| `--label NAME` | Issue label; repeatable |
| `--title TEXT` | Override the generated title |
| `--target-version TEXT` | App version for the Environment section |
| `--remove-local` | Needs `--submit`; delete the run directory after a successful filing when nothing the issue points to would be lost |
| `--json` | Emit one object with `run_id`, `verdict`, `kind`, `fingerprint`, `title`, `bundle`, `files`, `would_file`, `url`, `duplicate`, `video`, `attach` and `removed` |

Every file in the bundle, and the command's output, is scrubbed: declared credential values present in the environment (raw, JSON-escaped, URL-encoded in either hex case, base64 and base64url), opaque `Bearer`/`Basic` credentials, and JWT or GitHub tokens become `[redacted]`, the target origin becomes `$TARGET` and its host name `$TARGET_HOST` (whatever the port), and the run directory, runs directory and home directory become `<run>`, `<runs>` and `~`. The bundle is then re-read; if a credential, token or (for security-class failures) marker value is still present, the bundle is deleted and the command exits `3` with `redaction check failed`. The body carries a `litetraffic-fingerprint` derived from the scenario name, the failing assertion ids and a hash of the normalized target origin, so the same failure against the same origin gets the same fingerprint. Every value taken from the run (response bodies, ids, names, versions) is rendered in `issue.md` and `security.md` as inline code on a single line, so a response cannot add headings, mentions, links, HTML or a fingerprint line of its own. `repro.sh` exits `1` only when the failure reproduced; a missing `TARGET` or credential, or a step that fails before the check, exits `2`.

**Video proof.** With the optional extra (`pip install 'litetraffic[video]'`, which brings Pillow and imageio-ffmpeg; a system `ffmpeg` on `PATH` plus Pillow also works), the bundle gets `proof.mp4` (1280x720, 10 fps, about 14 s) and `proof.gif` (640x360). The video is a replay drawn from the bundle's already-scrubbed `evidence.json`, not a screen recording, and every frame says so: the run header, the traffic per operation, each failing check with its expected and actual values, the verdict, and the sha256 of the source artifacts. No raw run file is read, and every line the video would draw is checked against the same redaction as the text files before anything is encoded. Rendering is byte-identical for the same evidence and the same ffmpeg build. A file over 9.5 MB (mp4) or 5 MB (gif) is dropped. Without the extra, `evidence.json` records `video.status` `unavailable` with what is missing and how to install it; a render error records `failed` with the reason. Neither changes the exit code, and the rest of the bundle is written as usual.

**Filing.** `--submit` needs an authenticated `gh` (GitHub CLI) and never prompts. A correctness failure is filed as a public issue after two duplicate checks: the ledger `issues.jsonl` in the runs directory (repository names compared case-insensitively; an entry whose issue has since been closed does not block a new filing, which links the closed one), then an open issue in the repository whose body carries the same fingerprint. A duplicate exits `3` with `already reported: URL` unless `--comment` is given, which posts `comment.md` on that issue instead. A fingerprint counts only on a line of its own, as the tool writes it. The video is uploaded with the issue only when the installed `gh` can attach files and the token can write to the repository; otherwise the command prints `drag <bundle>/proof.mp4 into <url> to add the video`. If the issue is created but the upload fails, the inline video reference is edited back out of the issue (`gh issue edit`). A security-class failure is sent only as a private vulnerability report (`report.json`) and nothing is ever attached; if the repository does not accept private reports, the command exits `3` with where to report it instead. A successful filing is recorded in `issue/filed.json` and the ledger. `--remove-local` then deletes the run directory only when nothing the issue points to would be lost (no video, the video was attached, or an explicit `--attach-url` was given and no upload failed); a video that failed to render or upload, or only the auto-detected Actions link, keeps the run, and the command says why. Security-class runs are always kept.

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
| `report issue` | Draft written, issue filed, or a `pass` run | — | CLI usage error | Not a failing run, changed artifacts, unknown run, scenario mismatch, redaction check failed, or a refused or failed submission | — |
| `prune` | Pruned (or listed with `--dry-run`) | — | CLI usage error | Invalid retention options or deletion failed (runs deleted before the failure stay deleted) | — |

With `--repeat`, `verify` applies the same mapping to the aggregate series verdict.

Without `--json`, `verify`, `diff`, and `inspect` print a short text summary meant for people, not parsers:

- `verify`: `verdict: PASS  lifecycle: finished  journeys: 20/20`, then one `ID  STATUS  SAMPLES` line per assertion (followed by `  (REASON)` when an unknown final observation recorded one), an `unexpected HTTP failure rate: X% (FAILED/SAMPLES)` line when the run has `metrics.unexpected_http_failure_rate`, each limitation as a `- ...` line, and `report: PATH` to the run's `report.html`. With `--repeat`, a series line (`verdict: VERDICT  lifecycle: LIFECYCLE  runs: COMPLETED/REQUESTED  consistent: yes|no`), one `seed N  VERDICT  LIFECYCLE  journeys: D/P` line per run, and `summary: PATH` to the series JSON.
- `diff`: `verdict: ...`, each reason as `- ...`, `compatibility: comparable` or `compatibility: incompatible (FIELDS)`, one `regression: ID` line per assertion that passed in the baseline but not the candidate, and `p95: BASELINEms -> CANDIDATEms (+X%)  status: STATUS` (`n/a` when a value is missing), `unexpected HTTP failure rate: B% -> C% (+Npp)` when either run has that metric, then one `p95 OPERATION: BASELINEms -> CANDIDATEms (+X%)` line per operation present in both runs.
- `inspect`: name, script, `scenario sha256:` (the bundle digest), planned journeys, request/write maxima, one `phase:` line per resolved phase, one `assertion:` line per assertion ID, `fixture setup:`/`fixture teardown:` lines with the space-joined argv of a command fixture, one `fixture input:` line per `inputs` file, one `observation ASSERTION expected:` line per observation with expressions resolved, an `observation ASSERTION origin env:` line per `origin_env`, and an `allowed origins env:` line when `allowed_origins_env` is set.

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
