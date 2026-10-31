// Per-run fixture for the Vendure stock study. Reads LT_TARGET, LT_RUN_ID, LT_VENDURE_AUTH ("Bearer <admin token>").
// Creates a fresh product with exactly STOCK units, at the price in LT_PRICE (unique per run, so paid orders for this run can be counted by their total).
// Prints one JSON line.
const BASE = process.env.LT_TARGET;
const AUTH = process.env.LT_VENDURE_AUTH;
if (!BASE || !AUTH) { console.error("need LT_TARGET and LT_VENDURE_AUTH"); process.exit(1); }
const STOCK = 5;

async function gql(api, query, variables, authHeader) {
  const res = await fetch(`${BASE}/${api}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(authHeader ? { Authorization: authHeader } : {}) },
    body: JSON.stringify({ query, variables }),
  });
  const body = await res.json();
  if (body.errors) throw new Error(`${api}: ${JSON.stringify(body.errors).slice(0, 300)}`);
  return { data: body.data, token: res.headers.get("vendure-auth-token") };
}

const run = (process.env.LT_RUN_ID || "x").replace(/[^A-Za-z0-9]/g, "").slice(-12).toLowerCase();
const p = await gql("admin-api", `mutation($i: CreateProductInput!){ createProduct(input:$i){ id } }`,
  { i: { enabled: true, translations: [{ languageCode: "en", name: `LT stock ${run}`, slug: `lt-stock-${run}`, description: "study" }] } }, AUTH);
const v = await gql("admin-api", `mutation($i:[CreateProductVariantInput!]!){ createProductVariants(input:$i){ id priceWithTax stockOnHand } }`,
  { i: [{ productId: p.data.createProduct.id, sku: `LT-${run}`, price: Number(process.env.LT_PRICE || 1000), stockOnHand: STOCK, trackInventory: "TRUE",
          translations: [{ languageCode: "en", name: `LT stock ${run}` }] }] }, AUTH);
const variant = v.data.createProductVariants[0];

console.log(JSON.stringify({
  variant_id: variant.id, stock: variant.stockOnHand,
  hold_seconds: Number(process.env.LT_HOLD ?? 2),
  expected_total: variant.priceWithTax + 500,           // price incl. tax + standard shipping
}));
