# Kill Bill study: step-by-step log

Third app after pretix and Vendure. Areas not covered before: refunds, invoice payments and credits, and privacy between customers (tenants).
Rules were written first, at 16:16:10Z on 2026-10-21, in `00-rules.md`, before Kill Bill was started and before its issue tracker or advisories were read. Raw output is in `raw/`; every LiteTraffic run is in `runs/`.

## Step 0: why Kill Bill
Billing and payments engine (Java, REST API, MariaDB), 0.24.22 released 2026-09-29 (current stable). Stands up from two public Docker images, no build step. Others on the list (Saleor, Medusa, Sylius, Solidus, InvoiceNinja) need a build or a larger stack; I did not try them. Ports: 127.0.0.1:8480 only.

## Step 1: stand it up (`docker-compose.yml`, `raw/01-compose-up.log`)
`killbill/killbill:0.24.22` + `killbill/mariadb:0.24`. Healthy in about 15 s. The default admin login (admin / password) is the image's public default. I created two tenants (`lt-tenant-a`, `lt-tenant-b`) with throwaway local keys, each with its own data.
Small things: (a) Kill Bill checks GitHub for updates at start. My first env var (`KILLBILL_SERVER_UPDATECHECK_SKIP`) did nothing; the property name is case-sensitive and the image wants `KB_org_killbill_server_updateCheck_skip`, which is what the compose file now has. I did not verify the log afterwards, so treat "no phone-home" as unverified. (b) The only payment plugin built in is `__EXTERNAL_PAYMENT__` (records payments made elsewhere).

## Step 2: probe by hand (`raw/02` to `raw/08`, `probe*.sh`)
- Built-in external plugin: purchase of 100.00, then refunds of 60 and 60: both accepted, refunded 120.00 of 100.00, **one at a time**. One refund of 150: accepted (`raw/03`, `raw/07`).
- I installed the official `payment-test` plugin with Kill Bill's own installer (`raw/04`), loaded its tables (`ddl-payment-test.sql`, downloaded from the plugin's repo), and tried again: purchases work but every refund fails inside the plugin with a NullPointerException, transaction status UNKNOWN (`raw/05`, `raw/06`). I did not chase why; it is a test plugin. So I ran the money checks on the built-in external plugin.
- Invoice paid twice: the second payment answers 204 and does nothing; invoice adjusted twice: second refused 400 "should be lower than 0" (`raw/08`). The core does police these.
- Tenant B reading tenant A's account: **HTTP 500** (an `IllegalStateException`), not 403 or 404 (`raw/03`).
Because of the first bullet I added rules K6 and K7 to `00-rules.md` as a dated addendum (16:26Z), after probing but before any LiteTraffic run, and said so there. K1 stayed.

