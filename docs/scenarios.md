# Writing scenarios

[Documentation index](README.md) · [CLI reference](cli.md)

A scenario is a directory with two files:

```text
my-scenario/
├── manifest.json   # declared shape, schedule, assertions, budgets
└── journeys.js     # k6 script that performs the journeys and emits evidence
```

The fastest start is to copy the bundled example closest to your case and change its API calls and expectations. Validate every change with `litetraffic inspect`.

## Tenant isolation in 10 minutes

`litetraffic init tenant-isolation` writes a complete bundle (`manifest.json` plus a `journeys.js` built on the bundled runtime helper) from a small JSON config, so you describe *who owns what* instead of writing k6 code. [`examples/tenant_api/kit.json`](../examples/tenant_api/kit.json) is a working config:

```json
{
  "name": "tenant-isolation-kit",
  "identities": [
    {"name": "tenant-a", "headers": {"X-Actor-Tenant": "a"}, "resources": ["a"]},
    {"name": "tenant-b", "headers": {"X-Actor-Tenant": "b"}, "resources": ["b"]}
  ],
  "endpoints": [
    {"method": "GET", "path": "/tenants/{id}/records/1", "kind": "read"},
    {"method": "PUT", "path": "/tenants/{id}/records/1", "kind": "write", "body": {"value": "mallory"}}
  ],
  "fixtures": {"recipe": "owned-overlapping-tenant-records", "owned_http": {"...": "as in the manifest"}},
  "observations": [{"path": "/tenant/state", "assertion": "tenant_fixture_intact", "expected": {"/tenant_count": 2}}]
}
```

```bash
litetraffic init tenant-isolation --config examples/tenant_api/kit.json --out my-isolation
litetraffic inspect my-isolation
python examples/tenant_api/server.py --port 8769 &
litetraffic verify my-isolation --target http://127.0.0.1:8769   # pass; --wrong-leak, --deny-all or --wrong-silent-write fail
```

Config fields:

