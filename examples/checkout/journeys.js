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
  // k6 excludes an admission exactly on the final duration boundary. The
  // extra millisecond makes an integer rate × duration plan deliver its final
  // journey without materially changing the requested rate.
  stages.push({ target: rate, duration: `${phase.seconds * 1000 + 1}ms` });
}

export const options = {
  scenarios: {
    traffic: {
      executor: "ramping-arrival-rate",
      exec: "checkout",
      startRate: phases[0].rate,
      timeUnit: "1s",
      stages,
      preAllocatedVUs: maxVUs,
      maxVUs,
    },
  },
  noConnectionReuse: false,
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

export function checkout() {
  const key = `${__ENV.LT_RUN_ID}-${exec.vu.idInTest}-${exec.scenario.iterationInTest}`;
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
  evidence(
    "accepted_orders_persist",
    [200, 201].includes(created.status) && [200, 201].includes(retried.status) && observed.status === 200,
    key,
  );
  evidence("one_effect_per_payment", body.effects === 1, key);
  evidence("order_totals_match", body.total === 1250, key);
}
