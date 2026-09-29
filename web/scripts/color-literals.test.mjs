// Only packages/design may define colors: fail on any hex/rgb/hsl/oklch literal in app source.
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { extname, join, relative } from "node:path";
import { test } from "node:test";

const WEB = join(import.meta.dirname, "..");
const APPS = join(WEB, "apps");
const EXTENSIONS = new Set([".ts", ".tsx", ".js", ".mjs", ".jsx", ".css", ".astro", ".html", ".svg"]);
const SKIP_DIRS = new Set(["node_modules", "dist", ".astro", "public"]);

// Hex colors, except inside shadcn chart's recharts attribute selectors ([stroke='#ccc']), which match rather than define.
const HEX = /(?<![\w&])(?<!\[(?:stroke|fill)=')#(?:[0-9a-f]{8}|[0-9a-f]{6}|[0-9a-f]{3,4})\b/gi;
const FUNCTION = /\b(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch)\(/gi;

export function colorLiterals(text) {
  return [...text.matchAll(HEX), ...text.matchAll(FUNCTION)].map((match) => match[0]);
}

function* sourceFiles(dir) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    if (entry.isDirectory()) {
      if (!SKIP_DIRS.has(entry.name)) yield* sourceFiles(join(dir, entry.name));
    } else if (EXTENSIONS.has(extname(entry.name))) {
      yield join(dir, entry.name);
    }
  }
}

test("the detector catches every color literal form and ignores look-alikes", () => {
  assert.deepEqual(colorLiterals("color: #fff; fill: #A3302A80"), ["#fff", "#A3302A80"]);
  assert.deepEqual(colorLiterals("rgb(0 0 0) rgba(1,2,3,.5) hsl(1 2% 3%) oklch(0.5 0.1 90)"), ["rgb(", "rgba(", "hsl(", "oklch("]);
  assert.deepEqual(colorLiterals("[&_.x[stroke='#ccc']]:stroke-border &#123; href=\"#main\" label(x) bg-muted"), []);
});

test("no color literals in apps/ source", () => {
  const offenders = [];
  for (const file of sourceFiles(APPS)) {
    const found = colorLiterals(readFileSync(file, "utf8"));
    if (found.length) offenders.push(`${relative(WEB, file)}: ${found.join(", ")}`);
  }
  assert.deepEqual(offenders, [], "define colors in packages/design/tokens.css and use its tokens instead");
});
