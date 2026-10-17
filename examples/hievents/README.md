# Hi.Events: a known oversell bug encoded as a FAIL/PASS regression check

[Hi.Events](https://github.com/HiEventsDev/hi.events) is an open-source event-ticketing
platform (Laravel + React). This example takes a **known, already-fixed** oversell bug
(CVE-2026-57959) and encodes it as a LiteTraffic regression check: the invariant *"a
single-use promo code is redeemed at most once"*. The **same** scenario runs against two
stock release images side by side — it **FAILs** on the vulnerable build and **PASSes** on
the fixed one, over black-box HTTP, the same shape as the [Gitea](../gitea) outside-app
check.

The bug was reported upstream by George Chen in
[issue #1223](https://github.com/HiEventsDev/hi.events/issues/1223) and fixed in
[PR #1233](https://github.com/HiEventsDev/hi.events/pull/1233). **LiteTraffic did not
discover it.** The oracle, the reserve-before-complete ordering and the per-journey
`session_identifier` trick were all written from that issue and the fix diff — this is a
reproduction written after the answer was known, turned into a black-box gate.

## The bug

A promo code with `max_allowed_usages = 1` can be redeemed by many orders.

- Issue: <https://github.com/HiEventsDev/hi.events/issues/1223>
- Fix PR: <https://github.com/HiEventsDev/hi.events/pull/1233> (commit
  [`b551d55`](https://github.com/HiEventsDev/hi.events/commit/b551d55a31b2906167b840fddca17f07e0275cb6))
- CVE-2026-57959

On `v1.9.0-beta`, `PromoCodeDomainObject::isValid()` compares `order_usage_count` against
`max_allowed_usages`. That counter is only incremented **after an order completes**, by
`UpdateEventStatisticsJob` (dispatched from `UpdateEventStatsListener` on
`OrderStatusChangedEvent` once the order is completed). So while orders are being *reserved*,
the counter is still `0`, every reservation passes `isValid()`, and every buyer gets the
discount. This is a **stale-counter logic bug, not a timing race**: the usage limit is
checked against a counter that only moves on completion. The outcome does not depend on how
requests interleave, and it reproduces with `QUEUE_CONNECTION=sync` and a single client.
(The upstream issue is filed under CWE-362, but no race is needed to trigger it.)

The fix (`v.1.10.0-beta` and later) replaces that check with
`PromoCodeUsageValidationService::isPromoCodeUsable()`, which derives usage from a **live**
count of orders currently holding the code (completed, awaiting offline payment, or unexpired
reservations) via `OrderRepository::countActivePromoCodeUsage()` — so only the first
reservation gets the discount and the rest fall back to full price.

The two images (the leading `v.` on the fixed tag is literal):

| Target | Image | Port |
|---|---|---|
| vulnerable | `daveearley/hi.events-all-in-one:v1.9.0-beta` | 8081 |
| fixed | `daveearley/hi.events-all-in-one:v.1.11.0-beta` | 8082 |

## Install the CLI

The `litetraffic` CLI is not on your `PATH` by default. Create a virtualenv with a modern
pip and install from this repository, and install k6 v2.2.0 — see
[docs/installation.md](../../docs/installation.md) for the full requirements. LiteTraffic needs
Python 3.11 or newer; on macOS `python3` is often the system 3.9, so name a newer one
explicitly (for example `python3.13` from Homebrew):

```bash
python3.13 -m venv .venv && . .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -e .            # from the repository root
litetraffic doctor                     # checks k6 v2.2.0 is on PATH
```

## Bring it up

`docker-compose.yml` runs one `postgres:17-alpine` (two databases, `hievents_vuln` and
`hievents_fixed`, created by `initdb.sql`), one `redis:7-alpine`, and the two app instances,
each with its own `APP_KEY`, `JWT_SECRET` (from your environment), database and host port. `QUEUE_CONNECTION=sync`, so
the demo cannot be dismissed as "just a missing queue worker": the counter is read stale
regardless. Both images publish `linux/amd64` and `arm64` variants, so they run natively on
Apple Silicon; we have only exercised them on arm64 here.

```bash
# Per-instance keys, generated locally and never committed (Laravel takes a raw 32-character key).
# Keep them for the shell session: recreating the containers with new keys logs everyone out.
export LT_HIEVENTS_VULN_APP_KEY=$(openssl rand -hex 16) LT_HIEVENTS_FIXED_APP_KEY=$(openssl rand -hex 16)
export LT_HIEVENTS_VULN_JWT_SECRET=$(openssl rand -hex 32) LT_HIEVENTS_FIXED_JWT_SECRET=$(openssl rand -hex 32)
docker compose -f examples/hievents/docker-compose.yml -p lt-hievents up -d   # from the repository root
# Wait until each app's HTTP API answers at all (first boot runs migrations). A 404 is
# fine — it just means the API is up; event 1 does not exist until bootstrap runs below,
# so do NOT wait for a 2xx here.
ready() {  # $1 = base url
  until [ "$(curl -s -o /dev/null -w '%{http_code}' "$1/api/public/events/1")" != 000 ]; do
    sleep 3
  done
}
ready http://127.0.0.1:8081
ready http://127.0.0.1:8082
```

(`curl -sf` would exit non-zero on the 404 and loop forever on a fresh database, because
event 1 is not created until the bootstrap step below. The probe above accepts any HTTP
status — it only checks that the server is answering.)

## Bootstrap each instance once

On a fresh database, register an account, create the organizer, one PAID product and a LIVE
event. The first of each gets id `1`, which the scenario's final observation path
(`/api/events/1/promo-codes`) relies on. The block below is **idempotent**: it checks whether
event 1 already exists and skips if so, and it fails loudly (`set -e` inside a subshell, `curl -f`, every
status checked) rather than silently POSTing duplicate organizers and events on a re-run. Run
it against each target:

```bash
bootstrap() (        # $1 = base, e.g. http://127.0.0.1:8081; a subshell, so set -e never leaks into your shell
  set -euo pipefail
  B="$1/api"

  # Idempotency guard: if event 1 is already published, bootstrap has run — skip.
  if curl -sf -o /dev/null "$B/public/events/1"; then
    echo "bootstrap: $1 already has event 1, skipping" >&2
    return 0
  fi

  # register is a no-op (409) if the account exists; that is fine, so do not -f it.
  curl -s -o /dev/null -X POST "$B/auth/register" -H 'Content-Type: application/json' \
    -d '{"first_name":"Org","last_name":"Admin","email":"admin@lt.test","password":"Password123!","password_confirmation":"Password123!","timezone":"UTC","currency_code":"USD"}'

  T=$(curl -fs -D- -o /dev/null -X POST "$B/auth/login" -H 'Content-Type: application/json' \
    -d '{"email":"admin@lt.test","password":"Password123!"}' \
    | awk -F': ' 'tolower($1)=="x-auth-token"{print $2}' | tr -d '\r')
  [ -n "$T" ] || { echo "bootstrap: login returned no token for $1" >&2; return 1; }

  curl -fs -o /dev/null -X POST "$B/organizers" -H "Authorization: Bearer $T" -H 'Content-Type: application/json' \
    -d '{"name":"LT Organizer","email":"org@lt.test","currency":"USD","timezone":"UTC"}'
  curl -fs -o /dev/null -X POST "$B/events" -H "Authorization: Bearer $T" -H 'Content-Type: application/json' \
    -d '{"title":"LT Oversell Demo","organizer_id":1,"start_date":"2027-01-01T10:00:00","end_date":"2027-01-01T18:00:00","timezone":"UTC","currency":"USD"}'
  curl -fs -o /dev/null -X POST "$B/events/1/products" -H "Authorization: Bearer $T" -H 'Content-Type: application/json' \
    -d '{"title":"General Admission","type":"PAID","product_type":"TICKET","product_category_id":1,"initial_quantity_available":100000,"prices":[{"price":25.00}]}'
  curl -fs -o /dev/null -X PUT "$B/events/1/status" -H "Authorization: Bearer $T" -H 'Content-Type: application/json' -d '{"status":"LIVE"}'
  echo "bootstrap: $1 done" >&2
)
bootstrap http://127.0.0.1:8081
bootstrap http://127.0.0.1:8082
```

`setup.sh` (the fixture) creates a **fresh single-use 100%-off promo code** on event 1 for
every run, so runs stay independent; `teardown.sh` removes it. The 100% discount is what keeps
the demo payment-free — completed orders need no Stripe interaction. Note that this means the
**paid fallback path is never exercised**: on the fixed build the nine non-discounted orders
just sit at `reserved`. The upstream report used a 50% code.

## Run it

`LT_HIEVENTS_ORG_TOKEN` is the organizer (admin) JWT the fixture and the final observation
use — the check is black-box over HTTP but needs organizer access to create and delete promo
codes and to read `/api/events/1/promo-codes`. Grab a fresh token for the target you are
about to run (tokens last 7 days):

```bash
token() { curl -fs -D- -o /dev/null -X POST "$1/api/auth/login" -H 'Content-Type: application/json' \
  -d '{"email":"admin@lt.test","password":"Password123!"}' | awk -F': ' 'tolower($1)=="x-auth-token"{print $2}' | tr -d '\r'; }

# Vulnerable build -> FAIL
export LT_HIEVENTS_ORG_TOKEN=$(token http://127.0.0.1:8081)
litetraffic verify examples/hievents --target http://127.0.0.1:8081 --seed 7 --capture docker:lt-hievents-vuln

# Fixed build -> PASS
export LT_HIEVENTS_ORG_TOKEN=$(token http://127.0.0.1:8082)
litetraffic verify examples/hievents --target http://127.0.0.1:8082 --seed 7 --capture docker:lt-hievents-fixed
```

Nothing in the scenario changes between the two targets — only `--target` and the token.

## What the check asserts

Each of 10 buyer journeys reserves one ticket with the promo code (its own checkout session,
passed as `?session_identifier=`, so the server's `deleteExistingOrders()` cannot wipe a
sibling's reservation), holds the reservation, then completes. The scenario starts the 10
journeys in about one second and holds each reservation for 2 s. The **only** purpose of that
shape is to guarantee that every reservation lands before any completion; concurrency is not
required and request overlap plays no part (see [Honest caveat](#honest-caveat)). The final
observation reads the promo code's own recorded usage over HTTP and asserts it is `<= 1`:

```json
"observation": {
  "path": "/api/events/1/promo-codes",
  "expected": {"/data/0/order_usage_count": {"lte": 1}, "/data/0/max_allowed_usages": 1}
}
```

Observed (seeds 42–44 and 7; the scenario is deterministic by construction, so repeated seeds
are repeats of one test, not evidence of robustness under varied load):

| Target | `order_usage_count` | journeys completed | verdict |
|---|---|---|---|
| `v1.9.0-beta` (`:8081`) | **10** | 10 / 10 | **FAIL** (expected `<= 1`, actual `10`) |
| `v.1.11.0-beta` (`:8082`) | **1** | 1 / 10 | **PASS** |

(The CLI's headline `journeys: 10/10` line counts journeys *delivered* on both builds — all
ten ran to the end. The table's "journeys completed" column is the *completed* funnel stage:
on the fixed build only the first journey reaches `completed`, so `journeys: 10/10` and `1/10`
completed are both correct and describe different things.)

Every HTTP call is a `200`/`201` in both runs (`unexpected HTTP failure rate: 0.0%`), so a
status-code-only check or an error-rate SLO would miss the bug entirely. The violation is
*also* visible in the reservation response bodies, not only in the admin read-back: on the
vulnerable build all ten reservations come back with `total_gross: 0` (the 100%-off discount
applied), while on the fixed build only the first does and the other nine come back at full
price (`total_gross: 25`). This check asserts on the recorded usage count; a test that
inspected response bodies would also catch it. On the fixed build only the first journey
reaches `completed`; the other nine fall back to full price and stall at `reserved` (they
cannot complete without payment), which is the intended behaviour, so the run is a clean PASS.

## Honest caveat

This is a **stale-counter** bug, not a timing race, and **concurrency is not what triggers
it** — the reserve-before-complete *hold* does. It reproduces whenever reservations outrun
completions, including strictly sequentially. We tested this directly with a one-at-a-time
variant (reserve all ten orders first, then complete them, no overlap at all):

- **Vulnerable `v1.9.0-beta`:** oversells just the same — all ten reservations get the
  discount (`total_gross: 0`), all ten complete, `order_usage_count = 10`.
- **Fixed `v.1.11.0-beta`:** holds — only the first reservation gets the discount, the other
  nine come back at full price (`total_gross: 25`) and stall at `reserved`,
  `order_usage_count = 1`.

So the burst shape in the shipped scenario is incidental: it is a scheduling device to make
"all reservations before any completion" deterministic, nothing more.

One residual note on the fix: `countActivePromoCodeUsage()` is a plain `COUNT` with no row
lock or unique constraint, so a very narrow window may remain under truly simultaneous
reservations. **We did not test that**, and this scenario cannot exercise it — the burst never
puts two reservations inside the same window, and the per-event lock serialises them anyway.
What this demo shows is that the reported stale-counter bug is fixed; it says nothing about
whether that residual window exists.
