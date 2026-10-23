import http from "k6/http";
import exec from "k6/execution";
import * as lt from "./litetraffic/runtime.js";

export const options = lt.options();

// One anonymous shopper buys one unit of the fixture variant through the Vendure shop API:
// addItemToOrder -> setCustomerForOrder -> setOrderShippingAddress (US) -> setOrderShippingMethod("1")
// -> transitionOrderToState(ArrangingPayment) -> addPaymentToOrder("standard-payment").
// Rule 1 (never oversold) is judged afterwards from the admin API (see manifest observation).
// Per-journey evidence covers Rule 2 (right total charged) and Rule 3 (a refusal is clear, never a server error).

const ORDER = "id code state totalQuantity totalWithTax payments { amount state }";
const ERR = "... on ErrorResult { errorCode message }";

const Q_ADD = `mutation($id: ID!) { addItemToOrder(productVariantId: $id, quantity: 1) { __typename ... on Order { ${ORDER} } ${ERR} } }`;
const Q_CUSTOMER = `mutation($input: CreateCustomerInput!) { setCustomerForOrder(input: $input) { __typename ... on Order { ${ORDER} } ${ERR} } }`;
const Q_ADDRESS = `mutation($input: CreateAddressInput!) { setOrderShippingAddress(input: $input) { __typename ... on Order { ${ORDER} } ${ERR} } }`;
const Q_SHIPPING = `mutation($ids: [ID!]!) { setOrderShippingMethod(shippingMethodId: $ids) { __typename ... on Order { ${ORDER} } ${ERR} } }`;
const Q_TRANSITION = `mutation($state: String!) { transitionOrderToState(state: $state) { __typename ... on Order { ${ORDER} } ${ERR} } }`;
const Q_PAY = `mutation($input: PaymentInput!) { addPaymentToOrder(input: $input) { __typename ... on Order { ${ORDER} } ${ERR} } }`;

function headerValue(res, name) {
  const want = name.toLowerCase();
  for (const k of Object.keys(res.headers || {})) {
    if (k.toLowerCase() === want) return res.headers[k];
  }
  return null;
}

export default function checkout() {
  const fx = JSON.parse(__ENV.LT_FIXTURE_JSON);
  const n = exec.scenario.iterationInTest;
  const url = `${__ENV.LT_TARGET}/shop-api`;

  const statuses = [];
  let clean = true;
  let problem = null;
  let token = null;
  let readyOrder = null; // order seen in ArrangingPayment
  let paidOrder = null; // order returned by a successful payment

  function call(op, query, variables, field) {
    const headers = {
      "Content-Type": "application/json",
      Accept: "application/json",
      "X-LiteTraffic-Run": __ENV.LT_RUN_ID,
    };
    if (token) headers.Authorization = `Bearer ${token}`;
    const res = http.post(url, JSON.stringify({ query, variables }), {
      headers,
      tags: { operation: op },
    });
    statuses.push({ op, status: res.status });
    let body = null;
    try {
      body = res.json();
    } catch (_) {}
    if (res.status !== 200 || !body || body.errors || !body.data) {
      clean = false;
      problem = problem || `${op}: status ${res.status}${body && body.errors ? " graphql errors" : ""}`;
      return { ok: false, order: null };
    }
    const result = body.data[field];
    if (!result) {
      clean = false;
      problem = problem || `${op}: empty result`;
      return { ok: false, order: null };
    }
    if (!token) {
      const t = headerValue(res, "vendure-auth-token");
      if (t) token = t;
    }
    if (result.__typename === "Order") return { ok: true, order: result };
    if (result.errorCode) return { ok: false, order: null, refusal: result.errorCode };
    clean = false;
    problem = problem || `${op}: unrecognised result ${result.__typename}`;
    return { ok: false, order: null };
  }

  function run() {
    let r = call("add_item", Q_ADD, { id: fx.variant_id }, "addItemToOrder");
    if (!r.ok) return;
    if (!token) {
      clean = false;
      problem = problem || "no session token returned";
      return;
    }
    lt.stage("carted");

    r = call(
      "set_customer",
      Q_CUSTOMER,
      { input: { firstName: "LT", lastName: `Shopper${n}`, emailAddress: `lt-${n}@example.com` } },
      "setCustomerForOrder",
    );
    if (!r.ok) return;

    r = call(
      "set_shipping_address",
      Q_ADDRESS,
      {
        input: {
          fullName: `LT Shopper ${n}`,
          streetLine1: "1 Test Street",
          city: "Austin",
          province: "TX",
          postalCode: "73301",
          countryCode: "US",
        },
      },
      "setOrderShippingAddress",
    );
    if (!r.ok) return;

    r = call("set_shipping_method", Q_SHIPPING, { ids: ["1"] }, "setOrderShippingMethod");
    if (!r.ok) return;

    r = call("arrange_payment", Q_TRANSITION, { state: "ArrangingPayment" }, "transitionOrderToState");
    if (!r.ok) return;
    readyOrder = r.order;
    lt.stage("payment_ready");

    r = call(
      "pay",
      Q_PAY,
      { input: { method: "standard-payment", metadata: { lt: `journey-${n}` } } },
      "addPaymentToOrder",
    );
    if (!r.ok) return;
    paidOrder = r.order;
    if (r.order.state === "PaymentSettled" || r.order.state === "PaymentAuthorized") {
      lt.stage("paid");
    }
  }

  run();

  // Rule 3: a shopper who cannot buy gets a clear refusal, never a server error.
  lt.evidence("clean_response", clean, {
    expected: "HTTP 200 with an Order or a typed error result at every step",
    actual: { statuses, problem },
  });

  // Rule 2: the shopper is charged the right total.
  let chargeOk = true;
  const seen = {};
  if (readyOrder) {
    seen.ready_total = readyOrder.totalWithTax;
    if (readyOrder.totalWithTax !== fx.expected_total) chargeOk = false;
  }
  if (paidOrder) {
    const settled = paidOrder.state === "PaymentSettled" || paidOrder.state === "PaymentAuthorized";
    seen.paid_total = paidOrder.totalWithTax;
    seen.state = paidOrder.state;
    if (paidOrder.totalWithTax !== fx.expected_total) chargeOk = false;
    if (settled) {
      const paidSum = (paidOrder.payments || []).reduce((s, p) => s + p.amount, 0);
      seen.payments_sum = paidSum;
      if (paidSum !== fx.expected_total) chargeOk = false;
    }
  }
  lt.evidence("charge_matches_rules", chargeOk, {
    expected: { totalWithTax: fx.expected_total },
    actual: seen,
  });
}
