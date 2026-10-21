# Vendure study: step-by-step log

Every step says what was done, why, and how Vendure reacted. Raw output is in `raw/`.
Rules were written first, at 05:41Z, in `00-rules.md`, before Vendure was started.

## Step 1: get the current release
`@vendure/create` latest = 3.8.0 (`raw/01-npm-version.txt`). Scaffolded with `--ci --db postgres`: it installs the project, starts Postgres in Docker (port 6543) and loads sample data (54 products). Log: `raw/02-create.log`.

## Step 2: start the server
`npm run dev:server` on localhost:3000, Postgres underneath. Vendure turns anonymous telemetry on by default, so I restarted it with `VENDURE_DISABLE_TELEMETRY=true` to keep the study fully local. Its own startup log warns that dev CORS and CSRF settings are open ("do not deploy with this"); that is the scaffold's dev setting, not a finding. Log: `raw/03-server.log`.

## Step 3: look around, then do one purchase by hand (`probe.mjs`, output `raw/05-one-purchase.txt`)
Vendure's shop API is GraphQL. A purchase is 7 calls: add item to cart, set customer, set address, list and pick shipping, move to "ArrangingPayment", pay. Result for one laptop: charged 156,380 = 155,880 + 500 shipping (correct); the stock record then showed `stockOnHand` 100, `stockAllocated` 1.
Key thing learned: Vendure **checks** stock when the shopper moves to payment, but **allocates** stock only when payment is authorized. Two different moments. That gap is where a race can hide.
Also learned: LiteTraffic's final check only does HTTP GET, and Vendure's GraphQL also answers GET (`raw/06-get-probe.txt`), so the final check could read Vendure's records.

## Step 4: wrote the scenario (a person did this, LiteTraffic did not)
`scenarios/stock/`: setup creates a brand-new product with exactly 5 units; 20 shoppers arrive within one second; each runs the full 7-call checkout, holding the cart for 2 seconds before paying (real shoppers do, and this makes sure everyone is in the cart before anyone pays). Final checks read Vendure's records. `inspect` validated it (`raw/07-inspect-stock.txt`).

## Step 5: first run, FAIL, but my own rule was wrong (kept for honesty)
I had also added a privacy check ("a shopper must not read another shopper's order by its code"). It FAILED 20/20. Reading Vendure's code (`DefaultOrderByCodeAccessStrategy`): guest orders are readable by anyone who has the order code for 2 hours, by design, because the code works like a secret link. I had handed the shoppers the victim's code, which a stranger wouldn't have. So that FAIL was **my rule being wrong, not a Vendure bug**. LiteTraffic faithfully reported my rule; deciding whether the rule is right is a human job. Removed that check. Rule V5 (privacy) is therefore NOT TESTED (testing it properly needs registered customers, whose sign-up needs email verification). Run kept in `runs-first-attempt-bad-privacy-rule/`.

## Step 6: the stock check passed while 8 shoppers had paid for 5 units
In that first run the stock check (read `stockAllocated`) said 5 of 5, pass. But Vendure's records showed 8 paid orders for the variant (`raw/09-oversell-check.txt`) and the server log said "Stock shortfall on Order ...: requested 1, allocated 0" (3 times). So the check I had chosen read a ledger that stays tidy and missed what customers experienced. Lesson: choose the check that matches what a customer sees (were they charged for a unit that doesn't exist), not the internal ledger.

## Step 7: is it known? (read-only research, nothing filed)
Yes. Vendure issue #3508 "Concurrent Checkout Causes Stock Oversell" (opened 2025, closed 2026-08-19 via PR #4930, `raw/11-known-issues-text.txt`, `raw/12-pr4930.txt`), and the open design issue #4152 "Order Concurrency Improvements" lists "stock overselling, payment flow" as a known race. PR #4930 (in 3.8.0) made the stock **ledger** safe (locked writes, never negative) but on purpose still accepts the extra paid orders: "allocation is capped... not hard-failed... plugins react to the event (refund, backorder, notify)". So Vendure knows, has half-fixed it and left the business policy to plugins. **This is not a new vulnerability.** What it means for a shop: a freshly scaffolded Vendure (no handler plugin) charges customers for stock that doesn't exist when many buy the last units together.

