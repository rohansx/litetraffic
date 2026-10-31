// Download the shop API schema by introspection and print it as SDL (what any storefront developer can read).
import { createRequire } from "node:module";
const require = createRequire(new URL("../shop/package.json", import.meta.url));
const { buildClientSchema, getIntrospectionQuery, printSchema } = require("graphql");
const res = await fetch("http://localhost:3000/shop-api", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ query: getIntrospectionQuery() }) });
const { data } = await res.json();
console.log(printSchema(buildClientSchema(data)));
