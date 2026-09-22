import http from "k6/http";
import * as lt from "./litetraffic/runtime.js";

export const options = lt.options();

export default function readReport() {
  const response = http.get(`${__ENV.LT_TARGET}/reports/sales?window=current`, {
    headers: { "X-LiteTraffic-Run": __ENV.LT_RUN_ID, "X-LiteTraffic-Fixture": __ENV.LT_FIXTURE_ID },
    tags: { operation: "read_sales_report" },
  });
  let report = {};
  try {
    report = response.json();
  } catch (_) {
    // Failed parsing is captured by report_response_valid.
  }
  const regions = report.regions || {};
  lt.evidence("report_total_matches_fixture", report.total === 1000);
  lt.evidence("report_row_count_complete", report.row_count === 5);
  lt.evidence(
    "report_region_breakdown_complete",
    regions.north === 700 && regions.south === 200 && regions.west === 100,
  );
  lt.evidence("report_response_valid", response.status === 200 && report.window === "current");
}
