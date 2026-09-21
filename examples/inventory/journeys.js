import exec from "k6/execution";
import http from "k6/http";

const phases = JSON.parse(__ENV.LT_SCHEDULE_JSON);
const maxVUs = Number(__ENV.LT_MAX_IN_FLIGHT);
const stages = [];
let rate = phases[0].rate;

for (const phase of phases) {
  if (phase.rate !== rate) {
    stages.push({ target: phase.rate, duration: "0s" });
    rate = phase.rate;
  }
  stages.push({ target: rate, duration: `${phase.seconds * 1000 + 1}ms` });
}

export const options = {
  scenarios: {
    traffic: {
      executor: "ramping-arrival-rate",
      exec: "reserve",
      startRate: phases[0].rate,
      timeUnit: "1s",
      stages,
      preAllocatedVUs: maxVUs,
      maxVUs,
    },
  },
  maxRedirects: 0,
};

function evidence(assertion, passed, logicalKey) {
  console.log(`LT_EVENT ${JSON.stringify({
    schema_version: 1,
    type: "assertion",
    run_id: __ENV.LT_RUN_ID,
    assertion,
    passed,
    logical_key: logicalKey,
  })}`);
}

export function reserve() {
  const journey = `${__ENV.LT_RUN_ID}-${exec.vu.idInTest}-${exec.scenario.iterationInTest}`;
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
  evidence("inventory_never_negative", observed.status === 200 && state.remaining >= 0, journey);
  evidence(
    "accepted_reservations_within_capacity",
    observed.status === 200 && state.accepted_count <= state.capacity,
    journey,
  );
  evidence(
    "rejections_require_empty_inventory",
    outcomes.length === 4 && outcomes.every((outcome) => outcome.accepted || outcome.remaining === 0),
    journey,
  );
  evidence(
    "reservation_responses_valid",
    outcomes.length === 4 && responses.every((response) => [200, 201].includes(response.status)),
    journey,
  );
}
