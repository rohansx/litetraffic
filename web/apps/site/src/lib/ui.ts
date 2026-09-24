// Stock shadcn/ui (new-york) class strings, copied from the dashboard's components/ui so the landing
// page and the dashboard read as one product. Keep these in step with button.tsx, badge.tsx, tabs.tsx.
export const wrap = "mx-auto w-full max-w-6xl px-4 sm:px-6";
export const repo = "https://github.com/rohansx/litetraffic";
export const doc = (path: string) => `${repo}/blob/main/${path}`;

const btnBase =
  "inline-flex shrink-0 items-center justify-center gap-2 rounded-md text-sm font-medium whitespace-nowrap transition-all outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4";
export const button = `${btnBase} h-10 px-6 bg-primary text-primary-foreground shadow-xs hover:bg-primary/90`;
export const buttonSm = `${btnBase} h-8 px-3 bg-primary text-primary-foreground shadow-xs hover:bg-primary/90`;
export const buttonOutline = `${btnBase} h-10 px-6 border border-input bg-background shadow-xs hover:bg-accent hover:text-accent-foreground dark:bg-input/30 dark:hover:bg-input/50`;
export const buttonGhostSm = `${btnBase} h-8 px-3 hover:bg-accent hover:text-accent-foreground dark:hover:bg-accent/50`;
export const buttonIcon = `${btnBase} size-9 hover:bg-accent hover:text-accent-foreground dark:hover:bg-accent/50`;

export const badgeOutline =
  "inline-flex w-fit shrink-0 items-center justify-center gap-1.5 rounded-md border px-2 py-0.5 text-xs font-medium whitespace-nowrap text-foreground";
/** Mono chip: shadcn outline badge set in JetBrains Mono, for flags, ids and states. */
export const chip = `${badgeOutline} font-mono font-normal text-muted-foreground`;

// Tabs look (TabsList + TabsTrigger) for aria-pressed toggle groups.
export const tabsList = "inline-flex h-9 w-fit items-center justify-center rounded-lg bg-muted p-[3px] text-muted-foreground";
export const tabsTrigger =
  "inline-flex h-[calc(100%-1px)] flex-1 items-center justify-center gap-1.5 rounded-md border border-transparent px-3 py-1 font-mono text-xs font-medium whitespace-nowrap text-muted-foreground transition-[color,box-shadow] focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-1 focus-visible:outline-ring aria-pressed:bg-primary aria-pressed:text-primary-foreground aria-pressed:shadow-sm";

export const h2 = "font-heading text-3xl font-semibold tracking-tight text-balance sm:text-4xl";
export const lead = "mt-3 max-w-2xl text-base leading-relaxed text-muted-foreground text-pretty sm:text-lg";
export const kicker = "font-mono text-xs text-muted-foreground";
export const section = "py-16 sm:py-24";
export const codeBlock = "overflow-x-auto font-mono text-xs leading-relaxed sm:text-[13px]";

export type Verdict = "pass" | "fail" | "error" | "inconclusive";
