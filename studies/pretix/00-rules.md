# pretix: rules written before any run

Written 2026-10-21T04:47Z, before starting pretix and before reading its issue
tracker or security advisories. These come from what a ticket shop must
guarantee, not from known bugs. They are fixed now so the results can't be
bent to fit afterwards.

| # | Rule, in plain words | How LiteTraffic checks it |
|---|---|---|
| R1 | A quota of N tickets never sells more than N. | Many buyers order at once; afterwards ask pretix how many orders hold that quota. |
| R2 | A voucher allowed 1 use is used at most once. | Many buyers redeem the same voucher at once; afterwards ask pretix the voucher's redeemed count and count the orders that carry it. |
| R3 | What the buyer is charged matches the price minus the voucher. | Compare each accepted order's total with the expected price. |
| R4 | Refusals are clean: a buyer who doesn't get a ticket gets a clear "sold out" or "voucher used" error, not a server crash (5xx). | Count response codes across the burst. |

What would count as a finding: any rule broken in a way we can reproduce, on
the current stable pretix image, through an API pretix offers for this job.

What would not count: overload on a laptop, rate limiting, or anything that
needs admin settings no real shop would use.
