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

# Outside-app check: Hi.Events

A second outside-app check, against a different stack: a stock [Hi.Events](https://github.com/HiEventsDev/hi.events) all-in-one image (Laravel + React on Postgres + Redis). The scenario encodes a **known, already-fixed** oversell bug — CVE-2026-57959, reported upstream in [#1223](https://github.com/HiEventsDev/hi.events/issues/1223) and fixed in [#1233](https://github.com/HiEventsDev/hi.events/pull/1233); LiteTraffic did not discover it — as the invariant *"a single-use promo code is redeemed at most once"*. The point of the check is that the **same** manifest turns a published bug into a black-box FAIL/PASS gate that runs unchanged against two stock release images. The scenarios are in [`examples/hievents`](../examples/hievents).

| Scenario | Target image | Journeys | Result |
|---|---|---|---|
| `hievents-promo-oversell`: 10 buyers each reserve one ticket with a single-use 100%-off code, hold the reservation, then complete; the observation reads the code's `order_usage_count` back over HTTP and asserts `<= 1` | `v1.9.0-beta` (vulnerable) | 10 | `fail` — `order_usage_count = 10`, 0 unexpected HTTP failures |
| Same manifest, only `--target` and the organizer token change | `v.1.11.0-beta` (fixed) | 10 | `pass` — `order_usage_count = 1`, nine orders fall back to full price and stall at `reserved` |

## What this check adds over Gitea

- **Status codes lie, the read-back doesn't.** Every HTTP call is `200`/`201` in both runs. A status-code or error-rate check misses the bug; the business-effect read-back catches it. (The discount is also visible in the reservation response bodies — `total_gross: 0` on all ten reservations on the vulnerable build, versus one on the fixed — so a body-inspecting test would catch it too.)
- **It is a stale-counter logic bug, not a race.** Concurrency is not what triggers it; the reserve-before-complete *hold* is. A strictly sequential variant (reserve all ten, then complete all ten) was run against both builds and reproduced the same FAIL/PASS, so the burst shape in the shipped scenario is an incidental scheduling device.
- **Black-box, but with admin access.** The fixture and the oracle need an organizer (admin) JWT to create/delete promo codes and read `/api/events/1/promo-codes`. The 100%-off code keeps the demo payment-free, so the paid-fallback path is not exercised.

## Noted, not changed (Hi.Events)

- The check is a **reproduction** of a published bug, not a discovery. The oracle, the reserve-before-complete ordering and the per-journey `session_identifier` were written from the upstream issue and fix diff. Deterministic-simulation tools and an ordinary integration test would catch this bug too; the upstream fix ships unit tests for it.
- The scenario is deterministic by construction, so repeated seeds are repeats of one test, not evidence of robustness under varied load.
- A residual simultaneous-reservation window in `countActivePromoCodeUsage()` (a plain `COUNT`, no row lock) is untested; this scenario cannot exercise it.
