import { CircleCheck, CircleDashed, CircleHelp, CircleX, OctagonAlert, Radio, type LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { AssertionStatus, ListVerdict } from "@/api/types";

type Tone = ListVerdict | AssertionStatus;

const STYLE: Record<Tone, { label: string; icon: LucideIcon; badge: string; dot: string }> = {
  pass: { label: "Pass", icon: CircleCheck, badge: "bg-pass-muted text-pass", dot: "bg-pass" },
  fail: { label: "Fail", icon: CircleX, badge: "bg-fail-muted text-fail", dot: "bg-fail" },
  inconclusive: { label: "Inconclusive", icon: CircleHelp, badge: "bg-inconclusive-muted text-inconclusive", dot: "bg-inconclusive" },
  error: { label: "Error", icon: OctagonAlert, badge: "bg-error-muted text-error", dot: "bg-error" },
  unknown: { label: "Unknown", icon: CircleHelp, badge: "bg-inconclusive-muted text-inconclusive", dot: "bg-inconclusive" },
  unreadable: { label: "Unreadable", icon: CircleDashed, badge: "bg-muted text-muted-foreground", dot: "bg-muted-foreground" },
  background: { label: "Activity", icon: Radio, badge: "bg-secondary text-secondary-foreground", dot: "bg-muted-foreground" },
};

export const VERDICT_MEANING: Record<"pass" | "fail" | "inconclusive" | "error", string> = {
  pass: "Every declared check held with complete evidence.",
  fail: "At least one business check definitely failed.",
  inconclusive: "Evidence was missing or incomplete, so the run cannot pass.",
  error: "The run could not be evaluated: unreachable target, budget or fixture failure.",
};

function styleOf(verdict: string): (typeof STYLE)[Tone] {
  return STYLE[verdict as Tone] ?? STYLE.unreadable;
}

export function verdictLabel(verdict: string): string {
  return styleOf(verdict).label;
}

/** Verdict as icon + text: color is never the only signal. */
export function VerdictBadge({ verdict, className }: { verdict: string; className?: string }) {
  const { label, icon: Icon, badge } = styleOf(verdict);
  return (
    <Badge className={cn("gap-1 rounded-md font-semibold", badge, className)}>
      <Icon aria-hidden />
      {label}
    </Badge>
  );
}

/** A compact run history: one bar per run, oldest first, each with an accessible label. */
export function VerdictStrip({ runs, max = 16 }: { runs: { run_id: string; verdict: string }[]; max?: number }) {
  const shown = runs.slice(-max);
  return (
    <ol className="flex h-6 items-end gap-0.5" aria-label={`Last ${shown.length} runs, oldest first`}>
      {Array.from({ length: max - shown.length }, (_, index) => (
        <li key={`pad-${index}`} aria-hidden className="h-1 w-1.5 rounded-sm bg-border" />
      ))}
      {shown.map((run) => (
        <li key={run.run_id}>
          <Tooltip>
            <TooltipTrigger asChild>
              <span
                role="img"
                aria-label={`${run.run_id}: ${verdictLabel(run.verdict)}`}
                className={cn(
                  "block w-1.5 rounded-sm",
                  run.verdict === "pass" ? "h-6" : "h-4",
                  styleOf(run.verdict).dot,
                )}
              />
            </TooltipTrigger>
            <TooltipContent>
              <p className="font-semibold">{verdictLabel(run.verdict)}</p>
              <p className="font-mono">{run.run_id}</p>
            </TooltipContent>
          </Tooltip>
        </li>
      ))}
    </ol>
  );
}