| Field | Required | Meaning |
|---|---|---|
| `name` | yes | Scenario name |
| `identities` | yes | Exactly two, each `{"name", "resources", "markers"?, "auth"?, "token_env"?, "headers"?}`. `resources` are what this identity owns: each is an id (the `{id}` placeholder) or an object of named placeholders such as `{"id": "org-a", "position": "pos-a"}`; every resource must have the same keys. Give `auth` (an [actor auth](#actor-auth) recipe; `kind` defaults to `jwt_hs256`) or `token_env` (a variable holding a ready bearer token), not both; `headers` are plain, non-secret headers. At least one of the three is required. `markers` are non-empty strings that only this identity's private data contains, such as a seeded secret value or row id (resource ids are not used automatically); see the body checks below. Markers are written into `journeys.js` as plain text, so use seeded test values, not real secrets |
| `endpoints` | yes | `[{"method", "path", "kind": "read"\|"write", "name"?, "body"?, "expected_statuses"?, "check_own"?, "read_back"?, "journey_in_path"?}]`. Placeholders are filled from the resource under test: URL-encoded in `path`, verbatim in every `body` string (a `{name}` that is not a resource key stays literal). `{journey}` is filled with the journey key (`lt.journeyKey()`, `<run_id>-<scenario>-<iteration>`) in every body string, and in the path only when the endpoint sets `journey_in_path: true`, so rows a write creates can be tied back to the journey (and run) that made them; `journey` cannot be a resource key. `path` may otherwise only use resource keys, and each endpoint must use at least one placeholder in its path or body, so `POST /positions` with `{"organization_id": "{id}"}` works. Reads use `GET`/`HEAD`, writes `POST`/`PUT`/`PATCH`/`DELETE` with an optional JSON `body`. `expected_statuses` are the owner's success statuses (default `[200]` for reads, `[200, 201, 204]` for writes). At least one read is required. A write reads its resource back through its `read_back`: the index of a read endpoint in `endpoints` or that read's `name` (default: the first read); a reference to anything but a read endpoint is rejected |
| `fixtures`, `schedule`, `observations`, `allowed_origins`, `allowed_origins_env` | no | Copied into the manifest as written (list an observation's `origin`, or the origins its `origin_env` may resolve to, in `allowed_origins`). Defaults: `{"recipe": "static-resources"}`, the 6-second warmup/measure/recovery schedule of the tenant example, and none; an explicit `null` is rejected. An observation's `assertion` may not reuse a generated assertion id |
| `max_in_flight` | no | Default `6` |
| `unauthenticated_probe` | no | `true` adds the `unauthenticated_rejected` assertion (below). Default `false` |

Each journey gives each identity its own fresh cookie jar, so a session cookie the server sets for one identity is never sent with the other identity's requests (or carried into the next journey). Then, for each identity as owner and each owned id:

- `own_access`: the owner reads the resource through every read endpoint (and writes with `check_own: true` writes) and gets one of the endpoint's `expected_statuses`.
- `cross_tenant_read_blocked`: the other identity reads it and gets `401`, `403` or `404`, and the body (whatever the status) contains none of the owner's markers.
- `cross_tenant_write_blocked` (only with write endpoints): the other identity writes it and gets `401`, `403` or `404`, and the body contains none of the owner's markers.
- `victim_unchanged` (only with write endpoints): the owner reads the resource back (through the write's `read_back`) before and after each cross-tenant write; the statuses and bodies must match (`deepEqual`). This catches a server that answers `403` but applies the write anyway.
- `unauthenticated_rejected` (only with `unauthenticated_probe: true`): each read endpoint is also requested with no credentials at all (no bearer token, none of the identity's `headers` and no cookies: each probe uses a fresh, empty cookie jar), tagged `operation: unauthenticated`, and must get `401`, `403` or `404` with a body that contains none of the owner's markers.
- `no_foreign_data_in_own_responses` (only when some identity declares `markers`): no response the owner gets (its reads, `check_own` writes and write read-backs) contains the other identity's markers, so an owner list that also returns the other tenant's rows fails.

Markers are matched as plain substrings of the raw response body. Without them the checks are status-only and cannot see private data returned in a `403` body or mixed into an owner's `200`; `init` lists every identity without markers in its `warnings`.

Requests are tagged `operation: own` or `cross_tenant`, and the journey declares those tags' `expected_statuses`, so rejected probes do not count as unexpected HTTP failures. The generator computes `journeys[].max_requests`/`max_writes` and all budgets from the config; generation is deterministic, so the same config gives the same digest. Evidence carries identity, method, path template, id and statuses, never response bodies; a marker hit is recorded as `markers: {identity, indexes}` (whose markers, and their positions in that identity's `markers` list), never the marker values.

Limits: owner writes are off by default because a concurrent journey's owner write would race another journey's read-back compare; set `check_own` only for idempotent writes. A `token_env` value is sent to k6 but is not a declared credential, so it is not scrubbed from kept artifacts; the generated script never writes it to evidence. Prefer `auth` when the app accepts controller-minted JWTs. Resource ids are static; regenerate after editing the config rather than editing the generated files.

## Manifest

Unknown fields are rejected. `schema_version` must be `1`.

| Field | Required | Meaning |
|---|---|---|
| `name` | yes | Scenario name recorded in run metadata |
| `script` | yes | Script path relative to the bundle; must stay inside the directory. Relative imports it reaches (`./`, `../`) must also resolve to existing files inside the directory, or loading fails. Remote modules (any `scheme://` specifier, such as `https://jslib.k6.io/...`) and `k6/x/...` extensions are rejected in the script and every local import; k6 built-ins such as `k6`, `k6/http` and `k6/execution` are allowed |
| `actors` | yes | `[{"class", "count", "auth_recipe", "auth"}]` — `class`, `count` and `auth_recipe` are descriptive labels for reviewers, except that an `auth` recipe with `per_identity` mints `count` tokens; the optional `auth` recipe makes the controller mint a token for the actor class ([below](#actor-auth)) |
| `fixtures.recipe`, `fixtures.parameters` | yes / no | Descriptive fixture label and parameters |
| `fixtures.owned_http` | no | Run-owned HTTP fixture the controller creates and deletes ([below](#run-owned-fixtures)) |
| `fixtures.command` | no | Setup and teardown commands run on the controller host ([below](#command-fixtures)); cannot be combined with `owned_http` |
| `fixtures.pool` | no | Bundle-relative JSON array file with one item per journey ([below](#fixture-pool)) |
| `journeys` | yes | `[{"name", "max_requests", "max_writes", "min_overlap"?, "expected_statuses"?}]` per-journey maxima used for budget checks; optional `min_overlap` maps a k6 `operation` tag to the minimum number of those requests that must be observed in flight at once (a client-side overlap check: it shows requests were in flight together, not that the server executed them concurrently), positive integers; when several journeys name the same operation the largest minimum applies; optional `expected_statuses` maps a k6 `operation` tag to a non-empty list of HTTP status codes (100-599) that are intended outcomes for that operation, merged across journeys and used only for `metrics.unexpected_http_failure_rate` (see [results](results.md)) |
| `schedule` | yes | `unit: "journeys_per_second"` plus exactly one of `phases` or `profile` |
| `assertions` | yes | Assertion IDs that must receive evidence for a pass |
| `observer` | yes | Descriptive label for how the effect is observed |
| `observation` | no | Final read-only HTTP check ([below](#final-observation)) |
| `observations` | no | List of final checks, each shaped like `observation` ([below](#multiple-observations)); cannot be combined with `observation` |
| `allowed_origins` | no | Absolute http(s) origins an observation may read besides the target ([below](#observing-another-origin)) |
| `allowed_origins_env` | no | Uppercase environment variable holding comma-separated extra origins an `origin_env` may resolve to ([below](#origin-from-the-environment)) |
| `budgets` | yes | `max_seconds`, `max_requests`, `max_write_attempts`, `max_in_flight`, `max_artifact_bytes` |

`inspect` rejects a manifest when the schedule could exceed its budgets: planned journeys × the largest `max_requests` (plus fixture and observer calls) must fit `max_requests`, the same for writes, and the scheduled duration plus 2 s for k6 start-up and in-flight journeys, the fixture deadline (10 s for `owned_http`, (setup timeout + 4 s stop grace) + (teardown timeout + 4 s stop grace) for `command`) and the observation deadline (5 s per observation, plus `until.deadline_seconds` rounded up for a [polled](#eventual-observations) one) must fit `max_seconds`; a polled observation counts its most possible attempts against `max_requests`. The error names each part and the total, e.g. `scheduled duration (12 s) plus engine start/drain (2 s), fixture (48 s) and observation (10 s) deadlines need 72 s, max_seconds is 70`. Each fixture create, fixture cleanup and observation request has a hard 5 s wall-clock deadline covering connect, headers and the full body (a slow or trickling server cannot stretch it), and a response body over 1 MiB is rejected; either failure makes the fixture `error` or the observation `unknown` (`... unavailable: deadline exceeded` / `... unavailable: response body over 1 MiB`).

## Schedules

**Explicit phases** — each phase admits `ceil(seconds × rate)` journeys:

```json
"schedule": {
  "unit": "journeys_per_second",
  "phases": [
    {"name": "warmup", "seconds": 1, "rate": 1},
    {"name": "measure", "seconds": 5, "rate": 2}
  ]
}
```

**Profiles** compile into phases from the seed, so the same seed always yields the same schedule:

| `kind` | Fields | Shape |
|---|---|---|
| `spiky` | `duration_seconds`, `baseline_rate`, `spike_rate`, `spike_seconds`, `spikes` | Steady baseline with short spikes, one per equal window |
| `random_bursts` | `duration_seconds`, `quiet_rate`, `burst_rate`, `burst_seconds`, `bursts` | Mostly quiet (rate may be 0) with seeded bursts |
| `sustained_burst` | `baseline_rate`, `plateau_rate`, `ramp_seconds`, `plateau_seconds`, `recovery_seconds` | Linear ramp, plateau, return to baseline |

Run `litetraffic inspect my-scenario --seed 42 --json` to see the resolved phases.

## Journey script

The script is ordinary k6 JavaScript. The controller passes these environment variables:

| Variable | Use |
|---|---|
| `LT_TARGET` | Base URL; prefix every request with it |
| `LT_RUN_ID` | Unique run ID; include it in evidence and, ideally, a request header |
| `LT_SCHEDULE_JSON` | Resolved phases; convert to a `ramping-arrival-rate` executor |
| `LT_MAX_IN_FLIGHT` | Use as `preAllocatedVUs`/`maxVUs` |
| `LT_SEED` | Seed, if the script needs deterministic choices |
| `LT_FIXTURE_ID` | Present only when an `owned_http` fixture was created |
| `LT_FIXTURE_JSON` | Present only when a `command` fixture's setup printed a JSON object as its last stdout line |
| `LT_FIXTURE_POOL_JSON` | Present only when a [fixture pool](#fixture-pool) is set; read it with `poolItem()` |
| `LT_TOKEN_<CLASS>` | Present only for actors with an [`auth` recipe](#actor-auth); a signed JWT for that actor class (with `per_identity`, the first of its tokens) |
| `LT_TOKENS_<CLASS>` | Present only for actors whose `auth` sets `per_identity`; a JSON array of `count` signed JWTs, one per identity. Read it with `tokenFor()` |

### Bundled runtime helper

Import `./litetraffic/runtime.js` instead of copying the schedule and evidence code into every script. The file is not in your scenario directory: `verify` runs k6 against a temporary copy of the directory with LiteTraffic's helper placed at that path, and deletes the copy afterwards. The helper's sha256 is recorded under `litetraffic/runtime.js` in the bundle digest and in `scenario.lock.json`. A scenario that contains its own `litetraffic/runtime.js` is rejected, because that path is reserved.

```js
import http from "k6/http";
import * as lt from "./litetraffic/runtime.js";

export const options = lt.options();

export default function () {
  const key = lt.journeyKey();
  const res = http.get(`${__ENV.LT_TARGET}/orders/${key}`);
  lt.evidence("order_visible", res.status === 200, { expected: 200, actual: res.status });
}
```

| Export | Behavior |
|---|---|
| `options()` | k6 options: one `ramping-arrival-rate` scenario named `traffic` built from `LT_SCHEDULE_JSON` and `LT_MAX_IN_FLIGHT`, running the script's default export, with `maxRedirects: 0` |
| `journeyKey()` | `<run_id>-<k6 scenario name>-<iterationInTest>`; the same for every call within one journey and independent of the VU that runs it |
| `evidence(assertion, passed, {logicalKey, expected, actual, detail})` | Logs one `LT_EVENT` line. `logicalKey` defaults to `journeyKey()`; `expected`/`actual`/`detail` are included only when given; `detail` is cut to 500 characters |
| `rng(iteration)` | Returns a function yielding numbers in `[0, 1)`, seeded from `LT_SEED` and `iteration` (default: the current `iterationInTest`), so the same seed replays the same choices per journey |
| `poolItem(index)` | Returns the [fixture pool](#fixture-pool) item for `index` (default: the current `iterationInTest`). Throws when no pool is set or the index is out of range |
| `deepEqual(a, b)` | `true` when two JSON-like values are structurally equal: object key order is ignored, array order is not, `NaN` equals `NaN`, and a key set to `undefined` differs from a missing key. Use it instead of comparing `JSON.stringify` output, which depends on key order |
| `tokenFor(actorClass, journeyIndex)` | A minted [actor token](#actor-auth) for `actorClass` (the class as written in `actors`), chosen round-robin by `journeyIndex` (default: the current `iterationInTest`) from `LT_TOKENS_<CLASS>` when the actor sets `per_identity`, else `LT_TOKEN_<CLASS>`. Throws when the class has no token |
| `hmacSha256Hex(secret, data)`, `hmacSha256Base64(secret, data)` | HMAC-SHA256 of `data` keyed by `secret` (via `k6/crypto`), hex or standard base64 — for signing webhook bodies, e.g. `lt.hmacSha256Hex(__ENV.WEBHOOK_SECRET, body)` |

All bundled examples use the helper. Scripts that build their own options still work; they must then apply `LT_SCHEDULE_JSON` and `LT_MAX_IN_FLIGHT` themselves. `verify` runs k6 with `--max-redirects 0`, which overrides a script's `maxRedirects` option, so redirects are not followed. A request that sets its own `redirects` parameter still follows them; avoid that unless your journey needs it.

### Emitting evidence

Every declared assertion needs evidence: one `LT_EVENT` line per assertion per journey. `lt.evidence` writes it; without the helper, log it directly:

```js
function evidence(assertion, passed, logicalKey) {
  console.log(`LT_EVENT ${JSON.stringify({
    schema_version: 1,
    type: "assertion",
    run_id: __ENV.LT_RUN_ID,
    assertion,
    passed,
    logical_key: logicalKey,
  })}`);
}
```

Every event must carry a non-empty `logical_key` string identifying its journey (`lt.evidence` uses `journeyKey()` unless you pass `logicalKey`). Events may also carry optional diagnostic fields: `expected` and `actual` (any JSON value) and `detail` (a string of at most 500 characters). These do not change the verdict; the first three failing samples of each assertion are copied into `result.json` and shown in `report.html`. For example, `evidence("order_totals_match", body.total === 1250, key)` could add `expected: 1250, actual: body.total`.

Events with a different `run_id`, a non-boolean `passed`, a non-string `logical_key` or `detail`, a `detail` longer than 500 characters, or invalid JSON are ignored and reported as a limitation. An assertion passes only when every sample passes, every sample has a non-empty `logical_key`, and the number of distinct keys equals the planned journeys. Any `false` sample fails it. Otherwise, a sample without a key (or with `""`) makes the assertion `unknown` with the limitation `evidence without journey identity for <assertion>`, and a key that appears twice makes it `unknown` with `duplicate evidence for <key>`, so one journey reporting twice cannot stand in for a journey that reported nothing.

Check the business effect, not just the status code — for example, read the ledger back after a retried payment rather than checking that the payment call returned 200.

## Actor auth

An actor may declare an `auth` recipe. Before any fixture work or traffic, the controller signs one HS256 JWT per such actor and passes it to k6 as `LT_TOKEN_<CLASS>`, where `<CLASS>` is the actor class upper-cased with every run of non-alphanumeric characters turned into `_` (`tenant-a` → `LT_TOKEN_TENANT_A`):

```json
"actors": [
  {
    "class": "buyer",
    "count": 10,
    "auth_recipe": "hs256-test-jwt",
    "auth": {
      "kind": "jwt_hs256",
      "secret_env": "SHOP_JWT_SECRET",
      "claims": {"sub": "lt-${run_id}-${actor_index}", "role": "buyer"},
      "ttl_seconds": 900
    }
  }
]
```

- `kind` must be `jwt_hs256`. `secret_env` names an uppercase environment variable holding the signing key; the manifest never holds the key.
- `claims` is any JSON object. In its string values, `${run_id}` becomes the run ID and `${actor_index}` the actor's position in `actors` (from 0); any other `${...}` placeholder is rejected. The controller sets `iat` to the signing time and `exp` to `iat + ttl_seconds`, replacing declared values of either.
- `ttl_seconds` is 1 to 86400.
- `per_identity` (default `false`) mints `count` tokens instead of one, with `${actor_index}` set to `0` .. `count - 1` in each, so `"sub": "lt-${run_id}-${actor_index}"` gives `count` distinct subjects. They reach k6 as `LT_TOKENS_<CLASS>`, a JSON array in index order; `LT_TOKEN_<CLASS>` still holds the first. In the script, `lt.tokenFor("buyer")` picks one round-robin by journey. The whole array must fit in one environment variable (Linux allows 128 KiB per `NAME=value` string), which is roughly 700 identities with a one-claim `sub` and fewer with more claims. When it does not fit, the run stops before any fixture work or traffic, and the limitation names the variable and its size.
- Two auth actor classes that map to the same `LT_TOKEN_` name are rejected. `LT_TOKEN_*` and `LT_TOKENS_*` variables inherited from the calling shell are not passed to k6.
- A missing or empty `secret_env` makes the run an `error` before any fixture or k6 process starts, with the limitation `auth secret env NAME missing`. A signing key shorter than 8 characters is refused the same way, with `auth secret env NAME is shorter than 8 characters`, because a short value cannot be scrubbed from evidence without corrupting it.
- Tokens and signing keys are never written by the controller. Every declared credential value — actor `auth` signing keys, minted `LT_TOKEN_*` JWTs and each token in `LT_TOKENS_*`, fixture and observer `bearer_token_env` tokens and observation `headers_env` values — is replaced with `[redacted]` in everything a run keeps or prints: `engine.stdout.log`, `engine.stderr.log`, `console.log` and `metrics.jsonl` are scrubbed as text for the literal value and its JSON-escaped forms (Python's `json.dumps` spellings and Go's `encoding/json` HTML escapes), so script events are scrubbed before assertions read them — their pass flags come from the script. `events/`, `observation.json`, `fixture.json`, `result.json` (and so `report.html`, the dashboard and `verify --json`) are also scrubbed by walking every decoded JSON string, keys included, which catches any other escape spelling; observations and fixtures are evaluated against the real values first. Values shorter than 8 characters are not scrubbed. `inspect` lists the `secret_env` names.

In the script, send the token like any header: `http.get(url, { headers: { Authorization: `Bearer ${__ENV.LT_TOKEN_BUYER}` } })`.

The [final observation](#final-observation) can use the same token: set its `bearer_token_env` (or a `headers_env` value) to `LT_TOKEN_<CLASS>`. The controller resolves those names against the run's environment, which holds the minted tokens, so no separately minted token is needed.

## Run-owned fixtures

Give the run its own starting state and remove it afterwards:

```json
"owned_http": {
  "create_path": "/fixtures",
  "delete_path": "/fixtures/{fixture_id}",
  "id_pointer": "/id",
  "create_body": {"total": 1000},
  "bearer_token_env": "MYAPP_TEST_TOKEN"
}
```

Before k6 starts, the controller sends `POST create_path` with `create_body` and an `X-LiteTraffic-Run` header. It expects HTTP 200/201 and reads the ID at the JSON Pointer `id_pointer`. The ID must match `[A-Za-z0-9_-]{1,128}`. After the run, it sends `DELETE delete_path` and expects 200/204; the DELETE is also sent when the run stops on an unexpected error, such as a failed artifact write. Paths must be same-origin; `delete_path` must end in `/{fixture_id}`. `bearer_token_env` is optional and names an environment variable, never a literal token. A failed create or cleanup makes the verdict `error`.

## Command fixtures

When the app has no create/delete endpoints, seed and reset state with commands instead:

```json
"fixtures": {
  "recipe": "seeded-orders",
  "command": {
    "setup": ["psql", "--no-psqlrc", "-v", "ON_ERROR_STOP=1", "-f", "seed.sql"],
    "teardown": ["psql", "--no-psqlrc", "-v", "ON_ERROR_STOP=1", "-f", "reset.sql"],
    "timeout_seconds": 20,
    "cwd": "bundle",
    "inputs": ["seed.sql", "reset.sql"]
  }
}
```

`setup` and `teardown` are argv lists, run directly without a shell, so write `["sh", "-c", "..."]` yourself if you need one. Both run in the scenario directory (`cwd` accepts only `"bundle"`), with the caller's environment plus `LT_RUN_ID`, `LT_TARGET` and `LT_SEED`; put connection settings such as `PGHOST` or `DATABASE_URL` in the environment, not in the manifest. `timeout_seconds` (1–60) applies to each command unless `setup_timeout_seconds` or `teardown_timeout_seconds` (each 1–60) overrides it for that stage; with both stage timeouts set, `timeout_seconds` may be omitted. Both timeouts, each plus a 4-second SIGTERM/SIGKILL grace, are reserved from `max_seconds` before k6 gets its share, so a slow seed with a quick reset only reserves what it needs.

- `setup` runs before k6. A non-zero exit, a timeout or a missing executable makes the verdict `error` (`fixture setup failed: exit N`, `fixture setup failed: timed out after Ns`), and k6 is not started.
- If the last line `setup` prints to stdout is a JSON object, it is passed to k6 (and to `teardown`) as `LT_FIXTURE_JSON` — for example `psql -At -c "select json_build_object('tenant_id', id) from ..."`.
- `teardown` always runs once setup has been attempted: after a pass, a failed setup, a k6 crash or timeout, a cancel, or an unexpected error such as a failed artifact write (the error is still reported, exit 3). A non-zero exit, timeout or cancel makes the verdict `error` (`fixture teardown failed: exit N`).
- On timeout or cancel, the command's whole process group gets SIGTERM, then SIGKILL, even when the command itself already exited and left children behind. If any process in the group is still alive a second later, the hook's reason ends with `process group did not exit`.
- `fixture.json` records each command's `argv`, `exit_code`, `duration_seconds`, `status` and the last 4 KB of its stderr. Stdout is not stored.

`inputs` (optional) lists bundle-relative files the commands read, such as SQL scripts. Each must exist and stay inside the scenario directory, or `inspect` and `verify` exit 3 (`fixture input does not exist`, `fixture input must stay inside the scenario directory`). Their sha256 values are recorded in the bundle digest and in `scenario.lock.json`, so editing `seed.sql` changes `scenario_sha256`. Any `setup` or `teardown` argv element that names an existing file inside the scenario directory (for example `setup.py` in `["python3", "setup.py"]`) is hashed the same way without being listed, so editing a bundle-local setup script also invalidates approvals. Only whole argv elements are matched: a path embedded in a flag such as `--file=seed.sql`, or any other file the commands read, must be listed in `inputs` to be covered.

`inspect` prints both argv lists, each `inputs` file, every hashed command file (`fixture command file:` lines; `fixture.command.hashed_files` in `--json`, which includes the declared inputs), and the `scenario_sha256` digest, which covers the whole manifest, so a changed command is visible in the digest and in `scenario.lock.json`.

## Fixture pool

When every journey needs its own pre-seeded row (a session, a payment method, a cart), give the run a pool with one item per journey. Either point `fixtures.pool` at a JSON array file inside the scenario directory:

```json
"fixtures": {"recipe": "seeded-sessions", "pool": "sessions.json"}
```

or let a `command` fixture's setup print a JSON object with a `pool` array as its last stdout line, for example `{"pool": [{"session": "s1"}, {"session": "s2"}]}`.

- The pool must hold at least `planned_journeys` items. A short or non-array `fixtures.pool` file makes `inspect` and `verify` exit 3 (`fixture pool has N items but M journeys are planned`). A short or non-array setup `pool` makes the verdict `error` and k6 is not started; teardown still runs.
- Setting both `fixtures.pool` and a setup `pool` key makes the verdict `error` (`fixture pool is set by both fixtures.pool and the setup output`).
- The pool file must stay inside the scenario directory. Its sha256 is recorded in the bundle digest and in `scenario.lock.json`, so changing the pool changes `scenario_sha256`.
- k6 receives the pool as `LT_FIXTURE_POOL_JSON`. In the script, `lt.poolItem()` returns the item for the current journey (`iterationInTest`) and throws if there is none.
- The pool travels in a single environment variable, so keep it small: Linux caps one variable at 128 KiB, and a larger pool makes k6 fail to start. A setup `pool` is sent twice, inside `LT_FIXTURE_JSON` (the whole setup object) and as `LT_FIXTURE_POOL_JSON`, so each stays under that cap but the total environment roughly doubles; prefer a `fixtures.pool` file for large pools.

## Final observation

Check the resulting state once, after all journeys finish:

```json
"observation": {
  "path": "/reports/ledger",
  "assertion": "ledger_matches_fixture",
  "expected": {"/total": 1000, "/regions/west": 100}
}
```

The controller sends one `GET` (with `X-LiteTraffic-Run` and, if present, `X-LiteTraffic-Fixture`), requires HTTP 200 with JSON, and compares each JSON Pointer to its expected value. `assertion` must be listed in `assertions`. It supports the same optional `bearer_token_env`, which may also name an [`LT_TOKEN_<CLASS>`](#actor-auth) token minted for the run. An unreachable observer yields `unknown`, which prevents a pass; the reason (for example `observer HTTP 503`, `observer bearer token missing` or `observer unavailable: deadline exceeded`) is kept as the assertion's `reason` in `result.json` and printed after it in the `verify` text summary.

### Eventual observations

When the application settles asynchronously (a queue, a projection, a replica), let the observation re-read until it converges:

```json
"observation": {
  "path": "/reports/ledger",
  "assertion": "ledger_matches_fixture",
  "expected": {"/total": 1000},
  "until": {"deadline_seconds": 20, "interval_seconds": 1}
}
```

The controller repeats the GET every `interval_seconds` (at least 0.5) until every expectation passes or `deadline_seconds` (more than 0, at most 60, and at least `interval_seconds`) have passed; the last attempt is made at the deadline. The observation passes on the first attempt whose checks all pass. Otherwise it is `fail`, with the last reading that returned JSON, only after the deadline; it is `unknown`, with the last reason, only if no attempt got an HTTP 200 JSON response. A missing environment variable or disallowed `origin_env` stops it at once, without a request. The result adds `attempts` (GETs sent) and `elapsed_seconds`, and `observer_requests` counts every attempt.

Each attempt keeps the 5 s request deadline, so `inspect` reserves `deadline_seconds` rounded up plus 5 s of `max_seconds`, and ⌈`deadline_seconds` / `interval_seconds`⌉ + 1 requests of `max_requests`, for the observation. With the example above that is 25 s and 21 requests.

### Matchers

Each `expected` value is either a literal, compared for equality, or a matcher object with exactly one of these keys:

| Matcher | Passes when the value at the pointer |
|---|---|
| `{"eq": v}` | exists and equals `v` as JSON: `true`/`false` never equal `1`/`0`, at any depth inside arrays and objects, while `1` equals `1.0` |
| `{"gte": n}` / `{"lte": n}` | exists, is a number (not a boolean), and is ≥ / ≤ `n` |
| `{"len": n}` | exists, is an array, string or object, and has `n` items/characters/keys |
| `{"exists": true\|false}` | is present / absent (a present `null` counts as present) |

An object is treated as a matcher only when all its keys are matcher keys; any other object is a literal. Wrap a literal object that looks like a matcher in `eq`, e.g. `{"eq": {"len": 2}}`. A matcher with more than one key or a wrongly typed operand fails validation. The empty pointer `""` addresses the whole response body, for example `{"": {"len": 2}}` on an array.

### Plan-aware expected values

A literal or matcher operand that is a string of exactly the form `"${EXPR}"` is an expression, evaluated before the comparison. `EXPR` may use non-negative integer literals written with ASCII digits `0-9`, `+`, `-` (binary or unary), `*`, parentheses, and only the names `planned_journeys` (the manifest's planned journey count) and `seed` (the run's `--seed`). It is parsed by a small dedicated parser, never Python `eval`. Any other name, operator or number form (for example `/`, `**`, `1.5`, `run_id`) fails manifest validation, so `inspect` rejects it. So does a whole `"${EXPR}"` string longer than 200 characters, or one nesting parentheses and unary minuses more than 32 deep. Strings that only contain `${` elsewhere, and values nested inside literal objects or arrays, stay literal.

```json
"expected": {"/balance": "${planned_journeys * 100}", "/orders": {"gte": "${planned_journeys - 1}"}}
```

`inspect` shows the resolved values for its `--seed` as `observation_expected`.

`observation.json` keeps `expected` (with expressions resolved) and `actual`, adds `expressions` (the pointers whose expected value was written as an expression, as written) when any exist, and adds `checks`: for each pointer, the normalized `matcher` (literals become `{"eq": …}`), the recorded `actual` value and `pass`, plus a `reason` such as `gte needs a number, got boolean` when a `gte`/`lte` check fails because the value is not a number. The observation passes only when every check passes.

The recorded `actual` (in `actual`, `checks` and `result.json`) is compact:

- `exists`: `true` or `false` (whether the pointer was found).
- `len`: `{"type", "length"}` of the value, where `type` is its JSON type (`array`, `string`, `object`, `number`, `boolean`, `null`) and `length` is present only for arrays, strings and objects; the value itself is never stored.
- any other matcher: the value, or `null` when missing. When its JSON form exceeds 2048 bytes it is replaced by a string of its first bytes ending in `...[truncated]`, 2048 bytes in total. Matching always uses the full value.

### Multiple observations

To check several things after the run, use `observations` instead of `observation`:

```json
"observations": [
  {"path": "/reports/ledger", "assertion": "ledger_matches_fixture", "expected": {"/total": 1000}},
  {"path": "/reports/audit", "assertion": "audit_complete", "expected": {"/entries": {"len": 20}}}
]
```

Each entry has its own `assertion`; every one must be listed in `assertions` and they must be distinct. They run one after another, in order, after k6 finishes; each gets 5 s and one request in the budgets (more with [`until`](#eventual-observations)). `observation.json` is then a list with one entry per observation, in order, and each assertion gets its own row in `result.json`. A legacy single `observation` still writes a single object. If k6 did not finish, every observation is `unknown` with reason `engine did not finish`.

### Observing another origin

To read state from a second service, such as PostgREST in front of the database, set `origin` and list it in the top-level `allowed_origins`:

```json
"allowed_origins": ["https://db.example.test"],
"observation": {
  "origin": "https://db.example.test",
  "path": "/rest/v1/orders?select=id&status=eq.paid",
  "assertion": "paid_orders_persist",
  "expected": {"": {"len": 20}},
  "headers_env": {"apikey": "DB_ANON_KEY"}
}
```

- `origin` must be an absolute http(s) URL without credentials, query or fragment; link-local and cloud metadata addresses are rejected, as for targets. A trailing slash is ignored and scheme and host are compared case-insensitively. An origin not listed in `allowed_origins` fails validation. Without `origin` or `origin_env`, the observation reads the target.
- `headers_env` maps header names to uppercase environment variable names. Values are read at run time and sent only on the observation request; if the endpoint echoes one back, it is `[redacted]` in every kept artifact and in `verify --json` (see [actor auth](#actor-auth)), though the assertion is evaluated against the real value first. A missing or empty variable makes the observation `unknown` with reason `observer header env NAME missing` and no request is sent. `inspect` lists the variable names under `secret_env`.
- The run and fixture headers, the `bearer_token_env` token (as `Authorization: Bearer`) and the `headers_env` values are all sent to the other origin.

### Origin from the environment

When the origin differs per environment, set `origin_env` (instead of `origin`) to an uppercase environment variable holding it:

```json
"allowed_origins": ["https://db.staging.example.test"],
"allowed_origins_env": "DB_ALLOWED_ORIGINS",
"observation": {"origin_env": "DB_ORIGIN", "path": "/rest/v1/orders", "assertion": "paid_orders_persist", "expected": {"": {"len": 20}}}
```

At run time the value must pass the same checks as `origin` and match, by scheme and host case-insensitively, an entry of `allowed_origins` or of the comma-separated list in the `allowed_origins_env` variable. Otherwise the observation is `unknown` without sending a request, with reason `observer origin env DB_ORIGIN missing` (unset or empty) or `observer origin env DB_ORIGIN is not an allowed origin`; an invalid entry in the list gives `observer allowed_origins_env DB_ALLOWED_ORIGINS holds an invalid origin`. Reasons name the variables, never their values.

## Validating a new scenario

1. `litetraffic inspect` until the manifest is valid and the budgets are what you expect.
2. Run against a known-good build; expect `pass`.
3. Run against a deliberately broken build; expect `fail` on the assertion you meant to catch.
4. Repeat with `--repeat 3` to expose seed-sensitive results.

A scenario that never fails has not shown it can catch anything. See [safety](safety.md) before pointing writes at shared data.
