import http from "k6/http";
import { sleep } from "k6";
import exec from "k6/execution";
import * as lt from "./litetraffic/runtime.js";

export const options = lt.options();

// Each buyer journey reserves one ticket with the single-use 100%-off promo code,
// then completes the order. Every journey uses its OWN checkout session
// (session_identifier query param) so the server's deleteExistingOrders() cannot
// wipe a sibling's reservation. All reservations land before any completion
// (see the sleep below), so on the buggy build the stale usage counter is still
// zero when each order is reserved and every journey keeps the discount -> the
// promo is redeemed far more than its max of one. The fixed build counts live
// promo usage, so only the first reservation gets the discount and the rest fall
// back to full price (and cannot complete without payment).
export default function buy() {
  const key = lt.journeyKey();
  const fx = JSON.parse(__ENV.LT_FIXTURE_JSON);
  const orderUrl = `${__ENV.LT_TARGET}/api/public/events/${fx.event_id}/order`;
  // iteration-first so the unique part survives the 40-char session column cap.
  const session = `lt${exec.scenario.iterationInTest}${__ENV.LT_RUN_ID}`
    .replace(/[^A-Za-z0-9]/g, "")
    .slice(0, 40);
  const email = `${session}@lt.test`;
  const headers = { "Content-Type": "application/json", Accept: "application/json", "X-LiteTraffic-Run": __ENV.LT_RUN_ID };

  // 1) Reserve with the promo code.
  const reserve = http.post(
    `${orderUrl}?session_identifier=${session}`,
    JSON.stringify({
      products: [{ product_id: fx.product_id, quantities: [{ price_id: fx.price_id, quantity: 1 }] }],
      promo_code: fx.promo_code,
    }),
    { headers, tags: { operation: "reserve_order" } },
  );
  let ro = null;
  try {
    ro = reserve.json("data");
  } catch (_) {}
  const shortId = ro ? ro.short_id : null;
  const discountApplied = ro ? ro.promo_code !== null && ro.total_gross === 0 : false;
  if (reserve.status === 201 && shortId) lt.stage("reserved");

  lt.evidence("reservation_accepted", reserve.status === 201 && !!shortId, {
    expected: { status: 201, short_id: "present" },
    actual: { status: reserve.status, short_id: shortId },
  });
  // Internal consistency, true on BOTH builds: the promo is recorded on the order
  // exactly when the order is free. The per-journey evidence of whether THIS buyer
  // got the discount is in `actual` (discount_applied); the oracle for the limit
  // is the final observation.
  lt.evidence("order_total_matches_promo", ro ? (ro.promo_code !== null) === (ro.total_gross === 0) : false, {
    expected: "promo_code set on the order <=> total_gross is 0",
    actual: ro ? { promo_code: ro.promo_code, total_gross: ro.total_gross, discount_applied: discountApplied } : null,
    detail: `discount_applied=${discountApplied}`,
  });

  // Hold the reservation so every journey reserves before any journey completes.
  // The schedule releases the burst within ~1s; 2s guarantees the ordering.
  sleep(2);

  // 2) Complete. A 100%-off order needs no payment and becomes COMPLETED; a
  // full-price fallback (fixed build) stays RESERVED and never reaches 'completed'.
  if (shortId) {
    const buyer = { first_name: "LT", last_name: `b${exec.scenario.iterationInTest}`, email, email_confirmation: email };
    const complete = http.put(
      `${orderUrl}/${shortId}?session_identifier=${session}`,
      JSON.stringify({
        order: buyer,
        products: [{ product_id: fx.product_id, product_price_id: fx.price_id, ...buyer }],
      }),
      { headers, tags: { operation: "complete_order" } },
    );
    let co = null;
    try {
      co = complete.json("data");
    } catch (_) {}
    if (co && co.status === "COMPLETED") lt.stage("completed");
  }
}
