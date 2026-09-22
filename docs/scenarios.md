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
| `script` | yes | Script path relative to the bundle; must stay inside the directory |
| `actors` | yes | `[{"class", "count", "auth_recipe"}]` — descriptive labels for reviewers; the controller does not execute them |
| `fixtures.recipe`, `fixtures.parameters` | yes / no | Descriptive fixture label and parameters |
| `fixtures.owned_http` | no | Run-owned HTTP fixture the controller creates and deletes ([below](#run-owned-fixtures)) |
| `journeys` | yes | `[{"name", "max_requests", "max_writes"}]` per-journey maxima used for budget checks |
| `schedule` | yes | `unit: "journeys_per_second"` plus exactly one of `phases` or `profile` |
| `assertions` | yes | Assertion IDs that must receive evidence for a pass |
| `observer` | yes | Descriptive label for how the effect is observed |
| `observation` | no | Final read-only HTTP check ([below](#final-observation)) |
| `budgets` | yes | `max_seconds`, `max_requests`, `max_write_attempts`, `max_in_flight`, `max_artifact_bytes` |

`inspect` rejects a manifest when the schedule could exceed its budgets: planned journeys × the largest `max_requests` (plus fixture and observer calls) must fit `max_requests`, the same for writes, and the scheduled duration plus fixture (10 s) and observation (5 s) deadlines must fit `max_seconds`.

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

The bundled examples show the standard executor setup. `verify` runs k6 with `--max-redirects 0`, which overrides a script's `maxRedirects` option, so redirects are not followed. A request that sets its own `redirects` parameter still follows them; avoid that unless your journey needs it.

### Emitting evidence

Every declared assertion needs evidence. Log one line per assertion per journey:

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

Events with a different `run_id`, a non-boolean `passed`, or invalid JSON are ignored and reported as a limitation. An assertion passes only when it has exactly one passing sample per planned journey. Any `false` sample fails it.

Check the business effect, not just the status code — for example, read the ledger back after a retried payment rather than checking that the payment call returned 200.

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

## Validating a new scenario

1. `litetraffic inspect` until the manifest is valid and the budgets are what you expect.
2. Run against a known-good build; expect `pass`.
3. Run against a deliberately broken build; expect `fail` on the assertion you meant to catch.
4. Repeat with `--repeat 3` to expose seed-sensitive results.

A scenario that never fails has not shown it can catch anything. See [safety](safety.md) before pointing writes at shared data.
