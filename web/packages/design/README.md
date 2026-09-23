# @litetraffic/design

The one place LiteTraffic colors, fonts and radius are defined. The dashboard (`apps/dashboard`, Vite + React + shadcn/ui) and the landing page (`apps/site`, Astro) both import it, so they look like one product.

## Usage

```css
/* app entry CSS */
@import "tailwindcss";
@import "@litetraffic/design/tokens.css";
```

Then use the Tailwind utilities the tokens generate: `bg-background`, `text-muted-foreground`, `border-border`, `bg-primary text-primary-foreground`, `text-pass bg-pass-muted`, `font-heading`, `rounded-lg`, `fill-chart-1`, ... or the raw variables (`var(--fail)`) where a utility does not fit (recharts `stroke`, inline SVG).

**Apps never write color literals.** `web/scripts/color-literals.test.mjs` fails `pnpm test` on any hex / `rgb()` / `hsl()` / `oklch()` literal under `apps/`. Need a new color? Add a token here.

## Theming

The base is the stock [shadcn/ui](https://ui.shadcn.com/docs/theming) **neutral** theme (new-york style), values unchanged. Each shadcn `:root` / `.dark` pair is written once as `light-dark(<light>, <dark>)` (Tailwind's Lightning CSS compiles it for older browsers).

- Default: follows the OS (`color-scheme: light dark`).
- Forced: put `class="dark"` or `class="light"` on `<html>`; the `dark:` variant honours both the class and the OS preference.

## Fonts

Self-hosted variable woff2 from Fontsource (no external requests), SIL Open Font License 1.1:

- `font-sans`: **Inter Variable** (`@fontsource-variable/inter`), body text.
- `font-heading`: **Inter Tight Variable** (`@fontsource-variable/inter-tight`), applied by default to `h1`-`h3` and shadcn card titles (`[data-slot=card-title]`); use it for KPI numbers and hero / section titles.

## Tokens

| Token | Light | Dark | Role |
|---|---|---|---|
| `--background` / `--foreground` | `oklch(1 0 0)` / `oklch(0.145 0 0)` | `oklch(0.145 0 0)` / `oklch(0.985 0 0)` | page, body text |
| `--card`, `--popover` (+ `-foreground`) | `oklch(1 0 0)` | `oklch(0.205 0 0)` | raised surfaces |
| `--primary` / `-foreground` | `oklch(0.205 0 0)` / `oklch(0.985 0 0)` | `oklch(0.922 0 0)` / `oklch(0.205 0 0)` | main action |
| `--secondary`, `--muted`, `--accent` | `oklch(0.97 0 0)` | `oklch(0.269 0 0)` | quiet buttons, subdued surfaces, hover rows |
| `--muted-foreground` | `oklch(0.556 0 0)` | `oklch(0.708 0 0)` | subdued text |
| `--destructive` | `oklch(0.577 0.245 27.325)` | `oklch(0.704 0.191 22.216)` | destructive actions |
| `--border` / `--input` | `oklch(0.922 0 0)` | `oklch(1 0 0 / 10%)` / `15%` | dividers, form control borders |
| `--ring` | `oklch(0.708 0 0)` | `oklch(0.556 0 0)` | focus ring |
| `--chart-1..5` | shadcn neutral chart palette | shadcn neutral chart palette | chart series |
| `--sidebar-*` | shadcn neutral sidebar | shadcn neutral sidebar | app sidebar |
| `--radius` | `0.625rem` | | `rounded-sm/md/lg/xl` derive from it |
| `--brand` / `-foreground` | gold `oklch(0.823 0.135 90.7)` / `oklch(0.205 0 0)` | same | LiteTraffic accent, use sparingly (logo mark) |
| `--pass` / `--pass-muted` | `oklch(0.475 0.084 155.3)` / `oklch(0.935 0.022 154.1)` | `oklch(0.820 0.075 153.6)` / `oklch(0.321 0.044 156.8)` | verdict pass |
| `--fail` / `--fail-muted` | `oklch(0.483 0.152 27.4)` / `oklch(0.931 0.023 31.1)` | `oklch(0.771 0.106 27.6)` / `oklch(0.286 0.038 27.4)` | verdict fail |
| `--inconclusive` / `-muted` | `oklch(0.488 0.100 84.4)` / `oklch(0.942 0.048 94.3)` | `oklch(0.823 0.135 90.7)` / `oklch(0.319 0.042 92.7)` | verdict inconclusive |
| `--error` / `--error-muted` | `oklch(0.474 0.157 302.5)` / `oklch(0.929 0.027 307.0)` | `oklch(0.786 0.106 305.1)` / `oklch(0.285 0.052 299.6)` | verdict error (run/engine failure, distinct from fail) |

Verdict colors are never the only signal: badges always carry the verdict word.

## Contrast (WCAG 2.2)

Computed from `tokens.css` by `web/scripts/contrast.test.mjs` (runs in `pnpm test`; fails below AA 4.5:1 for text). The shadcn base is kept as shipped, so its non-text `--ring` / `--input` and light `muted-foreground` on `muted` (4.34) are not asserted.

| Pair | Light | Dark |
|---|---|---|
| foreground on background / card | 19.79 / 19.79 | 18.96 / 17.16 |
| muted-foreground on card / background | 4.73 / 4.73 | 6.91 / 7.63 |
| primary-foreground on primary | 17.16 | 14.22 |
| secondary-foreground on secondary | 16.42 | 14.48 |
| brand-foreground on brand | 10.35 | 10.35 |
| sidebar-foreground on sidebar | 18.96 | 17.16 |
| pass on card / background / muted / pass-muted | 6.41 / 6.41 / 5.87 / 5.33 | 10.56 / 11.67 / 8.91 / 7.31 |
| fail on card / background / muted / fail-muted | 6.97 / 6.97 / 6.39 / 5.65 | 8.32 / 9.19 / 7.02 / 6.74 |
| inconclusive on card / background / muted / inconclusive-muted | 6.38 / 6.38 / 5.85 / 5.39 | 10.35 / 11.43 / 8.73 / 7.35 |
| error on card / background / muted / error-muted | 7.24 / 7.24 / 6.64 / 5.83 | 8.82 / 9.75 / 7.44 / 7.18 |
