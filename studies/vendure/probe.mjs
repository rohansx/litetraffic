// One manual purchase against the shop API, logging every call. Usage: node probe.mjs > raw/05-one-purchase.txt
const BASE = "http://localhost:3000";
const log = (s) => console.log(s);
async function gql(api, query, variables, token, label) {
  const res = await fetch(`${BASE}/${api}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: JSON.stringify({ query, variables }),
  });
  const body = await res.json();
  log(`\n### ${label}  [${api}] HTTP ${res.status}\n${query.trim().split("\n")[0].slice(0, 110)}\n=> ${JSON.stringify(body).slice(0, 420)}`);
  return { body, token: res.headers.get("vendure-auth-token") };
}
const admin = await gql("admin-api", `mutation { login(username:"superadmin", password:"superadmin"){ ... on CurrentUser{ id } ... on ErrorResult{ message } } }`, {}, null, "admin login");
const A = admin.token;
const v = await gql("admin-api", `{ productVariants(options:{take:3}){ items{ id name sku stockOnHand trackInventory outOfStockThreshold } } }`, {}, A, "read stock settings");
const buyer = await gql("shop-api", `mutation { addItemToOrder(productVariantId:"1", quantity:1){ ... on Order{ id code state totalWithTax } ... on ErrorResult{ errorCode message } } }`, {}, null, "buyer: add 1 laptop to cart");
const T = buyer.token;
await gql("shop-api", `mutation { setCustomerForOrder(input:{emailAddress:"probe@example.com", firstName:"P", lastName:"B"}){ ... on Order{ id } ... on ErrorResult{ errorCode message } } }`, {}, T, "buyer: set customer");
await gql("shop-api", `mutation { setOrderShippingAddress(input:{fullName:"P B", streetLine1:"1 Test St", city:"Town", postalCode:"12345", countryCode:"US"}){ ... on Order{ id } ... on ErrorResult{ errorCode message } } }`, {}, T, "buyer: shipping address");
await gql("shop-api", `{ eligibleShippingMethods{ id code price } }`, {}, T, "buyer: list shipping methods");
await gql("shop-api", `mutation { setOrderShippingMethod(shippingMethodId:["1"]){ ... on Order{ id } ... on ErrorResult{ errorCode message } } }`, {}, T, "buyer: choose shipping");
await gql("shop-api", `mutation { transitionOrderToState(state:"ArrangingPayment"){ ... on Order{ id state } ... on OrderStateTransitionError{ errorCode message transitionError } } }`, {}, T, "buyer: move to payment");
await gql("shop-api", `{ eligiblePaymentMethods{ id code isEligible } }`, {}, T, "buyer: list payment methods");
await gql("shop-api", `mutation { addPaymentToOrder(input:{method:"standard-payment", metadata:{}}){ ... on Order{ id code state totalWithTax } ... on ErrorResult{ errorCode message } } }`, {}, T, "buyer: pay");
await gql("admin-api", `{ productVariants(options:{take:1}){ items{ id stockOnHand stockAllocated } } }`, {}, A, "read stock after");
