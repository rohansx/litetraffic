# Kill Bill: rules written before any run

Written 2026-10-21T16:16:10Z before starting Kill Bill and before reading its issue tracker or
advisories. App picked because it is a billing and payments engine: money must balance, and it
stands up from two public Docker images. Rules come from what a billing system must guarantee.

| # | Rule, in plain words | How LiteTraffic checks it |
|---|---|---|
| K1 | Refunds on one payment never add up to more than was paid, however many refund requests arrive at once. | A payment of 100.00 is captured; many clients each ask to refund the full 100.00 at once; afterwards read the payment's transactions and sum the refunds that succeeded. |
| K2 | A payment is only ever made once for the same transaction key: sending the same payment request (same transaction external key) many times at once creates one payment transaction. | Many clients send the identical payment at once; afterwards count transactions on the payment. |
| K3 | One tenant's data is private from another tenant: an account created under tenant A is not readable with tenant B's API key. | Tenant B's key requests tenant A's account id and must be refused. |
| K4 | Refusals are clean: a request that can't be honoured gets a 4xx with an error body, not a 5xx. | Count response codes across the burst. |
| K5 | The amount recorded for a payment or refund equals what the request asked for. | Compare each transaction amount with the request. |

Finding = a rule broken in a reproducible way on the current stable release through the public REST API.
Not a finding: overload on a laptop, rate limiting, non-default settings, or by-design behaviour
(I will read the docs for what is by design before calling anything a bug).

## Addendum 2026-10-21T16:26Z (after hand-probing the API once, before any LiteTraffic scenario ran, issue tracker still unread)

The hand probes (raw/03 to raw/08) showed that with the built-in external payment plugin a refund larger than the payment is accepted even one at a time (raw/03, raw/07), and that the test plugin cannot refund (NullPointerException in the plugin). So K1 is probably not a race question at all; I keep it and run it, with a sequential control, and will report what it shows. Two more rules, about money the core itself should police:

| # | Rule, in plain words | How LiteTraffic checks it |
|---|---|---|
| K6 | An invoice of 100.00 is paid at most once: many clients paying the same invoice at once leave the account balance at 0, never a credit (an overpayment). | Many clients post a 100.00 payment to the same invoice at once; afterwards read the account balance. |
| K7 | An invoice line of 100.00 can be adjusted away at most once: many concurrent full adjustments leave the account balance at 0, not negative. | Many clients post a full adjustment of the same line at once; afterwards read the account balance. |
