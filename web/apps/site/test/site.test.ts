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
    for (const id of ["main", "mechanics", "isolation", "dashboard", "profiles", "scenarios", "commands", "targets", "start", "theme-toggle"]) {
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

  test("the dashboard mock is labelled illustrative", () => {
    expect(html).toContain("Illustrative mock");
  });
});
