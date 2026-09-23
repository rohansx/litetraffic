import http from "k6/http";
import * as lt from "./litetraffic/runtime.js";

export const options = lt.options();

export default function searchUpdateSearch() {
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
  lt.evidence(
    "search_reads_valid",
    hotReads.every((response) => response.status === 200) &&
      hotPrices.every((price) => price === 100 || price === 120) &&
      cold.status === 200 && coldPrice === 10,
    {
      expected: { hot_status: 200, hot_price: "100 or 120", cold_status: 200, cold_price: 10 },
      actual: {
        hot_status: hotReads.map((response) => response.status),
        hot_price: hotPrices,
        cold_status: cold.status,
        cold_price: coldPrice,
      },
    },
  );
  lt.evidence("cache_update_visible", update.status === 200 && after.status === 200 && afterPrice === 120, {
    expected: { update_status: 200, after_status: 200, price: 120 },
    actual: { update_status: update.status, after_status: after.status, price: afterPrice },
  });
}
