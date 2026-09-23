import { useMemo, useState } from "react";
import { Area, AreaChart, CartesianGrid, XAxis } from "recharts";
import type { RunListEntry } from "@/api/types";
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig } from "@/components/ui/chart";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { JUDGED } from "@/lib/runs";

const HOUR = 3_600_000;
const DAY = 24 * HOUR;

export const RANGES = {
  "24h": { label: "Last 24 hours", short: "24 hours", span: DAY, step: HOUR },
  "7d": { label: "Last 7 days", short: "7 days", span: 7 * DAY, step: DAY },
  "30d": { label: "Last 30 days", short: "30 days", span: 30 * DAY, step: DAY },
} as const;
export type Range = keyof typeof RANGES;

const chartConfig = {
  passed: { label: "Passed", color: "var(--primary)" },
  notPassed: { label: "Did not pass", color: "var(--muted-foreground)" },
} satisfies ChartConfig;

const hourFormat = new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit" });
const dayFormat = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" });

export interface Bucket {
  start: number;
  passed: number;
  notPassed: number;
}

/** Judged verify runs counted per hour or day, ending at the newest run (local data can be old). */
// ponytail: buckets align to UTC hours/days; align to local midnight if day labels near midnight confuse anyone.
export function bucketRuns(runs: RunListEntry[], range: Range): Bucket[] {
  const { span, step } = RANGES[range];
  const judged = runs.filter((run) => run.kind === "run" && run.finished_at && JUDGED.has(run.verdict));
  if (!judged.length) return [];
  const newest = Math.max(...judged.map((run) => Date.parse(run.finished_at!)));
  const end = Math.floor(newest / step) * step + step;
  const buckets: Bucket[] = Array.from({ length: span / step }, (_, index) => ({
    start: end - span + index * step,
    passed: 0,
    notPassed: 0,
  }));
  for (const run of judged) {
    const bucket = buckets[Math.floor((Date.parse(run.finished_at!) - (end - span)) / step)];
    if (!bucket) continue;
    if (run.verdict === "pass") bucket.passed += 1;
    else bucket.notPassed += 1;
  }
  return buckets;
}

/** dashboard-01 ChartAreaInteractive: pass / did-not-pass runs over time with a range toggle. */
export function ChartAreaInteractive({ runs }: { runs: RunListEntry[] }) {
  const [range, setRange] = useState<Range>("24h");
  const data = useMemo(() => bucketRuns(runs, range), [runs, range]);
  const passed = data.reduce((sum, bucket) => sum + bucket.passed, 0);
  const total = passed + data.reduce((sum, bucket) => sum + bucket.notPassed, 0);
  const format = RANGES[range].step === HOUR ? hourFormat : dayFormat;
  const pick = (value: string) => value && setRange(value as Range);

  return (
    <Card className="@container/card">
      <CardHeader>
        <CardTitle>Runs over time</CardTitle>
        <CardDescription>
          <span className="hidden @[540px]/card:block">
            {total} judged runs in the {RANGES[range].short} before the last run, {passed} passed
          </span>
          <span className="@[540px]/card:hidden">{RANGES[range].label}</span>
        </CardDescription>
        <CardAction>
          <ToggleGroup
            type="single"
            value={range}
            onValueChange={pick}
            variant="outline"
            aria-label="Time range"
            className="hidden *:data-[slot=toggle-group-item]:px-4! @[767px]/card:flex"
          >
            {(Object.keys(RANGES) as Range[]).map((key) => (
              <ToggleGroupItem key={key} value={key}>
                {RANGES[key].label}
              </ToggleGroupItem>
            ))}
          </ToggleGroup>
          <Select value={range} onValueChange={pick}>
            <SelectTrigger
              size="sm"
              aria-label="Time range"
              className="flex w-40 **:data-[slot=select-value]:block **:data-[slot=select-value]:truncate @[767px]/card:hidden"
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent className="rounded-xl">
              {(Object.keys(RANGES) as Range[]).map((key) => (
                <SelectItem key={key} value={key} className="rounded-lg">
                  {RANGES[key].label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </CardAction>
      </CardHeader>
      <CardContent className="px-2 pt-4 sm:px-6 sm:pt-6">
        <ChartContainer
          config={chartConfig}
          className="aspect-auto h-[250px] w-full"
          role="img"
          aria-label={`${RANGES[range].label}: ${total} judged runs, ${passed} passed, ${total - passed} did not pass`}
        >
          <AreaChart data={data}>
            <defs>
              <linearGradient id="fillPassed" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="var(--color-passed)" stopOpacity={1.0} />
                <stop offset="95%" stopColor="var(--color-passed)" stopOpacity={0.1} />
              </linearGradient>
              <linearGradient id="fillNotPassed" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="var(--color-notPassed)" stopOpacity={0.8} />
                <stop offset="95%" stopColor="var(--color-notPassed)" stopOpacity={0.1} />
              </linearGradient>
            </defs>
            <CartesianGrid vertical={false} />
            <XAxis
              dataKey="start"
              tickLine={false}
              axisLine={false}
              tickMargin={8}
              minTickGap={32}
              tickFormatter={(value: number) => format.format(value)}
            />
            <ChartTooltip
              cursor={false}
              content={<ChartTooltipContent labelFormatter={(_, payload) => format.format(Number(payload?.[0]?.payload?.start))} indicator="dot" />}
            />
            <Area dataKey="notPassed" type="monotone" fill="url(#fillNotPassed)" stroke="var(--color-notPassed)" stackId="a" isAnimationActive={false} />
            <Area dataKey="passed" type="monotone" fill="url(#fillPassed)" stroke="var(--color-passed)" stackId="a" isAnimationActive={false} />
          </AreaChart>
        </ChartContainer>
      </CardContent>
    </Card>
  );
}
