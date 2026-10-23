import http from "k6/http";
import exec from "k6/execution";
import * as lt from "./litetraffic/runtime.js";

export const options = lt.options();

// Kill Bill study. One script, several modes; setup.py prints the mode and the run's fixture ids.
//   refund   every client asks to refund the full payment            (rule K1)
//   dup      every client sends the identical payment                (rule K2)
//   privacy  tenant B's key reads/writes tenant A's data             (rule K3, K4)
//   privacy-canary  same but with tenant A's own key: MUST fail      (proves the check can fail)
//   pay      every client pays the same 100.00 invoice               (rule K6)
//   adjust   every client adjusts the same 100.00 line away          (rule K7)
// The money rules are judged afterwards from Kill Bill's own records (manifest observations).
const T = __ENV.LT_TARGET + "/1.0/kb";

function hdr(key, secret, extra) {
  return Object.assign(
    {
      "Content-Type": "application/json",
      Accept: "application/json",
      Authorization: __ENV.LT_KB_AUTH,
      "X-Killbill-ApiKey": key,
      "X-Killbill-ApiSecret": secret,
      "X-Killbill-CreatedBy": "litetraffic",
      "X-LiteTraffic-Run": __ENV.LT_RUN_ID,
    },
    extra || {},
  );
}

export default function journey() {
  const fx = JSON.parse(__ENV.LT_FIXTURE_JSON);
  const n = exec.scenario.iterationInTest;
  const A = hdr(__ENV.LT_KB_KEY, __ENV.LT_KB_SECRET);

  if (fx.mode === "refund") {
    const res = http.post(
      `${T}/payments/${fx.payment}/refunds`,
      JSON.stringify({ amount: 100, transactionExternalKey: `${fx.key}-r-${n}` }),
      { headers: A, tags: { operation: "refund" } },
    );
    if (res.status === 201) lt.stage("refund_accepted");
    lt.evidence("clean_response", res.status < 500, { expected: "no 5xx", actual: { status: res.status } });
  } else if (fx.mode === "dup") {
    const res = http.post(
      `${T}/accounts/${fx.account}/payments`,
      JSON.stringify({
        transactionType: "PURCHASE",
        amount: 100,
        currency: "USD",
        paymentExternalKey: fx.key,
        transactionExternalKey: `${fx.key}-t`,
      }),
      { headers: A, tags: { operation: "purchase" } },
    );
    if (res.status === 201) lt.stage("payment_accepted");
    lt.evidence("clean_response", res.status < 500, { expected: "no 5xx", actual: { status: res.status } });
  } else if (fx.mode === "pay") {
    const res = http.post(
      `${T}/invoices/${fx.invoice}/payments?externalPayment=false`,
      JSON.stringify({ accountId: fx.account, purchasedAmount: 100, paymentMethodId: fx.pm }),
      { headers: A, tags: { operation: "pay_invoice" } },
    );
    if (res.status === 201) lt.stage("payment_accepted");
    lt.evidence("clean_response", res.status < 500, { expected: "no 5xx", actual: { status: res.status } });
  } else if (fx.mode === "adjust") {
    const res = http.post(
      `${T}/invoices/${fx.invoice}`,
      JSON.stringify({ accountId: fx.account, invoiceItemId: fx.item, amount: 100 }),
      { headers: A, tags: { operation: "adjust" } },
    );
    if (res.status === 201) lt.stage("adjustment_accepted");
    lt.evidence("clean_response", res.status < 500, { expected: "no 5xx", actual: { status: res.status } });
  } else if (fx.mode === "privacy" || fx.mode === "privacy-canary") {
    // Control: the owner can read its own account and the marker is visible to us.
    const own = http.get(`${T}/accounts/${fx.account}`, { headers: A, tags: { operation: "own_read" } });
    lt.evidence("own_read_works", own.status === 200 && own.body.includes(fx.marker), {
      expected: "owner reads own account, marker present",
      actual: { status: own.status, marker: own.body.includes(fx.marker) },
    });
    // The "foreign" actor: tenant B's key. In the canary it is tenant A's own key, so a leak MUST be seen.
    const B =
      fx.mode === "privacy"
        ? hdr(__ENV.LT_KB_KEY_B, __ENV.LT_KB_SECRET_B)
        : A;
    const probes = [
      ["x_account", "GET", `${T}/accounts/${fx.account}`],
      ["x_payments", "GET", `${T}/accounts/${fx.account}/payments`],
      ["x_invoices", "GET", `${T}/accounts/${fx.account}/invoices`],
      ["x_methods", "GET", `${T}/accounts/${fx.account}/paymentMethods`],
      ["x_search", "GET", `${T}/accounts/search/${fx.search}`],
      ["x_bykey", "GET", `${T}/accounts?externalKey=${fx.key}`],
    ];
    const leaks = [];
    const statuses = {};
    let worst = 0;
    for (const [op, , url] of probes) {
      const r = http.get(url, { headers: B, tags: { operation: op } });
      worst = Math.max(worst, r.status);
      statuses[op] = r.status;
      // A search is a list: "200 with no rows" is the right answer to a foreign search, so for it only the
      // marker counts. (My first version flagged every 200 here: that was my rule being wrong, see LOG.md.)
      const refusedOk = op === "x_search" ? true : r.status !== 200;
      if (!refusedOk || r.body.includes(fx.marker)) leaks.push({ op, status: r.status, marker: r.body.includes(fx.marker) });
    }
    // Write: the foreign actor tries to add a payment on someone else's account.
    const w = http.post(
      `${T}/accounts/${fx.account}/payments`,
      JSON.stringify({
        transactionType: "PURCHASE",
        amount: 1,
        currency: "USD",
        paymentExternalKey: fx.key,
        transactionExternalKey: `${fx.key}-foreign-${n}`,
      }),
      { headers: B, tags: { operation: "x_write" } },
    );
    worst = Math.max(worst, w.status);
    statuses.x_write = w.status;
    if (w.status >= 200 && w.status < 300) leaks.push({ op: "x_write", status: w.status, marker: false });
    lt.evidence("no_foreign_data", leaks.length === 0, {
      expected: "every cross-tenant probe refused and free of the owner's marker",
      actual: leaks,
    });
    lt.evidence("clean_response", worst < 500, {
      expected: "cross-tenant refusals are 4xx",
      actual: { worst_status: worst, statuses },
    });
    if (leaks.length === 0) lt.stage("refused");
  } else {
    throw new Error("unknown mode " + fx.mode);
  }
}
