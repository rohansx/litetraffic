import exec from "k6/execution";
import http from "k6/http";

const phases = JSON.parse(__ENV.LT_SCHEDULE_JSON);
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
      exec: "searchUpdateSearch",
      startRate: phases[0].rate,
      timeUnit: "1s",
      stages,
      preAllocatedVUs: Number(__ENV.LT_MAX_IN_FLIGHT),
      maxVUs: Number(__ENV.LT_MAX_IN_FLIGHT),
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

export function searchUpdateSearch() {
  const key = `${__ENV.LT_RUN_ID}-${exec.scenario.iterationInTest}`;
  const headers = {
    "Content-Type": "application/json",
    "X-LiteTraffic-Run": __ENV.LT_RUN_ID,
    "X-LiteTraffic-Fixture": __ENV.LT_FIXTURE_ID,
  };
  const hotReads = Array.from({ length: 4 }, () => http.get(`${__ENV.LT_TARGET}/search?q=hot`, { headers }));
  const cold = http.get(`${__ENV.LT_TARGET}/search?q=cold`, { headers });
  const update = http.post(`${__ENV.LT_TARGET}/products/hot/price`, JSON.stringify({ price: 120 }), { headers });
  const after = http.get(`${__ENV.LT_TARGET}/search?q=hot`, { headers });
  let hotPrices = [];
  let coldPrice = null;
  let afterPrice = null;
  try {
    hotPrices = hotReads.map((response) => response.json().price);
    coldPrice = cold.json().price;
    afterPrice = after.json().price;
  } catch (_) {
    // Parse failures become assertion failures.
  }
  evidence(
    "search_reads_valid",
    hotReads.every((response) => response.status === 200) &&
      hotPrices.every((price) => price === 100 || price === 120) &&
      cold.status === 200 && coldPrice === 10,
    key,
  );
  evidence("cache_update_visible", update.status === 200 && after.status === 200 && afterPrice === 120, key);
}
