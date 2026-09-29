import type { ReactNode } from "react";
import { Minus, TrendingDown, TrendingUp } from "lucide-react";
import type { RunListEntry } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Card, CardAction, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { VerdictBadge } from "@/components/verdict";
import { formatDateTime, formatRelative } from "@/lib/format";
import { JUDGED, oldestFirst, type ScenarioSummary } from "@/lib/runs";

const DAY_MS = 86_400_000;
const RECENT = 5;

const rate = (runs: RunListEntry[]) => runs.filter((run) => run.verdict === "pass").length / runs.length;

function Trend({ value, children }: { value: number; children: ReactNode }) {
  const Icon = value > 0 ? TrendingUp : value < 0 ? TrendingDown : Minus;
  return (
    <Badge variant="outline" className="font-mono">
      <Icon aria-hidden />
      {children}
    </Badge>
  );
}

function Kpi({ label, value, badge, headline, detail }: { label: string; value: ReactNode; badge: ReactNode; headline: ReactNode; detail: ReactNode }) {
  return (
    <Card className="@container/card">
      <CardHeader>
        <CardDescription className="font-mono text-xs">{label}</CardDescription>
        <CardTitle className="text-2xl font-semibold tabular-nums @[250px]/card:text-3xl">{value}</CardTitle>
        <CardAction>{badge}</CardAction>
      </CardHeader>
      <CardFooter className="flex-col items-start gap-1.5 text-sm">
        <div className="line-clamp-1 font-medium">{headline}</div>
        <div className="line-clamp-1 text-muted-foreground">{detail}</div>
      </CardFooter>
    </Card>
  );
}

/** dashboard-01 SectionCards, fed from the runs list. Trends are relative to the newest run, not the clock. */
export function SectionCards({ runs, summaries }: { runs: RunListEntry[]; summaries: ScenarioSummary[] }) {
  const verifyRuns = oldestFirst(runs.filter((run) => run.kind === "run"));
  const finished = verifyRuns.filter((run) => run.finished_at);
  const newest = finished.at(-1);
  const since = newest ? Date.parse(newest.finished_at!) - DAY_MS : 0;
  const lastDay = finished.filter((run) => Date.parse(run.finished_at!) > since).length;
  const others = runs.length - verifyRuns.length;

  const judged = verifyRuns.filter((run) => JUDGED.has(run.verdict));
  const passed = judged.filter((run) => run.verdict === "pass").length;
  const recent = judged.slice(-RECENT);
  const earlier = judged.slice(0, -RECENT);
  const delta = earlier.length ? Math.round((rate(recent) - rate(earlier)) * 100) : 0;

  const failing = summaries.filter((s) => s.latest && (s.latest.verdict === "fail" || s.latest.verdict === "error"));

  return (
    <section
      aria-label="Key figures"
      className="grid grid-cols-1 gap-4 *:data-[slot=card]:bg-gradient-to-t *:data-[slot=card]:from-primary/5 *:data-[slot=card]:to-card *:data-[slot=card]:shadow-xs @xl/main:grid-cols-2 @5xl/main:grid-cols-4 dark:*:data-[slot=card]:bg-card"
    >
      <Kpi
        label="Runs"
        value={verifyRuns.length}
        badge={<Trend value={lastDay}>+{lastDay} in 24h</Trend>}
        headline={`${lastDay} in the last 24 hours`}
        detail={others ? `Plus ${others} series and activities` : "Verify runs on disk"}
      />
      <Kpi
        label="Pass rate"
        value={judged.length ? `${Math.round((passed / judged.length) * 100)}%` : "n/a"}
        badge={
          <Trend value={delta}>
            {delta > 0 ? "+" : ""}
            {delta} pts
          </Trend>
        }
        headline={`${passed} of ${judged.length} judged runs passed`}
        detail={earlier.length ? `Last ${recent.length} runs vs earlier ones` : "Not enough runs for a trend"}
      />
      <Kpi
        label="Failing scenarios"
        value={failing.length}
        badge={
          <Trend value={-failing.length}>
            {failing.length} of {summaries.length}
          </Trend>
        }
        headline={failing.length ? "Latest run failed or errored" : "No scenario is failing"}
        detail={failing.map((s) => s.name).join(", ") || `All ${summaries.length} scenarios are clear`}
      />
      <Kpi
        label="Last run"
        value={formatRelative(newest?.finished_at)}
        badge={newest ? <VerdictBadge verdict={newest.verdict} /> : null}
        headline={newest?.scenario ?? "No finished runs"}
        detail={newest ? formatDateTime(newest.finished_at) : "Run litetraffic verify to start"}
      />
    </section>
  );
}
