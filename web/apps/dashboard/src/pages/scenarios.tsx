import { Link, useParams } from "react-router";
import { Area, AreaChart, CartesianGrid, XAxis, YAxis } from "recharts";
import { api, ApiError } from "@/api/client";
import type { TrendPoint } from "@/api/types";
import { Page } from "@/components/page";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig } from "@/components/ui/chart";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { VerdictBadge, VerdictStrip, verdictLabel } from "@/components/verdict";
import { formatDateTime, formatMs, formatRelative } from "@/lib/format";
import { summarizeScenarios } from "@/lib/runs";
import { useApi } from "@/lib/use-api";


export function ScenariosPage() {
  const { data, error, reload } = useApi("scenarios", async (signal) => {
    const [runs, scenarios] = await Promise.all([api.runs({}, signal), api.scenarios(signal)]);
    return summarizeScenarios(runs, scenarios);
  });

  return (
    <Page title="Scenarios" crumbs={[{ label: "Scenarios" }]} description="Each scenario that has run here, with its latest verdict and recent history.">
      {error && !data ? (
        <ErrorState error={error} onRetry={reload} />
      ) : !data ? (
        <LoadingState label="Loading scenarios" />
      ) : !data.length ? (
        <EmptyState title="No scenarios yet">Scenarios appear once a run of them lands in the runs folder.</EmptyState>
      ) : (
        <div className="rounded-xl border bg-card">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Scenario</TableHead>
                <TableHead>Latest</TableHead>
                <TableHead>History</TableHead>
                <TableHead className="text-right">Runs</TableHead>
                <TableHead>Last run</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.map((scenario) => (
                <TableRow key={scenario.name}>
                  <TableCell className="font-medium">
                    <Link to={`/scenarios/${encodeURIComponent(scenario.name)}`} className="hover:underline focus-visible:underline">
                      {scenario.name}
                    </Link>
                  </TableCell>
                  <TableCell>{scenario.latest ? <VerdictBadge verdict={scenario.latest.verdict} /> : "Never run"}</TableCell>
                  <TableCell>
                    <VerdictStrip runs={scenario.runs} max={12} />
                  </TableCell>
                  <TableCell className="text-right tabular-nums">{scenario.runs.length}</TableCell>
                  <TableCell className="text-muted-foreground">{formatRelative(scenario.latest?.finished_at)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </Page>
  );
}

const chartConfig = { p95: { label: "p95", color: "var(--primary)" } } satisfies ChartConfig;

interface DotProps {
  cx?: number;
  cy?: number;
  index?: number;
  payload?: TrendPoint;
}

function VerdictDot({ cx, cy, index, payload }: DotProps) {
  if (cx == null || cy == null || payload?.p95 == null) return <g key={index} />;
  return (
    <circle
      key={index}
      cx={cx}
      cy={cy}
      r={4}
      // Shape, not color: passing runs are solid dots, every other verdict a hollow ring.
      fill={payload.verdict === "pass" ? "var(--color-p95)" : "var(--card)"}
      stroke="var(--color-p95)"
      strokeWidth={2}
    />
  );
}

const shortTime = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });

export function ScenarioPage() {
  const name = useParams().name ?? "";
  const { data, error, reload } = useApi(`trend:${name}`, (signal) => api.trend(name, signal));
  const points = (data ?? []).map((point) => ({
    ...point,
    label: point.finished_at ? shortTime.format(new Date(point.finished_at)) : point.run_id,
  }));

  return (
    <Page
      title={name}
      crumbs={[{ label: "Scenarios", to: "/scenarios" }, { label: name }]}
      description="p95 latency of every run of this scenario over time. Solid dots passed, hollow ones did not; the table below has the same data."
      actions={
        <Button asChild variant="outline">
          <Link to={`/runs?scenario=${encodeURIComponent(name)}`}>View runs</Link>
        </Button>
      }
    >
      {error instanceof ApiError && error.status === 404 ? (
        <EmptyState title="Scenario not found">No run of {name} exists in the runs folder.</EmptyState>
      ) : error ? (
        <ErrorState error={error} onRetry={reload} />
      ) : !data ? (
        <LoadingState label="Loading trend" />
      ) : !data.length ? (
        <EmptyState title="No finished runs yet">The trend starts with this scenario's first finished run.</EmptyState>
      ) : (
        <>
          <Card>
            <CardHeader>
              <CardTitle>p95 latency per run</CardTitle>
              <CardDescription>{data.length} runs, oldest on the left.</CardDescription>
            </CardHeader>
            <CardContent>
              <ChartContainer config={chartConfig} className="aspect-auto h-64 w-full" aria-hidden>
                <AreaChart data={points} margin={{ left: 4, right: 12, top: 8 }}>
                  <defs>
                    <linearGradient id="fillP95" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="var(--color-p95)" stopOpacity={0.8} />
                      <stop offset="95%" stopColor="var(--color-p95)" stopOpacity={0.1} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid vertical={false} />
                  <XAxis dataKey="label" tickLine={false} axisLine={false} tickMargin={8} minTickGap={24} />
                  <YAxis tickLine={false} axisLine={false} width={48} tickFormatter={(value: number) => `${value} ms`} />
                  <ChartTooltip
                    content={
                      <ChartTooltipContent
                        labelFormatter={(_, payload) => {
                          const point = payload?.[0]?.payload as TrendPoint | undefined;
                          return point ? `${verdictLabel(point.verdict)}, ${point.run_id}` : "";
                        }}
                      />
                    }
                  />
                  <Area dataKey="p95" type="monotone" fill="url(#fillP95)" stroke="var(--color-p95)" strokeWidth={2} dot={VerdictDot} activeDot={{ r: 6 }} isAnimationActive={false} />
                </AreaChart>
              </ChartContainer>
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle>Data</CardTitle>
            </CardHeader>
            <CardContent>
              <Table aria-label="Trend data">
                <TableHeader>
                  <TableRow>
                    <TableHead>Verdict</TableHead>
                    <TableHead className="text-right">p95</TableHead>
                    <TableHead>Run</TableHead>
                    <TableHead>Finished</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {[...data].reverse().map((point) => (
                    <TableRow key={point.run_id}>
                      <TableCell>
                        <VerdictBadge verdict={point.verdict} />
                      </TableCell>
                      <TableCell className="text-right tabular-nums">{formatMs(point.p95)}</TableCell>
                      <TableCell className="font-mono text-xs">
                        <Link to={`/runs/${encodeURIComponent(point.run_id)}`} className="hover:underline focus-visible:underline">
                          {point.run_id}
                        </Link>
                      </TableCell>
                      <TableCell className="text-muted-foreground">{formatDateTime(point.finished_at)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        </>
      )}
    </Page>
  );
}
