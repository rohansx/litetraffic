import http from "k6/http";
import * as lt from "./litetraffic/runtime.js";

export const options = lt.options();

export default function reserve() {
  const journey = lt.journeyKey();
  const requests = Array.from({ length: 4 }, (_, index) => [
    "POST",
    `${__ENV.LT_TARGET}/inventory/reservations`,
    JSON.stringify({ logical_key: `${journey}-${index}`, quantity: 1 }),
    {
      headers: { "Content-Type": "application/json", "X-LiteTraffic-Run": __ENV.LT_RUN_ID },
      tags: { operation: "reserve_inventory" },
    },
  ]);
  const responses = http.batch(requests);
  const observed = http.get(`${__ENV.LT_TARGET}/inventory/state`, {
    headers: { "X-LiteTraffic-Run": __ENV.LT_RUN_ID },
    tags: { operation: "observe_inventory" },
  });
  let state = {};
  let outcomes = [];
  try {
    state = observed.json();
    outcomes = responses.map((response) => response.json());
  } catch (_) {
    // Failed parsing is captured by reservation_responses_valid.
  }
  lt.evidence("inventory_never_negative", observed.status === 200 && state.remaining >= 0);
  lt.evidence(
    "accepted_reservations_within_capacity",
    observed.status === 200 && state.accepted_count <= state.capacity,
  );
  lt.evidence(
    "rejections_require_empty_inventory",
    outcomes.length === 4 && outcomes.every((outcome) => outcome.accepted || outcome.remaining === 0),
  );
  lt.evidence(
    "reservation_responses_valid",
    outcomes.length === 4 && responses.every((response) => [200, 201].includes(response.status)),
  );
}
