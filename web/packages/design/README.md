# @litetraffic/design

The one place LiteTraffic colors, fonts and radius are defined. The dashboard (`apps/dashboard`, Vite + React + shadcn/ui) and the landing page (`apps/site`, Astro) both import it, so they look like one product.

## Usage

```css
/* app entry CSS */
@import "tailwindcss";
@import "@litetraffic/design/tokens.css";
```

Then use the Tailwind utilities the tokens generate: `bg-background`, `text-muted-foreground`, `border-border`, `bg-primary text-primary-foreground`, `text-pass bg-pass-muted`, `font-display`, `rounded-lg`, `fill-chart-1`, ... or the raw variables (`var(--fail)`) where a utility does not fit (recharts `stroke`, inline SVG).

**Apps never write color literals.** `web/scripts/color-literals.test.mjs` fails `pnpm test` on any hex / `rgb()` / `hsl()` / `oklch()` literal under `apps/`. Need a new color? Add a token here.

## Theming

Each color is declared once as `light-dark(<light>, <dark>)` (Tailwind's Lightning CSS compiles it for older browsers).

- Default: follows the OS (`color-scheme: light dark`).
- Forced: put `class="dark"` or `class="light"` on `<html>`; the `dark:` variant honours both the class and the OS preference.

## Fonts

`fonts/` holds Fira Sans (400, 600) and Fira Sans Condensed (600), SIL Open Font License 1.1 (`fonts/LICENSE.txt`). `font-sans` is Fira Sans (body), `font-display` / `font-heading` is Fira Sans Condensed (headings, h1–h3 by default).

## Tokens

Palette carried over from the original landing page (`site/styles.css`): green-grey neutrals and the gold "signal". Hex shown for reference; `tokens.css` holds the OKLCH values.

| Token | Light | Dark | Role |
|---|---|---|---|
| `--background` | `#f3f5f1` | `#121b17` | page |
| `--foreground` | `#1c2927` | `#eff4ed` | body text |
| `--card` / `--popover` | `#ffffff` | `#1b2821` | raised surfaces |
| `--primary` / `-foreground` | `#1c2927` / `#ffffff` | `#e6c151` / `#1b2620` | main action (ink in light, gold in dark, as the landing page's buttons) |
| `--secondary`, `--accent` | `#e7ece7` | `#26362d` | quiet buttons, hover rows |
| `--muted` / `-foreground` | `#e7ece7` / `#56645d` | `#1c2a22` / `#afc0b3` | subdued surfaces and text |
| `--destructive` / `-foreground` | `#a3302a` / `#ffffff` | `#f19a8f` / `#1b2620` | destructive actions |
| `--brand` / `-foreground` | `#e6c151` / `#1b2620` | same | brand gold signal: marks, highlights |
| `--border` | `#cad2ca` | `#3d5043` | dividers (decorative) |
| `--input` | `#7a877f` | `#6b7f71` | form control borders (3:1) |
| `--ring` | `#8a6a0e` | `#e6c151` | focus ring (gold) |
| `--pass` / `--pass-muted` | `#2f6a47` / `#dfeee3` | `#9fd3ae` / `#1f3a2a` | verdict pass |
| `--fail` / `--fail-muted` | `#a3302a` / `#f7e3df` | `#f19a8f` / `#3b2320` | verdict fail |
| `--inconclusive` / `-muted` | `#7a5a00` / `#f6ecc8` | `#e6c151` / `#3a3218` | verdict inconclusive |
| `--error` / `--error-muted` | `#6e3fa3` / `#ece3f6` | `#c9a8f0` / `#2e2440` | verdict error (run/engine failure, distinct from fail) |
| `--chart-1..5` | gold `#8a6a0e`, pass, sage `#758d79`, fail, error | gold `#e6c151`, pass, `#afc0b3`, fail, error | chart series |
| `--sidebar` | `#e7ece7` | `#0d1712` | app sidebar |
| `--sidebar-accent` | `#dde4dd` | `#26362d` | active / hovered sidebar item |
| `--sidebar-primary`, `-border`, `-ring`, `-foreground` | alias primary, border, ring, foreground | | |
| `--radius` | `0.5rem` | | `rounded-sm..4xl` derive from it |

Verdict colors are never the only signal: badges always carry the verdict word.

## Contrast (WCAG 2.2)

Computed from `tokens.css` by `web/scripts/contrast.test.mjs` (runs in `pnpm test`; fails below AA). Text needs 4.5:1, focus rings and input borders 3:1.

| Pair | Light | Dark |
|---|---|---|
| foreground on background | 13.74 | 15.73 |
| foreground on card | 15.04 | 13.73 |
| muted-foreground on card | 6.22 | 8.03 |
| muted-foreground on muted | 5.20 | 7.85 |
| primary-foreground on primary | 15.04 | 9.01 |
| destructive-foreground on destructive | 6.97 | 7.26 |
| pass on card / background / pass-muted | 6.41 / 5.85 / 5.34 | 9.03 / 10.38 / 7.31 |
| fail on card / background / fail-muted | 6.97 / 6.35 / 5.64 | 7.11 / 8.17 / 6.74 |
| inconclusive on card / background / inconclusive-muted | 6.38 / 5.82 / 5.39 | 8.83 / 10.14 / 7.35 |
| error on card / background / error-muted | 7.23 / 6.59 / 5.82 | 7.54 / 8.66 / 7.17 |
| ring on background | 4.61 | 10.14 |
| input on card / background | 3.75 / 3.43 | 3.57 / 4.10 |
