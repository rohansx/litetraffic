// Shared class strings so the landing page reuses the dashboard's shadcn look (button, card, badge).
export const wrap = "mx-auto w-full max-w-[1160px] px-4 sm:px-6";
export const card = "rounded-xl border bg-card text-card-foreground shadow-sm";
export const button =
  "inline-flex h-11 items-center justify-center rounded-md bg-primary px-5 text-sm font-semibold text-primary-foreground transition-colors hover:bg-primary/90";
export const label = "mb-5 text-sm font-semibold text-muted-foreground";
export const h2 = "text-[clamp(2.75rem,5.5vw,4.75rem)] leading-[0.97] font-bold text-balance";
export const lead = "mt-6 max-w-[620px] text-lg leading-relaxed text-muted-foreground text-pretty";

export type Verdict = "pass" | "fail" | "error" | "inconclusive";

const verdictTone: Record<Verdict, string> = {
  pass: "bg-pass-muted text-pass",
  fail: "bg-fail-muted text-fail",
  error: "bg-error-muted text-error",
  inconclusive: "bg-inconclusive-muted text-inconclusive",
};

// Same shape as the dashboard's verdict Badge: tinted surface, and the verdict word is always shown.
export const badge = (verdict: Verdict) =>
  `inline-flex items-center rounded-md px-2 py-0.5 text-xs font-semibold ${verdictTone[verdict]}`;
