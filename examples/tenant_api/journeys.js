import http from "k6/http";
import * as lt from "./litetraffic/runtime.js";

export const options = lt.options();

function headers(tenant) {
  return {
    "X-LiteTraffic-Run": __ENV.LT_RUN_ID,
    "X-LiteTraffic-Fixture": __ENV.LT_FIXTURE_ID,
    "X-Actor-Tenant": tenant,
  };
}

export default function readOwnAndProbeOther() {
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
  lt.evidence("tenant_a_reads_own", tenantA.status === 200 && valueA === "alpha");
  lt.evidence("tenant_b_reads_own", tenantB.status === 200 && valueB === "beta");
  lt.evidence("cross_tenant_blocked", crossTenant.status === 403);
}
