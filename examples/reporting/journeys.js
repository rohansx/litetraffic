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
      exec: "readReport",
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

export function readReport() {
  const journey = `${__ENV.LT_RUN_ID}-${exec.vu.idInTest}-${exec.scenario.iterationInTest}`;
  const response = http.get(`${__ENV.LT_TARGET}/reports/sales?window=current`, {
    headers: { "X-LiteTraffic-Run": __ENV.LT_RUN_ID },
    tags: { operation: "read_sales_report" },
  });
  let report = {};
  try {
    report = response.json();
  } catch (_) {
    // Failed parsing is captured by report_response_valid.
  }
  const regions = report.regions || {};
  evidence("report_total_matches_fixture", report.total === 1000, journey);
  evidence("report_row_count_complete", report.row_count === 5, journey);
  evidence(
    "report_region_breakdown_complete",
    regions.north === 700 && regions.south === 200 && regions.west === 100,
    journey,
  );
  evidence("report_response_valid", response.status === 200 && report.window === "current", journey);
}