## Step 3: scenarios (a person wrote them; `src/`, generated into `scenarios/` by `make_scenarios.py`)
One `journeys.js` and one `setup.py` for all modes; 10 clients arrive within one second (the "sequential" controls: 10 clients one per second). Setup creates a fresh account per run; `run.sh` picks a unique key per run and wires it into the final check (same product gap as in the Vendure study: a final check cannot name the run's own fixture). `inspect` accepted all 13 bundles (`raw/09`).

| Scenario | Rule | What it does | Final check |
|---|---|---|---|
| refund, refund-seq | K1 | 10 clients each refund the full 100.00 | refunded <= 100 |
| dup, dup-seq | K2 | 10 clients send the identical payment (same keys) | purchased = 100, 1 transaction |
| privacy | K3, K4 | tenant B's key reads 6 endpoints and writes on tenant A's account | no marker of A in any answer, no 2xx on a write, no 5xx, and A's payment untouched |
| pay, pay-seq | K6 | 10 clients pay the same 100.00 invoice | account balance 0 |
| adjust, adjust-seq | K7 | 10 clients adjust the same 100.00 line away | account balance 0 |
| refund-canary, dup-canary, pay-canary, privacy-canary | | same runs with a deliberately wrong limit (privacy: the "foreign" client uses A's own key) | must FAIL |

## Step 4: results (`raw/10`, `raw/11-*`, `raw/19-tally.txt`)
5 seeds (100 to 104) per concurrent scenario, 1 run per control and canary. Totals in the final series: 5 x 5 concurrent + 4 controls (refund-seq, dup-seq, pay-seq, adjust-seq) + 4 canaries = 33 runs of 10 journeys each.

| Rule | Concurrent (5 seeds) | Sequential control | Canary |
|---|---|---|---|
| K1 refunds within paid | FAIL 5/5: all 10 refunds accepted, 1000.00 refunded on 100.00 paid | FAIL: same, 1000.00 | FAIL as designed |
| K2 one payment per key | PASS 5/5: exactly 1 of 10 accepted | PASS | FAIL as designed |
| K3 no tenant data leaked | PASS 5/5 (own read works, so the marker check can see data; canary sees it) | n/a | FAIL as designed |
| K4 clean refusals (cross-tenant) | FAIL 5/5: every cross-tenant read of an account, its payments, invoices, payment methods, and the write, answers 500 (search answers 200 with no rows, lookup by key 404) | n/a | n/a |
| foreign write not applied | PASS 5/5 | | |
| K6 invoice paid once | PASS 5/5: exactly 1 of 10 accepted | PASS | FAIL as designed |
| K7 line adjusted once | PASS 5/5: exactly 1 of 10 accepted | PASS | n/a (no canary written) |

Server side (`--capture`): 554 log lines "doesn't belong to tenant" across the whole session, matching the cross-tenant requests.

## Step 5: mistakes of my own, kept for honesty
1. **First repeat attempt crashed** (`runs-failed-repeat-reused-key/`): I used `--repeat 5`, which reuses the wrapper's per-run key, so setup tried to create an account with a key that already existed (4 of 5 runs ERROR in each of 5 scenarios). Fix: one wrapper call per seed.
2. **Privacy rule wrong a second time** (`runs-first-attempt-bad-search-rule/`): my probe called any 200 a leak, but a foreign **search** answering "200, no rows" is the correct answer. Failed 10/10 for my own reason. Fixed to judge search by the marker only, then reran. (Same kind of slip as Vendure's privacy rule.)
3. **K1 as a rule was wrong for this setup**, see next step: Kill Bill does not check refund amounts at all; the payment gateway does. With only the no-op external plugin there is no gateway.
4. No pytest tests were written: LiteTraffic itself was not changed in this study. The scenarios' controls and canaries were the "tests that must fail first".

## Step 6: only now, the issue tracker and advisories (read-only, nothing filed; `raw/13` to `raw/18`)
- **K1 refunds beyond paid: known and by design.** Issues #333 and #334 (2015, closed): a maintainer writes "I don't believe KB will check amounts, but the gateway/payment processor should", another: "not enforced in the sandbox environment". Kill Bill core accepts amounts and leaves validation to the gateway plugin. Not a finding. What it means for an operator: with the external plugin (or any plugin that does not itself check) Kill Bill will record a refund of 10x the payment as a successful refund.
- **K2, K6, K7: held.** Not searched further because nothing failed.
- **K3: no leak found.**
- **K4, cross-tenant requests answer 500 instead of 403/404:** I found no matching issue (searches: "belong to tenant", "wrong tenant", "cross tenant", "multi-tenant", "tenant 500"; closest are #340 "API: should only return authentication error unless valid key/secret provided", open since 2015, and an open 2026 security issue #2251 about admin endpoints, different thing). GitHub security advisories for the repo: none listed (`raw/17`). My searches are shallow (titles via `gh search`), so "not found" is not "new". It is also minor: no data leaked, and the 500 body only echoes the requested id and the caller's own tenant id.
- Side effect worth a line: a foreign tenant gets **500 for an account id that exists in another tenant and 404 for one that exists nowhere** (`raw/12-existence-oracle.txt`), so tenant B can tell whether a guessed account id exists in some other tenant. Ids are random UUIDs, so exploiting it is not practical. Reproduced 5 runs x 10 journeys plus by hand, more than the 3 required. It is **not reported or filed anywhere**.

## Product gaps found by doing this (for the roadmap)
1. Same as Vendure: a final check cannot refer to the run's own fixture; I needed a wrapper script.
2. `--repeat` reuses everything the wrapper picked once; a repeat-safe per-run key (for example a `{run_id}` placeholder in observation paths) would remove the wrapper.
3. Cross-tenant "the other tenant's key" is a natural second actor class; `init tenant-isolation` needs a token or header per identity, but Kill Bill needs three headers (basic auth, key, secret) and a different status class (500) than the kit expects, so I wrote the privacy scenario by hand.
4. Stage counts (accepted = 1 of 10) can be seen in the result but not asserted on.

## Tear down
`docker compose down -v`: containers, network and plugin volume removed (`raw/20-teardown.txt`); other containers on the machine untouched. Nothing pushed, filed or sent.