## Step 8: a better check, run again: FAIL for the right reason (`raw/13-verify-stock-v2.txt`)
Added an independent check that asks Vendure how many paid orders exist at this run's unique price (`run.sh` picks the price; the final-check URL can't yet depend on the run's own fixture, a product gap, see below). Result: ledger consistent (pass), paid orders within stock (FAIL: 16 paid for 5 units).

## Step 9: control and repeats (`raw/14-*`, `raw/15-repeats.txt`)
- Control, same shoppers one at a time: PASS, exactly 5 sold, rest refused at the cart. So overlap is the trigger, not my setup.
- 20 shoppers at once, 8 runs in all: paid orders 8, 16, 12, 5, 6, 6, 6 (and 6 with no cart-holding). 7 of 8 oversold, 1 sold exactly 5. It is a timing race: it varies run to run.
- In every run, the number of paid shoppers counted by LiteTraffic equalled Vendure's own paid-order count, and the server's "stock shortfall" warnings matched the excess.

## Product gaps found by doing this (for the roadmap)
1. A final check can't refer to the run's own fixture (e.g. "the variant created in setup"); I had to generate the URL in a wrapper script.
2. Stage counts (8 reached "paid") are recorded but can't be asserted on ("paid must be <= stock").
3. The final check is GET only; GraphQL APIs that need POST can't be checked.
4. No server-side capture for apps running on the host (only `docker:` containers); I read Vendure's log file by hand.
5. Writing the scenario took a person: reading the docs, probing one purchase, three failed attempts (a quoting mistake, a syntax slip, a wrong rule). LiteTraffic itself caught each as ERROR or a clear FAIL.

## Not tested
V2 coupon limits (no way to count coupon uses across shoppers with the checks available), V5 privacy between registered customers.

## Step 10: can Claude draft the scenario itself? (`draft/`, raw `raw/16-19`)
Setup: `draft/draft.py` gives Claude (model `claude-sonnet-5-5`) only public material: Vendure's published GraphQL schema (downloaded by introspection, 84 KB), LiteTraffic's scenario docs, one working scenario for a different app (pretix) as a format example, and the shop owner's three rules in plain words. It must write `manifest.json` and `journeys.js`. LiteTraffic's `inspect` validates each draft; any error goes back to Claude to fix (max 3 repairs). Everything Claude was sent and answered is saved in `draft/attempt-*`. The fixture script (`setup.mjs`) was mine, supplied as a described contract.

**Round A, no hints.**
- Attempt 1 was cut off (ran out of output space); attempt 2 passed `inspect`. Tokens in total: 112,842 in, 18,705 out.
- Claude worked out the whole 7-step checkout from the schema correctly. But for rule 1 it chose to check the stock ledger (`stockAllocated <= 5`), the same trap I fell into.
- Run 3 times against Vendure: **PASS, PASS, PASS**, while 7 of 20 shoppers paid for 5 units each time. A scenario drafted by AI alone would have said the shop is fine. (`raw/17-claude-draft-runs.txt`)

**Round B, with one sentence of human insight** ("judge by what customers experienced; count paid orders").
- Valid on attempt 2 (115,404 in, 16,671 out). Claude added the paid-order check correctly and kept the ledger check.
- Run 3 times: **FAIL x3**, ledger check pass, paid-orders check fail. (`raw/19-claude-hinted-runs.txt`)
- Honest reading: AI does the plumbing well; the insight about what to check has to come from a person who understands the business. The tool should make that review step easy.

## Corrections made while doing this (kept for honesty)
- The runner picked prices that repeated for runs started within 5 seconds, so two runs' orders were counted together once (a reading of 14 instead of 7, seed 42). Fixed and re-run: 7. Earlier numbers were unaffected: in each of those runs the paid count equalled the shoppers LiteTraffic saw pay.
- My placeholder for the per-run check path was not valid for `inspect` as first written; Claude's repair was reasonable, and I changed the runner to accept it.

## What was NOT done
- No browser screenshots of the dashboard (the Chrome extension was not connected). The dashboard runs: `litetraffic dashboard --runs-dir studies/_all-runs`.
- Breakscale links are generated (`../breakscale/share-links.json`) but not opened; I did not verify the site opens them.
- E2B was not needed: both apps ran fine in local Docker.
