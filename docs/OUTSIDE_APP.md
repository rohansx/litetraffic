# Outside-app check: Gitea

[Documentation index](README.md)

The [technical specification](TECH_SPEC.md) gates authoring work on an outside-app check: run the preview against an unfamiliar, real application before polishing anything that generates scenarios. This is the first such check. The target is a stock [Gitea](https://github.com/go-gitea/gitea) 28.1 container with SQLite; the scenarios are in [`examples/gitea`](../examples/gitea).

## What ran

| Scenario | Journeys | Requests | Result |
|---|---|---|---|
| `init tenant-isolation` from `kit.json`: two users, two private repositories, `GET` repository and issue list, `PATCH` description, anonymous probes, two final observations | 10 | 180 + 2 | `pass`, 0 unexpected HTTP failures |
| Same kit after making Bob a read collaborator on Alice's repository | 10 | 180 + 2 | `fail` on `cross_tenant_read_blocked` only; writes still refused, victim unchanged |
| `gitea-issues`: file an issue and read it back per journey; `setup.sh`/`teardown.sh` recreate the repository; the observation polls `open_issues_count` until it equals the planned journeys | 8 | 16 + ≤11 | `pass` |

Both passing runs were repeated against a freshly restarted (cold) container.

## What worked without changes

- `token_env` identities, markers in repository descriptions, `bearer_token_env` observations and `secret_env` redaction all applied to a real token scheme (`Authorization: Bearer` is accepted by Gitea alongside `token`).
- Command fixtures with `LT_TARGET`/`LT_RUN_ID` in the environment and a JSON last line handed to k6 as `LT_FIXTURE_JSON`.
- `until` polling against a count the application maintains itself.
- `--capture docker:lt-gitea` recorded the container's log and resource use; its error signatures explained the first failure (below).

## What the check changed

- **`drain_seconds` for the kit.** The generated `max_seconds` was the schedule plus the fixed 2-second engine slack, so an 18-request journey admitted in the schedule's last second had 2 seconds to finish. Against a cold Gitea (requests over a second each while it computed language statistics and updated token stamps) k6 was stopped at its share with every journey unfinished: `inconclusive`, `timed_out`, `k6 dropped 4 iterations`. The kit config now takes `drain_seconds`; the example uses 10.
- The same first run showed `500`/`401 … context canceled` lines in Gitea's captured log. They are the application's view of a client stopped mid-request, not application errors; the example README says so, since a reader of `server/lt-gitea.log` would otherwise blame Gitea.

## Noted, not changed

- Schedule `rate` is a whole number of journeys per second, so a schedule cannot admit fewer than one journey per second or hold a quiet tail; `drain_seconds` covers the tail, and sub-1/s rates remain a possible later change.
- Gitea's token authentication writes to its database on every request, so SQLite serialises them: 25 ms alone, about 400 ms with six in flight. The kit's default `max_in_flight` of 6 is therefore the load, and `metrics.by_operation` p95 reports it. A target backed by Postgres would behave differently; nothing in the preview assumes either.
- The handwritten scenario needed no new helper: `lt.journeyKey()`, `lt.stage()` and `lt.evidence()` covered a create-then-read journey with a run-scoped fixture.
