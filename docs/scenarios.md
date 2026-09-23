# Writing scenarios

[Documentation index](README.md) · [CLI reference](cli.md)

A scenario is a directory with two files:

```text
my-scenario/
├── manifest.json   # declared shape, schedule, assertions, budgets
└── journeys.js     # k6 script that performs the journeys and emits evidence
```

The fastest start is to copy the bundled example closest to your case and change its API calls and expectations. Validate every change with `litetraffic inspect`.

## Manifest

Unknown fields are rejected. `schema_version` must be `1`.

| Field | Required | Meaning |
|---|---|---|
| `name` | yes | Scenario name recorded in run metadata |
| `script` | yes | Script path relative to the bundle; must stay inside the directory. Relative imports it reaches (`./`, `../`) must also resolve to existing files inside the directory, or loading fails. Remote modules (any `scheme://` specifier, such as `https://jslib.k6.io/...`) and `k6/x/...` extensions are rejected in the script and every local import; k6 built-ins such as `k6`, `k6/http` and `k6/execution` are allowed |
| `actors` | yes | `[{"class", "count", "auth_recipe", "auth"}]` — `class`, `count` and `auth_recipe` are descriptive labels for reviewers; the optional `auth` recipe makes the controller mint a token for the actor class ([below](#actor-auth)) |
| `fixtures.recipe`, `fixtures.parameters` | yes / no | Descriptive fixture label and parameters |
| `fixtures.owned_http` | no | Run-owned HTTP fixture the controller creates and deletes ([below](#run-owned-fixtures)) |
| `fixtures.command` | no | Setup and teardown commands run on the controller host ([below](#command-fixtures)); cannot be combined with `owned_http` |
| `fixtures.pool` | no | Bundle-relative JSON array file with one item per journey ([below](#fixture-pool)) |
| `journeys` | yes | `[{"name", "max_requests", "max_writes", "min_overlap"?, "expected_statuses"?}]` per-journey maxima used for budget checks; optional `min_overlap` maps a k6 `operation` tag to the minimum number of those requests that must be observed in flight at once, positive integers; when several journeys name the same operation the largest minimum applies; optional `expected_statuses` maps a k6 `operation` tag to a non-empty list of HTTP status codes (100-599) that are intended outcomes for that operation, merged across journeys and used only for `metrics.unexpected_http_failure_rate` (see [results](results.md)) |
| `schedule` | yes | `unit: "journeys_per_second"` plus exactly one of `phases` or `profile` |
| `assertions` | yes | Assertion IDs that must receive evidence for a pass |
| `observer` | yes | Descriptive label for how the effect is observed |
| `observation` | no | Final read-only HTTP check ([below](#final-observation)) |
| `allowed_origins` | no | Absolute http(s) origins the observation may read besides the target ([below](#observing-another-origin)) |
| `budgets` | yes | `max_seconds`, `max_requests`, `max_write_attempts`, `max_in_flight`, `max_artifact_bytes` |

`inspect` rejects a manifest when the schedule could exceed its budgets: planned journeys × the largest `max_requests` (plus fixture and observer calls) must fit `max_requests`, the same for writes, and the scheduled duration plus fixture (10 s for `owned_http`, 2 × `timeout_seconds` for `command`) and observation (5 s) deadlines must fit `max_seconds`.

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
| `LT_TOKEN_<CLASS>` | Present only for actors with an [`auth` recipe](#actor-auth); a signed JWT for that actor class |

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

Events may also carry optional diagnostic fields: `expected` and `actual` (any JSON value), `detail` (a string of at most 500 characters), and `logical_key` (a string). They do not change the verdict; the first three failing samples of each assertion are copied into `result.json` and shown in `report.html`. For example, `evidence("order_totals_match", body.total === 1250, key)` could add `expected: 1250, actual: body.total`.

Events with a different `run_id`, a non-boolean `passed`, a non-string `logical_key` or `detail`, a `detail` longer than 500 characters, or invalid JSON are ignored and reported as a limitation. An assertion passes only when it has exactly one passing sample per planned journey. Any `false` sample fails it. When samples carry a `logical_key`, each key may appear only once per assertion: a repeated key makes the assertion `unknown` and adds the limitation `duplicate evidence for <key>`, so one journey reporting twice cannot stand in for a journey that reported nothing. Samples without a `logical_key` are only counted.

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
- Two auth actor classes that map to the same `LT_TOKEN_` name are rejected. `LT_TOKEN_*` variables inherited from the calling shell are not passed to k6.
- A missing or empty `secret_env` makes the run an `error` before any fixture or k6 process starts, with the limitation `auth secret env NAME missing`.
- Tokens and signing keys are never written by the controller. If a script prints one, it is replaced with `[redacted]` in `engine.stdout.log`, `engine.stderr.log`, `console.log` and `metrics.jsonl`. `inspect` lists the `secret_env` names.

In the script, send the token like any header: `http.get(url, { headers: { Authorization: `Bearer ${__ENV.LT_TOKEN_BUYER}` } })`.

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

Before k6 starts, the controller sends `POST create_path` with `create_body` and an `X-LiteTraffic-Run` header. It expects HTTP 200/201 and reads the ID at the JSON Pointer `id_pointer`. The ID must match `[A-Za-z0-9_-]{1,128}`. After the run, it sends `DELETE delete_path` and expects 200/204. Paths must be same-origin; `delete_path` must end in `/{fixture_id}`. `bearer_token_env` is optional and names an environment variable, never a literal token. A failed create or cleanup makes the verdict `error`.

## Command fixtures

When the app has no create/delete endpoints, seed and reset state with commands instead:

```json
"fixtures": {
  "recipe": "seeded-orders",
  "command": {
    "setup": ["psql", "--no-psqlrc", "-v", "ON_ERROR_STOP=1", "-f", "seed.sql"],
    "teardown": ["psql", "--no-psqlrc", "-v", "ON_ERROR_STOP=1", "-f", "reset.sql"],
    "timeout_seconds": 20,
    "cwd": "bundle"
  }
}
```

`setup` and `teardown` are argv lists, run directly without a shell, so write `["sh", "-c", "..."]` yourself if you need one. Both run in the scenario directory (`cwd` accepts only `"bundle"`), with the caller's environment plus `LT_RUN_ID`, `LT_TARGET` and `LT_SEED`; put connection settings such as `PGHOST` or `DATABASE_URL` in the environment, not in the manifest. `timeout_seconds` (1–60) applies to each command, and both timeouts are reserved from `max_seconds` before k6 gets its share.

- `setup` runs before k6. A non-zero exit, a timeout or a missing executable makes the verdict `error` (`fixture setup failed: exit N`, `fixture setup failed: timed out after Ns`), and k6 is not started.
- If the last line `setup` prints to stdout is a JSON object, it is passed to k6 (and to `teardown`) as `LT_FIXTURE_JSON` — for example `psql -At -c "select json_build_object('tenant_id', id) from ..."`.
- `teardown` always runs once setup has been attempted: after a pass, a failed setup, a k6 crash or timeout, or a cancel. A non-zero exit, timeout or cancel makes the verdict `error` (`fixture teardown failed: exit N`).
- On timeout or cancel, the command's process group gets SIGTERM, then SIGKILL.
- `fixture.json` records each command's `argv`, `exit_code`, `duration_seconds`, `status` and the last 4 KB of its stderr. Stdout is not stored.

`inspect` prints both argv lists and the `scenario_sha256` digest, which covers the whole manifest, so a changed command is visible in the digest and in `scenario.lock.json`.

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

The controller sends one `GET` (with `X-LiteTraffic-Run` and, if present, `X-LiteTraffic-Fixture`), requires HTTP 200 with JSON, and compares each JSON Pointer to its expected value. `assertion` must be listed in `assertions`. It supports the same optional `bearer_token_env`. An unreachable observer yields `unknown`, which prevents a pass.

### Matchers

Each `expected` value is either a literal, compared for equality, or a matcher object with exactly one of these keys:

| Matcher | Passes when the value at the pointer |
|---|---|
| `{"eq": v}` | exists and equals `v` |
| `{"gte": n}` / `{"lte": n}` | exists, is a number (not a boolean), and is ≥ / ≤ `n` |
| `{"len": n}` | exists, is an array, string or object, and has `n` items/characters/keys |
| `{"exists": true\|false}` | is present / absent (a present `null` counts as present) |

An object is treated as a matcher only when all its keys are matcher keys; any other object is a literal. Wrap a literal object that looks like a matcher in `eq`, e.g. `{"eq": {"len": 2}}`. A matcher with more than one key or a wrongly typed operand fails validation. The empty pointer `""` addresses the whole response body, for example `{"": {"len": 2}}` on an array.

### Plan-aware expected values

A literal or matcher operand that is a string of exactly the form `"${EXPR}"` is an expression, evaluated before the comparison. `EXPR` may use non-negative integer literals, `+`, `-` (binary or unary), `*`, parentheses, and only the names `planned_journeys` (the manifest's planned journey count) and `seed` (the run's `--seed`). It is parsed by a small dedicated parser, never Python `eval`. Any other name, operator or number form (for example `/`, `**`, `1.5`, `run_id`) fails manifest validation, so `inspect` rejects it. Strings that only contain `${` elsewhere, and values nested inside literal objects or arrays, stay literal.

```json
"expected": {"/balance": "${planned_journeys * 100}", "/orders": {"gte": "${planned_journeys - 1}"}}
```

`inspect` shows the resolved values for its `--seed` as `observation_expected`.

`observation.json` keeps `expected` (with expressions resolved) and `actual`, adds `expressions` (the pointers whose expected value was written as an expression, as written) when any exist, and adds `checks`: for each pointer, the normalized `matcher` (literals become `{"eq": …}`), the `actual` value (`null` when missing) and `pass`. The observation passes only when every check passes.

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

- `origin` must be an absolute http(s) URL without credentials; link-local and cloud metadata addresses are rejected, as for targets. A trailing slash is ignored. An origin not listed in `allowed_origins` fails validation. Without `origin`, the observation reads the target.
- `headers_env` maps header names to uppercase environment variable names. Values are read at run time and sent only on the observation request; they are never written to artifacts. A missing or empty variable makes the observation `unknown` with reason `observer header env NAME missing` and no request is sent. `inspect` lists the variable names under `secret_env`.
- The run and fixture headers are sent to the other origin too.

## Validating a new scenario

1. `litetraffic inspect` until the manifest is valid and the budgets are what you expect.
2. Run against a known-good build; expect `pass`.
3. Run against a deliberately broken build; expect `fail` on the assertion you meant to catch.
4. Repeat with `--repeat 3` to expose seed-sensitive results.

A scenario that never fails has not shown it can catch anything. See [safety](safety.md) before pointing writes at shared data.
