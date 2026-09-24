# @litetraffic/design: Signal

The one place LiteTraffic colors, fonts, radius and shared surface utilities are defined. The dashboard (`apps/dashboard`, Vite + React + shadcn/ui) and the landing page (`apps/site`, Astro) both import it, so they look like one product.

**Signal** is a blueprint-on-paper look (warm paper / deep asphalt, faint grid, corner brackets, ruler ticks, mono labels, black ink buttons) with one personal touch: traffic signals. LiteTraffic's output is pass / inconclusive / fail, so the brand accent is **signal amber**, the verdict triad is green / amber / red (used only for meaning), window chrome dots are a traffic light, and a three-lamp **signal** indicator shows a verdict in both apps. Section motif: SEED · DRIVE · OBSERVE · VERDICT.

## Usage

```css
/* app entry CSS */
@import "tailwindcss";
@import "@litetraffic/design/tokens.css";
```

Then use the Tailwind utilities the tokens generate: `bg-background`, `text-muted-foreground`, `border-border`, `bg-primary text-primary-foreground`, `text-pass bg-pass-muted`, `text-brand-text`, `font-heading`, `font-mono`, `rounded-lg`, `fill-chart-1`, ... or the raw variables (`var(--fail)`) where a utility does not fit (recharts `stroke`, inline SVG).

**Apps never write color literals.** `web/scripts/color-literals.test.mjs` fails `pnpm test` on any hex / `rgb()` / `hsl()` / `oklch()` literal under `apps/`. Need a new color? Add a token here.

shadcn components stay stock: the variable names are shadcn's, so they restyle from the tokens. Adjust with `className`, never fork a component.

## Theming

- Default: follows the OS (`color-scheme: light dark`); each token is written once as `light-dark(<light>, <dark>)`.
- Forced: put `class="dark"` or `class="light"` on `<html>`; the `dark:` variant honours both the class and the OS preference.

## Fonts

Self-hosted variable woff2 from Fontsource (no external requests), SIL Open Font License 1.1:

- `font-sans`: **Inter Variable** (`@fontsource-variable/inter`), body text.
- `font-heading`: **Inter Tight Variable** (`@fontsource-variable/inter-tight`), applied by default to `h1`-`h3` and shadcn card titles (`[data-slot=card-title]`); use it for KPI numbers and hero / section titles.
- `font-mono`: **JetBrains Mono Variable** (`@fontsource-variable/jetbrains-mono`), applied by default to `code`, `kbd`, `samp`, `pre`; use it for eyebrows, labels, chips, run ids.

## Tokens

| Token | Light | Dark | Role |
|---|---|---|---|
| `--background` | paper `oklch(0.972 0.005 95)` | asphalt `oklch(0.170 0.008 255)` | page |
| `--foreground` (= `card-`, `popover-`, `secondary-`, `accent-`, `sidebar-foreground`) | ink `oklch(0.190 0.008 255)` | `oklch(0.955 0.005 95)` | body text |
| `--card`, `--popover` | `oklch(0.992 0.003 95)` | `oklch(0.212 0.009 255)` | raised surfaces, window cards |
| `--primary` / `-foreground` | ink / `oklch(0.985 0.003 95)` | paper `oklch(0.955 0.005 95)` / asphalt | black ink primary button |
| `--secondary`, `--muted` | `oklch(0.940 0.006 95)` | `oklch(0.262 0.010 255)` | quiet buttons, header strips |
| `--accent` | `oklch(0.935 0.012 85)` | `oklch(0.275 0.014 255)` | hover rows (faint amber warmth) |
| `--muted-foreground` | `oklch(0.490 0.012 255)` | `oklch(0.735 0.010 255)` | subdued text, eyebrows |
| `--destructive` | `oklch(0.520 0.180 27.5)` | `oklch(0.720 0.150 27.5)` | destructive actions |
| `--border` / `--input` | `oklch(0.885 0.007 95)` / `oklch(0.800 0.008 95)` | `oklch(0.315 0.010 255)` / `oklch(0.420 0.010 255)` | dividers / outline buttons, form controls |
| `--ring` | amber `oklch(0.600 0.150 62)` | `oklch(0.800 0.155 72)` | focus ring |
| `--brand` / `-foreground` | signal amber `oklch(0.800 0.155 72)` / ink | same | amber fills: logo mark, active nav, highlights |
| `--brand-text` | `oklch(0.520 0.120 58)` | `oklch(0.800 0.155 72)` | amber as readable text |
| `--grid-line` | ink at 5% | paper at 5% | `.grid-bg` blueprint grid |
| `--rule` | `oklch(0.640 0.010 255)` | `oklch(0.500 0.010 255)` | brackets, ruler ticks, lane markings, connectors |
| `--lamp-stop` / `-caution` / `-go` / `-off` | red / amber / green / unlit | brighter | traffic-light dots (decorative, never the only signal) |
| `--chart-1..5` | amber, blue, teal, ink grey, plum | lighter set | chart series; green/red are reserved for verdicts |
| `--sidebar`, `--sidebar-accent` | `oklch(0.958 0.006 95)`, `oklch(0.925 0.012 85)` | `oklch(0.192 0.009 255)`, `oklch(0.262 0.012 255)` | app sidebar; `--sidebar-primary` = brand amber |
| `--radius` | `0.5rem` | | `rounded-sm/md/lg/xl` derive from it |
| `--pass` / `-muted` | `oklch(0.475 0.100 152)` / `oklch(0.935 0.035 152)` | `oklch(0.820 0.110 152)` / `oklch(0.290 0.050 152)` | verdict pass (green) |
| `--inconclusive` / `-muted` | `oklch(0.500 0.110 70)` / `oklch(0.940 0.050 85)` | `oklch(0.830 0.140 78)` / `oklch(0.300 0.050 75)` | verdict inconclusive (amber) |
| `--fail` / `-muted` | `oklch(0.500 0.170 27.5)` / `oklch(0.935 0.030 27.5)` | `oklch(0.780 0.120 27.5)` / `oklch(0.290 0.060 27.5)` | verdict fail (red) |
| `--error` / `-muted` | `oklch(0.474 0.157 302.5)` / `oklch(0.935 0.027 307.0)` | `oklch(0.786 0.106 305.1)` / `oklch(0.285 0.052 299.6)` | verdict error: the run itself broke (distinct from fail) |

