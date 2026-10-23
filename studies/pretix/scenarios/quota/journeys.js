import http from "k6/http";
import exec from "k6/execution";
import * as lt from "./litetraffic/runtime.js";

export const options = lt.options();

// One buyer places one order for one ticket through pretix's orders API.
// voucher mode: every buyer tries the same single-use voucher.
// quota mode:   every buyer wants a ticket from a quota of 5.
// The rules are judged afterwards from pretix's own records (see manifest observations);
// the per-journey evidence only covers rules visible in one response (R3 price, R4 clean refusal).
export default function buy() {
  const fx = JSON.parse(__ENV.LT_FIXTURE_JSON);
  const n = exec.scenario.iterationInTest;
  const headers = {
    "Content-Type": "application/json",
    Accept: "application/json",
    Authorization: __ENV.LT_PRETIX_AUTH,
    "X-LiteTraffic-Run": __ENV.LT_RUN_ID,
  };
  const position = { item: fx.item, attendee_name: `LT buyer ${n}` };
  if (fx.voucher) position.voucher = fx.voucher;
  const res = http.post(
    `${__ENV.LT_TARGET}/api/v1/organizers/study/events/lab/orders/`,
    JSON.stringify({
      email: `lt${n}@example.com`,
      locale: "en",
      sales_channel: "web",
      testmode: true,
      payment_provider: "banktransfer",
      positions: [position],
    }),
    { headers, tags: { operation: "place_order" } },
  );
  let order = null;
  try {
    order = res.json();
  } catch (_) {}
  if (res.status === 201) lt.stage("ordered");

  // R4: a buyer who is refused gets a clear 4xx, never a server error.
  lt.evidence("clean_response", res.status === 201 || (res.status >= 400 && res.status < 500), {
    expected: "201, or a 4xx refusal",
    actual: { status: res.status },
  });
  // R3: an accepted order is charged what the rules say.
  lt.evidence("charge_matches_rules", res.status !== 201 || (order && order.total === fx.expected_total), {
    expected: { total: fx.expected_total },
    actual: { status: res.status, total: order ? order.total : null },
  });
}
