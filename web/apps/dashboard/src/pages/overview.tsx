import { Link } from "react-router";
import { api } from "@/api/client";
import type { RunListEntry, ScenarioList } from "@/api/types";
import { Page } from "@/components/page";
import { ChartAreaInteractive } from "@/components/chart-area-interactive";
import { SectionCards } from "@/components/section-cards";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { Button } from "@/components/ui/button";
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { VerdictBadge, VerdictStrip } from "@/components/verdict";
import { formatRelative } from "@/lib/format";
import { JUDGED, oldestFirst, summarizeScenarios } from "@/lib/runs";
import { useApi } from "@/lib/use-api";

export function OverviewPage() {
  const { data, error, reload } = useApi("overview", async (signal) => {
    const [runs, scenarios] = await Promise.all([api.runs({}, signal), api.scenarios(signal)]);
    return { runs, scenarios };
  });

  return (
    <Page
      title="Overview"
      crumbs={[{ label: "Overview" }]}
      description="Where every scenario stands, from the runs LiteTraffic wrote to this machine."
    >
      {data ? <Overview runs={data.runs} scenarios={data.scenarios} /> : error ? <ErrorState error={error} onRetry={reload} /> : <LoadingState label="Loading overview" />}
    </Page>
  );
}

function Overview({ runs, scenarios }: { runs: RunListEntry[]; scenarios: ScenarioList }) {
  const verifyRuns = runs.filter((run) => run.kind === "run");
  if (!runs.length) {
    return (
      <EmptyState title="No runs yet">
        Run <code className="font-mono">litetraffic verify scenario.yaml</code> and its evidence shows up here.
      </EmptyState>
    );
  }

  const summaries = summarizeScenarios(runs, scenarios);
  const problems = oldestFirst(verifyRuns.filter((run) => run.verdict !== "pass" && JUDGED.has(run.verdict)))
    .reverse()
    .slice(0, 5);

  return (
    <>
      <SectionCards runs={runs} summaries={summaries} />
      <ChartAreaInteractive runs={runs} />

      <div className="grid gap-4 md:gap-6 @5xl/main:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <Card>
          <CardHeader>
            <CardTitle>Scenarios</CardTitle>
            <CardDescription>Latest verdict and recent history, oldest bar first.</CardDescription>
            <CardAction>
              <Button asChild variant="ghost" size="sm">
                <Link to="/scenarios">All scenarios</Link>
              </Button>
            </CardAction>
          </CardHeader>
          <CardContent>
            <ul className="divide-y" aria-label="Scenario status">
              {summaries.map((scenario) => (
                <li key={scenario.name} className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 gap-y-2 py-3 first:pt-0 last:pb-0 sm:grid-cols-[minmax(0,1fr)_auto_6.5rem]">
                  <div className="min-w-0">
                    <Link
                      to={`/scenarios/${encodeURIComponent(scenario.name)}`}
                      className="block truncate font-medium hover:underline focus-visible:underline"
                    >
                      {scenario.name}
                    </Link>
                    <p className="text-xs text-muted-foreground">
                      {scenario.latest ? `Last run ${formatRelative(scenario.latest.finished_at)}` : "No runs yet"}
                    </p>
                  </div>
                  <div className="order-last col-span-2 sm:order-none sm:col-span-1">
                    <VerdictStrip runs={scenario.runs} max={12} />
                  </div>
                  <div className="justify-self-end">
                    {scenario.latest ? <VerdictBadge verdict={scenario.latest.verdict} /> : <span className="text-xs text-muted-foreground">Never run</span>}
                  </div>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Needs attention</CardTitle>
            <CardDescription>Most recent runs that did not pass.</CardDescription>
          </CardHeader>
          <CardContent>
            {problems.length ? (
              <ul className="divide-y" aria-label="Recent failures">
                {problems.map((run) => (
                  <li key={run.run_id} className="py-3 first:pt-0 last:pb-0">
                    <Link to={`/runs/${encodeURIComponent(run.run_id)}`} className="group grid gap-1 rounded-md">
                      <span className="flex items-center justify-between gap-2">
                        <span className="truncate font-medium group-hover:underline group-focus-visible:underline">{run.scenario}</span>
                        <VerdictBadge verdict={run.verdict} />
                      </span>
                      <span className="flex justify-between gap-2 text-xs text-muted-foreground">
                        <span className="truncate font-mono">{run.run_id}</span>
                        <span className="shrink-0">{formatRelative(run.finished_at)}</span>
                      </span>
                    </Link>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-muted-foreground">Every judged run passed.</p>
            )}
          </CardContent>
        </Card>
      </div>
    </>
  );
}
