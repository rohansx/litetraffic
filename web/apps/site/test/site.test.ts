// Checks the built page (the `test` script runs `astro build` first), so it sees exactly what ships.
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, test } from "vitest";

const DIST = join(import.meta.dirname, "..", "dist");
const html = readFileSync(join(DIST, "index.html"), "utf8");
const files = (dir: string): string[] =>
  readdirSync(dir, { withFileTypes: true }).flatMap((e) => (e.isDirectory() ? files(join(dir, e.name)) : [join(dir, e.name)]));
const assets = files(DIST).filter((f) => /\.(css|js)$/.test(f));

describe("landing page", () => {
  test("hero headline is exactly 'Users as an API.'", () => {
    const h1 = html.match(/<h1\b[^>]*id="hero-title"[^>]*>([\s\S]*?)<\/h1>/);
    expect(h1).not.toBeNull();
    expect(h1![1]).toBe("Users<br>as an API.");
  });

  test("keeps every section the nav and tests link to", () => {
    const ids = new Set([...html.matchAll(/\sid="([^"]+)"/g)].map((m) => m[1]));
    for (const id of ["main", "mechanics", "verdict", "features", "isolation", "dashboard", "profiles", "scenarios", "commands", "targets", "start", "theme-toggle"]) {
      expect(ids, id).toContain(id);
    }
    for (const [, target] of html.matchAll(/href="#([^"]+)"/g)) expect(ids, `#${target}`).toContain(target);
  });

  test("has the page landmarks", () => {
    for (const tag of ["header", "nav", "main", "footer"]) expect(html).toContain(`<${tag}`);
  });

  test("never links outside its own folder", () => {
    for (const text of [html, ...assets.map((f) => readFileSync(f, "utf8"))]) expect(text).not.toContain("../");
  });

  test("makes no external requests: every loaded resource is local", () => {
    const loaded = [
      ...html.matchAll(/<(?:script|img|iframe|source|audio|video)\b[^>]*\bsrc="([^"]+)"/g),
      ...html.matchAll(/<link\b[^>]*\bhref="([^"]+)"/g),
    ].map((m) => m[1]);
    const cssUrls = assets
      .filter((f) => f.endsWith(".css"))
      .flatMap((f) => [...readFileSync(f, "utf8").matchAll(/(?:url\(|@import\s+)["']?([^"')\s;]+)/g)].map((m) => m[1]));
    const external = [...loaded, ...cssUrls].filter((u) => /^(?:[a-z]+:)?\/\//i.test(u) && !u.startsWith("data:"));
    expect(external).toEqual([]);
    expect(loaded.length).toBeGreaterThan(0);
  });

  test("toggle and copy buttons keep an accessible name that matches their state", () => {
    const tag = (id: string) => html.match(new RegExp(`<button\\b[^>]*\\bid="${id}"[^>]*>`))![0];
    // A toggle announces its state through aria-pressed, so its name must not flip too.
    expect(tag("theme-toggle")).toContain('aria-label="Dark mode"');
    // The copy button's visible text changes to "Copied"; an aria-label would hide that from screen readers.
    expect(tag("copy-command")).not.toContain("aria-label");
  });

  test("the run preview renders finished, so it reads the same without JavaScript", () => {
    const steps = [...html.matchAll(/data-step\b[^>]*data-state="([^"]+)"/g)].map((m) => m[1]);
    expect(steps).toEqual(["done", "done", "done", "done"]);
    expect(html).toContain("data-run-again");
    expect(html).not.toMatch(/<li\b[^>]*data-log[^>]*\shidden/);
  });

  test("the verdict comparison shows the documented failing sample", () => {
    expect(html).toContain("one_effect_per_payment");
    expect(html).toMatch(/data-view="lt"[^>]*>|aria-pressed="true" data-view="lt"/);
    expect(html).toContain("&quot;actual&quot;: 2, &quot;expected&quot;: 1");
  });

  test("every signal is decorative and sits next to its verdict word", () => {
    const signals = [...html.matchAll(/<span\b[^>]*class="signal[^"]*"[^>]*>/g)].map((m) => m[0]);
    expect(signals.length).toBeGreaterThan(5);
    for (const s of signals) expect(s).toContain('aria-hidden="true"');
  });

  test("links out only to the real repository, with no invented star count", () => {
    const external = [...html.matchAll(/href="(https?:[^"]+)"/g)].map((m) => m[1]);
    expect(external.length).toBeGreaterThan(0);
    for (const url of external) expect(url.startsWith("https://github.com/rohansx/litetraffic")).toBe(true);
    expect(html).not.toMatch(/Star on GitHub[^<]*\d/);
  });

  test("the Inter Tight hero is not tightened again past shadcn's tracking-tight", () => {
    expect(html.match(/<h1\b[^>]*>/)![0]).not.toContain("tracking-tighter");
  });

  test("inline code such as --target never splits after its leading dashes", () => {
    const css = assets.filter((f) => f.endsWith(".css")).map((f) => readFileSync(f, "utf8")).join("");
    expect(css).toMatch(/:not\(pre\)\s*>\s*code\s*\{[^}]*white-space:\s*nowrap/);
  });
});
