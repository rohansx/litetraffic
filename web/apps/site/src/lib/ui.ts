// Stock shadcn/ui (new-york) class strings, copied from the dashboard's components/ui so the landing
// page and the dashboard read as one product. Keep these in step with button.tsx, badge.tsx, card.tsx, tabs.tsx.
export const wrap = "mx-auto w-full max-w-6xl px-4 sm:px-6";

const btnBase =
  "inline-flex shrink-0 items-center justify-center gap-2 rounded-md text-sm font-medium whitespace-nowrap transition-all outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4";
export const button = `${btnBase} h-10 px-6 bg-primary text-primary-foreground shadow-xs hover:bg-primary/90`;
export const buttonOutline = `${btnBase} h-10 px-6 border bg-background shadow-xs hover:bg-accent hover:text-accent-foreground dark:border-input dark:bg-input/30 dark:hover:bg-input/50`;
export const buttonOutlineSm = `${btnBase} h-8 px-3 border bg-background shadow-xs hover:bg-accent hover:text-accent-foreground dark:border-input dark:bg-input/30 dark:hover:bg-input/50`;
export const buttonGhostSm = `${btnBase} h-8 px-3 hover:bg-accent hover:text-accent-foreground dark:hover:bg-accent/50`;
export const buttonIcon = `${btnBase} size-9 hover:bg-accent hover:text-accent-foreground dark:hover:bg-accent/50`;

export const card = "flex flex-col rounded-xl border bg-card text-card-foreground shadow-sm";
export const cardHeader = "grid gap-1.5 px-6";
export const cardTitle = "font-heading leading-none font-semibold";
export const cardDescription = "text-sm text-muted-foreground";

export const badgeOutline =
  "inline-flex w-fit shrink-0 items-center justify-center gap-1 rounded-md border px-2 py-0.5 text-xs font-medium whitespace-nowrap text-foreground [&>svg]:size-3";
export const badgeSecondary =
  "inline-flex w-fit shrink-0 items-center justify-center gap-1 rounded-md border border-transparent bg-secondary px-2 py-0.5 text-xs font-medium whitespace-nowrap text-secondary-foreground";

// Tabs look (TabsList + TabsTrigger) for aria-pressed toggle groups.
export const tabsList = "inline-flex h-9 w-fit items-center justify-center rounded-lg bg-muted p-[3px] text-muted-foreground";
export const tabsTrigger =
  "inline-flex h-[calc(100%-1px)] flex-1 items-center justify-center gap-1.5 rounded-md border border-transparent px-2.5 py-1 text-sm font-medium whitespace-nowrap text-foreground transition-[color,box-shadow] focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-1 focus-visible:outline-ring dark:text-muted-foreground aria-pressed:bg-background aria-pressed:shadow-sm dark:aria-pressed:border-input dark:aria-pressed:bg-input/30 dark:aria-pressed:text-foreground";

export const label = "mb-3 text-sm font-medium text-muted-foreground";
export const h2 = "font-heading text-3xl font-semibold tracking-tight text-balance sm:text-4xl";
export const lead = "mt-4 max-w-2xl text-base leading-relaxed text-muted-foreground text-pretty sm:text-lg";
export const section = "py-16 sm:py-24";
export const codeBlock = "overflow-x-auto font-mono text-xs leading-relaxed sm:text-[13px]";

export type Verdict = "pass" | "fail" | "error" | "inconclusive";

// dashboard-01 status badge: outline badge with a colored marker; the verdict word is always shown.
export const verdictDot: Record<Verdict, string> = {
  pass: "fill-pass",
  fail: "fill-fail",
  error: "fill-error",
  inconclusive: "fill-inconclusive",
};
