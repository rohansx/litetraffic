import http from "k6/http";
import * as lt from "./litetraffic/runtime.js";

export const options = lt.options();

export default function checkout() {
  const key = lt.journeyKey();
  const headers = {
    "Content-Type": "application/json",
    "Idempotency-Key": key,
    "X-LiteTraffic-Run": __ENV.LT_RUN_ID,
  };
  const created = http.post(
    `${__ENV.LT_TARGET}/payments`,
    JSON.stringify({ logical_key: key, total: 1250 }),
    { headers, tags: { operation: "create_payment" } },
  );
  const retried = http.post(
    `${__ENV.LT_TARGET}/payments`,
    JSON.stringify({ logical_key: key, total: 1250 }),
    { headers, tags: { operation: "retry_payment" } },
  );
  const observed = http.get(`${__ENV.LT_TARGET}/payments/${encodeURIComponent(key)}`, {
    headers,
    tags: { operation: "observe_payment" },
  });
  let body = {};
  try {
    body = observed.json();
  } catch (_) {
    // The assertion events below preserve the failed observation.
  }
  lt.evidence(
    "accepted_orders_persist",
    [200, 201].includes(created.status) && [200, 201].includes(retried.status) && observed.status === 200,
  );
  lt.evidence("one_effect_per_payment", body.effects === 1);
  lt.evidence("order_totals_match", body.total === 1250);
}