Verdict colors are never the only signal: badges and signals always sit next to the verdict word.

## Utilities

Plain CSS classes in `@layer components` (a Tailwind utility on the same element wins). Tune them with the listed custom properties.

| Class | Markup | Knobs |
|---|---|---|
| `grid-bg` | `<section class="grid-bg">` | `--grid-size` (2rem) |
| `bracket-frame` | `<div class="bracket-frame">preview</div>`: corner brackets just outside the box | `--bracket-size`, `--bracket-inset`, `--bracket-color` |
| `eyebrow` | `<p class="eyebrow">01 · seed</p>`: mono small caps, amber diamond | |
| `section-number` | `<span class="section-number is-active">01</span>`: boxed step number + connector line; filled when `.is-active` / `[aria-current]` | `--connector-length` (5rem, `0` hides) |
| `lane-divider` | `<hr class="lane-divider">`: dashed road marking | `--lane-color` (e.g. `var(--brand)`) |
| `ruler` | `<hr class="ruler">`: minor ticks, major every 5 | `--tick` (1rem) |
| `window-card` + `window-card-header` | `<div class="window-card"><div class="window-card-header">run · checkout</div>…</div>`: red/amber/green dots in the header | |
| `signal` | `<span class="signal" data-verdict="pass" role="img" aria-label="Pass"></span> Pass`: stop/caution/go lamps, one lit; `error` lights all three in the error color | `--signal-dot` (0.5rem) |

Numbered markers (`section-number`, `01 · seed`) are for real sequences only: the seed, drive, observe, verdict steps.

## Contrast (WCAG 2.2)

Computed from `tokens.css` by `web/scripts/contrast.test.mjs` (runs in `pnpm test`; fails below 4.5:1 for text, 3:1 for the focus ring and ink buttons). Decorative lamps, `--grid-line` and `--border` are not asserted.

| Pair | Light | Dark |
|---|---|---|
| foreground on background / card | 17.03 / 18.05 | 16.77 / 15.47 |
| muted-foreground on card / background / muted / sidebar | 6.12 / 5.77 / 5.25 / 5.54 | 7.51 / 8.15 / 6.58 / 7.84 |
| primary-foreground on primary | 17.69 | 16.77 |
| primary on background (3:1) | 17.03 | 16.77 |
| secondary-foreground on secondary | 15.49 | 13.54 |
| accent-foreground on accent | 15.25 | 13.00 |
| destructive on card | 5.90 | 6.67 |
| brand-foreground on brand | 9.64 | 9.64 |
| brand-text on background / card | 5.28 / 5.60 | 9.98 / 9.21 |
| ring on background / card (3:1) | 3.79 / 4.01 | 9.98 / 9.21 |
| sidebar-foreground on sidebar | 16.35 | 16.14 |
| sidebar-accent-foreground on sidebar-accent | 14.80 | 13.54 |
| sidebar-primary-foreground on sidebar-primary | 9.64 | 9.64 |
| pass on card / background / muted / pass-muted | 6.22 / 5.87 / 5.34 / 5.32 | 10.53 / 11.42 / 9.22 / 8.27 |
| inconclusive on card / background / muted / inconclusive-muted | 6.01 / 5.67 / 5.16 / 5.15 | 10.29 / 11.15 / 9.01 / 8.01 |
| fail on card / background / muted / fail-muted | 6.40 / 6.04 / 5.49 / 5.35 | 8.40 / 9.11 / 7.36 / 6.88 |
| error on card / background / muted / error-muted | 7.07 / 6.68 / 6.07 / 5.94 | 8.68 / 9.42 / 7.60 / 7.18 |
