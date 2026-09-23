// WCAG contrast of the design tokens, computed from tokens.css in both themes.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";

const css = readFileSync(join(import.meta.dirname, "../packages/design/tokens.css"), "utf8");
const root = css.slice(css.indexOf(":root {"), css.indexOf("}", css.indexOf(":root {")));
const raw = Object.fromEntries([...root.matchAll(/--([\w-]+):\s*([^;]+);/g)].map((m) => [m[1], m[2].trim()]));

const OKLCH = /oklch\(([\d.]+) ([\d.]+) ([\d.]+)\)/g;

/** [light, dark] oklch strings for a token, following var() aliases. */
export function themed(name) {
  const value = raw[name];
  if (!value) throw new Error(`unknown token --${name}`);
  const alias = value.match(/^var\(--([\w-]+)\)$/);
  if (alias) return themed(alias[1]);
  const colors = [...value.matchAll(OKLCH)].map((m) => m.slice(1).map(Number));
  return colors.length === 1 ? [colors[0], colors[0]] : colors;
}

function luminance([L, C, H]) {
  const a = C * Math.cos((H * Math.PI) / 180);
  const b = C * Math.sin((H * Math.PI) / 180);
  const l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3;
  const m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3;
  const s = (L - 0.0894841775 * a - 1.291485548 * b) ** 3;
  const rgb = [
    4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
    -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
    -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s,
  ].map((v) => Math.min(1, Math.max(0, v)));
  return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2];
}

export function contrast(x, y) {
  const [hi, lo] = [luminance(x), luminance(y)].sort((p, q) => q - p);
  return (hi + 0.05) / (lo + 0.05);
}

// [foreground, background, minimum]: 4.5 for text (AA), 3 for focus rings and input borders (non-text).
const PAIRS = [
  ["foreground", "background", 4.5],
  ["foreground", "card", 4.5],
  ["muted-foreground", "card", 4.5],
  ["muted-foreground", "muted", 4.5],
  ["primary-foreground", "primary", 4.5],
  ["destructive-foreground", "destructive", 4.5],
  ["brand-foreground", "brand", 4.5],
  ["sidebar-foreground", "sidebar", 4.5],
  ...["pass", "fail", "inconclusive", "error"].flatMap((v) => [
    [v, "card", 4.5],
    [v, "background", 4.5],
    [v, "muted", 4.5],
    [v, `${v}-muted`, 4.5],
  ]),
  ["ring", "background", 3],
  ["input", "card", 3],
  ["input", "background", 3],
];

test("token pairs meet WCAG AA in light and dark", () => {
  const failures = [];
  for (const [fg, bg, min] of PAIRS) {
    ["light", "dark"].forEach((theme, i) => {
      const ratio = contrast(themed(fg)[i], themed(bg)[i]);
      if (ratio < min) failures.push(`${theme} --${fg} on --${bg}: ${ratio.toFixed(2)} < ${min}`);
    });
  }
  assert.deepEqual(failures, []);
});

test("contrast math matches known values", () => {
  assert.equal(contrast([1, 0, 0], [0, 0, 0]).toFixed(1), "21.0");
  assert.equal(contrast([1, 0, 0], [1, 0, 0]), 1);
});
