# pretix study: step-by-step log

Every step below says what was done, why, and what pretix did in response.
Raw command output lives in `raw/`, named by step number.

## Step 0, 04:47Z: rules written first
See `00-rules.md`. Nothing has been started yet.

## Step 1, 04:5xZ: start pretix 2026.8.0
Pulled `pretix/standalone:stable` (pinned by digest in docker-compose.yml, built 2026-09-30) with its own Postgres and Redis, bound to localhost:8345. pretix only accepts the hostname `localhost`; the same URL with `127.0.0.1` returns 400 (its allowed-hosts check). Raw: `raw/01-image.txt`, `raw/02-up.txt`.

## Step 2: admin team and API token
Created an organizer `study`, an admin team and an API token inside the container (`seed_admin.py`). My first attempt failed because this pretix version replaced the old permission flags with `all_event_permissions`; I read the model and fixed it. The token is stored in `.token` (git-ignored) and is not logged. Raw: `raw/03-seed.txt`.

## Step 3: fixtures through pretix's own API
Event `concert`, ticket (20.00 EUR), quota of 5, voucher `ONEUSE1` (max 1 use, price set to 10.00). pretix refused to create an event as "live" (it wants quota and payment first), so I made it a draft. Raw: `raw/04-fixtures.txt`.

## Step 4: one order by hand, to learn normal behaviour
- Order with the free payment provider: refused 400 ("not for non-free orders"). Correct.
- Payment provider `banktransfer` unknown until its plugin was enabled at **organizer level first**, then the event.
- Then one order with the voucher: 201, total 10.00 (R3 holds), voucher `redeemed` 1, quota available 5 -> 4.
- Oddity noted, not yet explained: quota availability reports `waiting_list: 1` although no waiting list entry was made.
Raw: `raw/05-single-order.txt`.

Lesson for the product report: before LiteTraffic can send a single useful request, a person had to learn pretix's setup order (organizer, plugin, event, quota, payment). That setup knowledge is the expensive part, not the load.

## Step 5: a clean event for repeatable runs
The hand-made event `concert` had a real (non-test) order, which pretix won't delete, so it can't be reset. I made event `lab` in test mode (`bootstrap.sh`) and placed every order there with `testmode: true`. Test-mode orders can be deleted, so each run starts from zero. Raw: `raw/08-bootstrap-lab.txt`, `raw/09-lab-reset-probe.txt`.

## Step 6: wrote the LiteTraffic scenarios (a person did this, LiteTraffic did not)
Two bundles in `scenarios/`: `voucher` (10 buyers, same 1-use voucher) and `quota` (20 buyers, quota of 5). Each has `manifest.json` (what to check), `journeys.js` (what a buyer does: one `POST /orders/`), `setup.sh` (reset the event and create the voucher or quota) and `teardown.sh`.
`litetraffic inspect` validated them and caught one mistake of mine (two checks with the same name). My first run then stopped at setup with ERROR and **zero requests sent** because of a quoting bug in my shell script; LiteTraffic reported it correctly as ERROR rather than a false PASS. Raw: `raw/11-inspect-*.txt`, `runs-failed-setup-bug/`.

## Step 7: first real run, voucher (R2), 10 buyers within 1 s: PASS
What LiteTraffic did, in order: ran setup (fresh voucher, max 1 use) -> k6 started 10 buyers within one second, each sending `POST /orders/` with that voucher -> after they finished, read pretix's own records (`GET /vouchers/`, `GET /orderpositions/`) -> compared with the rule.
How pretix reacted: 1 order accepted (201, charged 10.00 = voucher price), 9 refused (400, "The voucher has already been used the maximum number of times"). pretix's records agree: voucher redeemed 1, 1 order. pretix's container log shows the same nine refusals (captured by `--capture`). Raw: `raw/12-verify-voucher.txt`, `runs/`.

## Step 8: quota (R1), 20 buyers for 5 tickets: PASS
5 orders accepted (201, 20.00 each), 15 refused (400, "There is not enough quota available"). pretix's records: exactly 5 order positions. Raw: `raw/13-verify-quota.txt`.

## Step 9: harder and repeated
- Each scenario repeated 5 times with different seeds: all PASS, consistent. Raw: `raw/14-repeat5.txt`.
- 100 buyers at once, both scenarios: PASS. Response time at 100 buyers: p95 about 116 ms (quota) and 560 ms (voucher); no 5xx errors. Raw: `raw/15-*.txt`.

## Step 10: canary, to prove the check can fail
Same quota run but with a deliberately wrong limit (at most 4 sold). Result: FAIL, as it must. So a PASS is not just "LiteTraffic never fails". Raw: `raw/15-canary-quota.txt`.

## Honest limits of this study
- These calls used the **admin orders API with an API token**, not the public buyer storefront (cart, checkout pages). pretix's storefront is browser-session based; testing it is a follow-up and a likelier place for races.
- pretix runs here under CPU emulation (x86 image on an ARM Mac), so absolute timings are slow-ish. Correctness results are unaffected.
- No bug found. Rules R1 to R4 all held on pretix 2026.8.0 under bursts of up to 100 buyers.
