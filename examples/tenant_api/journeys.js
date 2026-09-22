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
      exec: "readOwnAndProbeOther",
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

function headers(tenant) {
  return {
    "X-LiteTraffic-Run": __ENV.LT_RUN_ID,
    "X-LiteTraffic-Fixture": __ENV.LT_FIXTURE_ID,
    "X-Actor-Tenant": tenant,
  };
}

export function readOwnAndProbeOther() {
  const key = `${__ENV.LT_RUN_ID}-${exec.scenario.iterationInTest}`;
  const tenantA = http.get(`${__ENV.LT_TARGET}/tenants/a/records/1`, { headers: headers("a") });
  const tenantB = http.get(`${__ENV.LT_TARGET}/tenants/b/records/1`, { headers: headers("b") });
  const crossTenant = http.get(`${__ENV.LT_TARGET}/tenants/b/records/1`, { headers: headers("a") });
  let valueA = null;
  let valueB = null;
  try {
    valueA = tenantA.json().value;
    valueB = tenantB.json().value;
  } catch (_) {
    // Parse failures become positive-access assertion failures.
  }
  evidence("tenant_a_reads_own", tenantA.status === 200 && valueA === "alpha", key);
  evidence("tenant_b_reads_own", tenantB.status === 200 && valueB === "beta", key);
  evidence("cross_tenant_blocked", crossTenant.status === 403, key);
}
