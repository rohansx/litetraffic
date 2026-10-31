import http from "k6/http";
import exec from "k6/execution";
import * as lt from "./litetraffic/runtime.js";

export const options = lt.options();

// One anonymous shopper buys one unit of the fixture variant through the Vendure shop API:
// add to cart, set customer, shipping address (US), shipping method, ArrangingPayment, pay.
// Per-journey evidence covers what one response can show:
//   clean_response       - every step answers HTTP 200 with either an order or a clear refusal
//                          (errorCode + message); never a server error / GraphQL error.
//   charge_matches_rules - every order total seen after shipping is set (and every payment
//                          amount) equals the expected total.
// Overselling (rule 1) is judged afterwards from the shop's own records (see manifest observations).

const ORDER_FIELDS = "id code state totalWithTax shippingWithTax totalQuantity payments { amount state }";
const RESULT = `__typename ... on Order { ${ORDER_FIELDS} } ... on ErrorResult { errorCode message }`;

const Q_ADD = `mutation($id: ID!) { addItemToOrder(productVariantId: $id, quantity: 1) { ${RESULT} } }`;
const Q_CUSTOMER = `mutation($input: CreateCustomerInput!) { setCustomerForOrder(input: $input) { ${RESULT} } }`;
const Q_ADDRESS = `mutation($input: CreateAddressInput!) { setOrderShippingAddress(input: $input) { ${RESULT} } }`;
const Q_SHIPPING = `mutation($ids: [ID!]!) { setOrderShippingMethod(shippingMethodId: $ids) { ${RESULT} } }`;
const Q_TRANSITION = `mutation($state: String!) { transitionOrderToState(state: $state) { ${RESULT} } }`;
const Q_PAY = `mutation($input: PaymentInput!) { addPaymentToOrder(input: $input) { ${RESULT} } }`;

function tokenFrom(res) {
  for (const k of Object.keys(res.headers)) {
    if (k.toLowerCase() === "vendure-auth-token") return res.headers[k];
  }
  return null;
}

function step(state, op, field, query, variables) {
  const headers = {
    "Content-Type": "application/json",
    Accept: "application/json",
    "X-LiteTraffic-Run": __ENV.LT_RUN_ID,
  };
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  const res = http.post(
    `${__ENV.LT_TARGET}/shop-api`,
    JSON.stringify({ query, variables }),
    { headers, jar: state.jar, tags: { operation: op } },
  );
  const t = tokenFrom(res);
  if (t) state.token = t;

  let body = null;
  try {
    body = res.json();
  } catch (_) {}
  const result = body && body.data ? body.data[field] : null;

  let problem = null;
  if (res.status !== 200) problem = `HTTP ${res.status}`;
  else if (!body) problem = "unparseable body";
  else if (body.errors) {
    const first = Array.isArray(body.errors) && body.errors[0] ? body.errors[0].message : "errors";
    problem = `graphql error: ${String(first).slice(0, 100)}`;
  } else if (!result || typeof result.__typename !== "string") problem = "missing result";
  else if (result.__typename !== "Order" && (!result.errorCode || !result.message)) {
    problem = `refusal without errorCode/message (${result.__typename})`;
  }
  if (problem) state.problems.push(`${op}: ${problem}`);
  return result && !problem ? result : null;
}

export default function checkout() {
  const fx = JSON.parse(__ENV.LT_FIXTURE_JSON);
  const n = exec.scenario.iterationInTest;
  const run = String(__ENV.LT_RUN_ID || "run").toLowerCase().replace(/[^a-z0-9]/g, "");
  const state = { token: null, jar: new http.CookieJar(), problems: [] };
  const chargeProblems = [];
  const reached = [];
  let paid = false;

  const steps = [
    { op: "add_item", field: "addItemToOrder", query: Q_ADD, vars: { id: fx.variant_id }, stage: "in_cart" },
    {
      op: "set_customer",
      field: "setCustomerForOrder",
      query: Q_CUSTOMER,
      vars: { input: { firstName: "LT", lastName: `Shopper${n}`, emailAddress: `lt-${run}-${n}@example.com` } },
      stage: "customer_set",
    },
    {
      op: "set_address",
      field: "setOrderShippingAddress",
      query: Q_ADDRESS,
      vars: {
        input: {
          fullName: `LT Shopper ${n}`,
          streetLine1: "1 Test Street",
          city: "Austin",
          province: "TX",
          postalCode: "78701",
          countryCode: "US",
        },
      },
      stage: "address_set",
    },
    {
      op: "set_shipping",
      field: "setOrderShippingMethod",
      query: Q_SHIPPING,
      vars: { ids: ["1"] },
      stage: "shipping_set",
      checkTotal: true,
    },
    {
      op: "to_arranging_payment",
      field: "transitionOrderToState",
      query: Q_TRANSITION,
      vars: { state: "ArrangingPayment" },
      stage: "arranging_payment",
      checkTotal: true,
    },
    {
      op: "pay",
      field: "addPaymentToOrder",
      query: Q_PAY,
      vars: { input: { method: "standard-payment", metadata: {} } },
      stage: "paid",
      checkTotal: true,
      isPayment: true,
    },
  ];

  for (const s of steps) {
    const result = step(state, s.op, s.field, s.query, s.vars);
    if (!result || result.__typename !== "Order") break; // refused (or broken): the journey stops here

    if (s.checkTotal && result.totalWithTax !== fx.expected_total) {
      chargeProblems.push(`${s.op}: totalWithTax ${result.totalWithTax}`);
    }
    if (s.isPayment) {
      const settled = result.state === "PaymentSettled" || result.state === "PaymentAuthorized";
      if (settled) {
        paid = true;
        for (const p of result.payments || []) {
          if (p.amount !== fx.expected_total) chargeProblems.push(`payment amount ${p.amount}`);
        }
        lt.stage("paid");
        reached.push("paid");
      }
      break;
    }
    lt.stage(s.stage);
    reached.push(s.stage);
  }

  // R3: a shopper who cannot buy gets a clear refusal, never a server error.
  lt.evidence("clean_response", state.problems.length === 0, {
    expected: "HTTP 200 with an order or a refusal that has errorCode and message",
    actual: state.problems.length ? state.problems.slice(0, 3) : { reached },
  });
  // R2: whatever the shopper is charged equals price with tax plus standard shipping.
  lt.evidence("charge_matches_rules", chargeProblems.length === 0, {
    expected: { totalWithTax: fx.expected_total },
    actual: chargeProblems.length ? chargeProblems.slice(0, 3) : { paid, reached },
  });
}
