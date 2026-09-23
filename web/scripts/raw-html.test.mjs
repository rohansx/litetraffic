// The dashboard renders untrusted run artifacts: its source never injects raw HTML.
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { extname, join, relative } from "node:path";
import { test } from "node:test";

const WEB = join(import.meta.dirname, "..");
const SKIP_DIRS = new Set(["node_modules", "dist", ".astro", "public"]);
const RAW_HTML = /dangerouslySetInnerHTML|\.innerHTML\s*=|\.outerHTML\s*=|insertAdjacentHTML/;

function* sourceFiles(dir) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    if (entry.isDirectory()) {
      if (!SKIP_DIRS.has(entry.name)) yield* sourceFiles(join(dir, entry.name));
    } else if ([".ts", ".tsx", ".js", ".jsx", ".mjs"].includes(extname(entry.name))) {
      yield join(dir, entry.name);
    }
  }
}

test("no raw HTML injection in dashboard source", () => {
  const offenders = [...sourceFiles(join(WEB, "apps", "dashboard"))].filter((file) => RAW_HTML.test(readFileSync(file, "utf8")));
  assert.deepEqual(offenders.map((file) => relative(WEB, file)), []);
});
