# Vendure: rules written before any run

Written 2026-10-21 before starting Vendure and before reading its issue tracker or
advisories. They come from what an online shop must guarantee, not from known bugs.

| # | Rule, in plain words | How LiteTraffic checks it |
|---|---|---|
| V1 | Stock never goes below zero: with N units, at most N units are sold, however many buyers check out at once. | Many buyers add the last units to their own carts and check out at once; afterwards read the variant's stock and the sold quantity. |
| V2 | A coupon limited to 1 use in total is used at most once. | Many buyers apply the same coupon and check out at once; afterwards count orders that carry it. |
| V3 | A buyer is charged the right total: price x quantity, minus the coupon. | Compare each completed order's total with the expected amount. |
| V4 | Refusals are clean: a buyer who can't buy gets a clear error, not a server crash (5xx). | Count response codes across the burst. |
| V5 | One shopper can never read another shopper's order. | Buyer A requests buyer B's order by code and must be refused. |

Finding = a rule broken in a reproducible way on the current stable release through the
public shop API (the one a storefront uses), not the admin API.
Not a finding: overload on a laptop, rate limiting, or non-default settings no real shop uses.
