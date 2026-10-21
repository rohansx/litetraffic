import http from "k6/http";
import { sleep } from "k6";
import exec from "k6/execution";
import * as lt from "./litetraffic/runtime.js";

export const options = lt.options();

// One shopper buys 1 unit of a variant that has only a few units, through Vendure's public shop API.
// Everyone fills their cart first and holds it 2 s (real shoppers do), THEN pays:
// that is the window where overselling would show up.
export default function buy() {
  const fx = JSON.parse(__ENV.LT_FIXTURE_JSON);
  const n = exec.scenario.iterationInTest;
  let token = null;
  let sawServerFault = false;
  const call = (query, op) => {
    const headers = { "Content-Type": "application/json", "X-LiteTraffic-Run": __ENV.LT_RUN_ID };
    if (token) headers.Authorization = `Bearer ${token}`;
    const res = http.post(`${__ENV.LT_TARGET}/shop-api`, JSON.stringify({ query }), { headers, tags: { operation: op } });
    if (!token && res.headers["Vendure-Auth-Token"]) token = res.headers["Vendure-Auth-Token"];
    let body = null;
    try { body = res.json(); } catch (_) {}
    if (res.status >= 500 || !body || body.errors) sawServerFault = true;
    return { status: res.status, data: body && body.data ? body.data : null };
  };
  const done = (outcome, extra) => {
    lt.evidence("clean_response", !sawServerFault, { expected: "HTTP 200 and no GraphQL errors", actual: { outcome } });
    return extra;
  };

  // 1) add to cart. A full shelf is a clean refusal (InsufficientStockError).
  const add = call(`mutation{ addItemToOrder(productVariantId:"${fx.variant_id}", quantity:1){ __typename ... on Order{ code } ... on ErrorResult{ errorCode message } } }`, "add_item");
  const added = add.data && add.data.addItemToOrder;
  if (!added || added.__typename !== "Order") { lt.evidence("charge_matches_rules", true, { actual: "no order" }); return done("refused_at_cart:" + (added && added.errorCode)); }
  lt.stage("in_cart");

  call(`mutation{ setCustomerForOrder(input:{emailAddress:"lt${n}@example.com", firstName:"L", lastName:"T"}){ ... on Order{ id } } }`, "set_customer");
  call(`mutation{ setOrderShippingAddress(input:{fullName:"L T", streetLine1:"1 Test St", city:"Town", postalCode:"12345", countryCode:"US"}){ ... on Order{ id } } }`, "set_address");
  call(`mutation{ setOrderShippingMethod(shippingMethodId:["1"]){ ... on Order{ id } } }`, "set_shipping");

  if (fx.hold_seconds > 0) sleep(fx.hold_seconds); // everyone is holding a cart before anyone pays

  const t = call(`mutation{ transitionOrderToState(state:"ArrangingPayment"){ __typename ... on Order{ state } ... on ErrorResult{ errorCode message } } }`, "to_payment");
  const tr = t.data && t.data.transitionOrderToState;
  if (!tr || tr.__typename !== "Order") { lt.evidence("charge_matches_rules", true, { actual: "no payment step" }); return done("refused_at_payment_step:" + (tr && tr.errorCode)); }

  const pay = call(`mutation{ addPaymentToOrder(input:{method:"standard-payment", metadata:{}}){ __typename ... on Order{ state totalWithTax } ... on ErrorResult{ errorCode message } } }`, "pay");
  const paid = pay.data && pay.data.addPaymentToOrder;
  if (paid && paid.__typename === "Order") {
    lt.stage("paid");
    lt.evidence("charge_matches_rules", paid.totalWithTax === fx.expected_total, { expected: { total: fx.expected_total }, actual: { total: paid.totalWithTax, state: paid.state } });
    return done("paid");
  }
  lt.evidence("charge_matches_rules", true, { actual: "payment refused" });
  return done("refused_at_payment:" + (paid && paid.errorCode));
}
