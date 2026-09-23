import { ArrowLeftRight, TriangleAlert } from "lucide-react";
import { Link, useSearchParams } from "react-router";
import { api, ApiError } from "@/api/client";
import type { CompatibilityField, Comparison, RunListEntry } from "@/api/types";
import { Facts } from "@/components/facts";
import { Page } from "@/components/page";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { VerdictBadge } from "@/components/verdict";
import { formatChange, formatMs, formatNumber, formatRate } from "@/lib/format";
import { useApi } from "@/lib/use-api";
import { cn } from "@/lib/utils";

const FIELD_LABELS: Record<CompatibilityField, string> = {
  scenario_sha256: "The scenario file changed between the runs",
  seed: "The runs used different seeds",
  engine: "The runs used different engine versions",
  resolved_schedule: "The traffic schedules differ",
};

const P95_LABELS: Record<Comparison["performance"]["p95"]["status"], string> = {
  reported: "Reported",
  unavailable: "Unavailable",
  inconclusive: "Inconclusive",
  regression: "Regression",
  within_limit: "Within limit",
  incomparable: "Not comparable",
};

const title = "font-display text-lg font-semibold";

export function ComparePage() {
  const [params, setParams] = useSearchParams();
  const baseline = params.get("baseline") ?? "";
  const candidate = params.get("candidate") ?? "";
  const runs = useApi("compare-runs", (signal) => api.runs({}, signal));

  function pick(key: "baseline" | "candidate", value: string) {
    const next = new URLSearchParams(params);
    next.set(key, value);
    setParams(next);
  }

  return (
    <Page
      title="Compare runs"
      crumbs={[{ label: "Compare" }]}
      description="The baseline is the older run. Compare checks whether correctness regressed and how p95 latency moved."
    >
      <div className="flex flex-wrap items-end gap-2">
        <RunPicker label="Baseline" value={baseline} runs={runs.data} onChange={(value) => pick("baseline", value)} />
        <Button
          variant="ghost"
          size="icon"
          aria-label="Swap baseline and candidate"
          disabled={!baseline || !candidate}
          onClick={() => setParams({ baseline: candidate, candidate: baseline })}
        >
          <ArrowLeftRight aria-hidden />
        </Button>
        <RunPicker label="Candidate" value={candidate} runs={runs.data} onChange={(value) => pick("candidate", value)} />
      </div>
      {baseline && candidate ? (
        <ComparisonResult baseline={baseline} candidate={candidate} />
      ) : (
        <EmptyState
          title="Pick two runs to compare"
          action={
            <Button asChild variant="outline">
              <Link to="/runs">Choose on the Runs page</Link>
            </Button>
          }
        >
          Choose a baseline and a candidate above, or tick two runs on the Runs page.
        </EmptyState>
      )}
    </Page>
  );
}

function RunPicker({ label, value, runs, onChange }: { label: string; value: string; runs?: RunListEntry[]; onChange: (value: string) => void }) {
  const options = (runs ?? []).filter((run) => run.kind === "run");
  if (value && !options.some((run) => run.run_id === value)) options.unshift({ run_id: value, scenario: null } as RunListEntry);
  return (
    <div className="grid w-full gap-1 sm:w-80">
      <span className="text-xs text-muted-foreground">{label}</span>
      <Select value={value || undefined} onValueChange={onChange}>
        <SelectTrigger aria-label={label} className="w-full">
          <SelectValue placeholder={`Choose a ${label.toLowerCase()} run`} />
        </SelectTrigger>
        <SelectContent>
          {options.map((run) => (
            <SelectItem key={run.run_id} value={run.run_id}>
              <span className="font-mono text-xs">{run.run_id}</span>
              {run.scenario && <span className="text-muted-foreground">{run.scenario}</span>}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}

function ComparisonResult({ baseline, candidate }: { baseline: string; candidate: string }) {
  const { data, error, reload } = useApi(`diff:${baseline}:${candidate}`, (signal) => api.diff(baseline, candidate, signal));
  if (error instanceof ApiError && error.status === 409) {
    return (
      <div role="alert" className="rounded-xl border border-inconclusive/40 bg-inconclusive-muted p-5">
        <p className="font-semibold">These runs cannot be compared</p>
        <p className="mt-1 text-sm text-muted-foreground">{error.message}</p>
      </div>
    );
  }
  if (error) return <ErrorState error={error} onRetry={reload} />;
  if (!data) return <LoadingState label="Comparing runs" />;
  return <ComparisonView diff={data} />;
}

function Change({ value, unit = "%", worseWhenUp = true }: { value: number | null; unit?: string; worseWhenUp?: boolean }) {
  const worse = value != null && value !== 0 && value > 0 === worseWhenUp;
  return <span className={cn("tabular-nums", worse && "text-fail", value != null && value !== 0 && !worse && "text-pass")}>{formatChange(value, unit)}</span>;
}

function ComparisonView({ diff }: { diff: Comparison }) {
  const { correctness, performance } = diff;
  const p95 = performance.p95;
  const operations = Object.entries(performance.by_operation).sort(([a], [b]) => a.localeCompare(b));

  return (
    <>
      <Card>
        <CardHeader>
          <CardTitle className={cn(title, "flex flex-wrap items-center gap-3")}>
            Result <VerdictBadge verdict={diff.verdict} className="h-6 px-2.5 text-sm" />
          </CardTitle>
          <CardDescription>
            <span className="font-mono">{diff.baseline_run_id}</span> as baseline, <span className="font-mono">{diff.candidate_run_id}</span> as candidate.
          </CardDescription>
        </CardHeader>
        {diff.reasons.length > 0 && (
          <CardContent>
            <ul className="list-disc space-y-1 pl-5 text-sm">
              {diff.reasons.map((reason) => (
                <li key={reason}>{reason}</li>
              ))}
            </ul>
          </CardContent>
        )}
      </Card>

      {!diff.comparable && (
        <div role="alert" className="flex gap-3 rounded-xl border border-inconclusive/40 bg-inconclusive-muted p-4">
          <TriangleAlert className="mt-0.5 size-4 shrink-0 text-inconclusive" aria-hidden />
          <div className="text-sm">
            <p className="font-semibold">Not comparable, so performance is not judged</p>
            <ul className="mt-1 list-disc pl-5">
              {diff.incompatibilities.map((field) => (
                <li key={field}>{FIELD_LABELS[field] ?? field}</li>
              ))}
            </ul>
          </div>
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className={title}>Correctness</CardTitle>
            <CardDescription>{correctness.regression ? "The candidate regressed." : "No correctness regression."}</CardDescription>
          </CardHeader>
          <CardContent className="grid gap-4">
            <div className="flex flex-wrap items-center gap-3 text-sm">
              <span className="text-muted-foreground">Baseline</span>
              <VerdictBadge verdict={correctness.baseline_verdict} />
              <span className="text-muted-foreground">Candidate</span>
              <VerdictBadge verdict={correctness.candidate_verdict} />
            </div>
            {correctness.assertion_regressions.length ? (
              <div>
                <p className="mb-1 text-sm font-medium text-fail">Assertions that newly fail</p>
                <ul className="grid gap-1" aria-label="Assertion regressions">
                  {correctness.assertion_regressions.map((id) => (
                    <li key={id} className="font-mono text-xs">
                      {id}
                    </li>
                  ))}
                </ul>
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">No assertion regressed.</p>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className={title}>p95 latency</CardTitle>
            <CardDescription>
              {P95_LABELS[p95.status]}
              {p95.threshold_percent != null && `, limit ${p95.threshold_percent}%`}
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Facts
              className="sm:grid-cols-3 lg:grid-cols-3"
              items={[
                ["Baseline", formatMs(p95.baseline_ms)],
                ["Candidate", formatMs(p95.candidate_ms)],
                ["Change", <Change value={p95.change_percent} />],
                ["HTTP error rate", `${formatRate(performance.http_error_rate.baseline)} to ${formatRate(performance.http_error_rate.candidate)}`],
                ["Requests per second", `${formatNumber(performance.http_reqs_per_second.baseline, 1)} to ${formatNumber(performance.http_reqs_per_second.candidate, 1)}`],
                ["Samples", `${p95.samples.baseline} and ${p95.samples.candidate}`],
              ]}
            />
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className={title}>p95 per operation</CardTitle>
        </CardHeader>
        <CardContent>
          {operations.length ? (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Operation</TableHead>
                  <TableHead className="text-right">Baseline</TableHead>
                  <TableHead className="text-right">Candidate</TableHead>
                  <TableHead className="text-right">Change</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {operations.map(([name, op]) => (
                  <TableRow key={name}>
                    <TableCell className="font-mono text-xs">{name}</TableCell>
                    <TableCell className="text-right tabular-nums">{formatMs(op.baseline_p95_ms)}</TableCell>
                    <TableCell className="text-right tabular-nums">{formatMs(op.candidate_p95_ms)}</TableCell>
                    <TableCell className="text-right">
                      <Change value={op.change_percent} />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          ) : (
            <p className="text-sm text-muted-foreground">Neither run reported per-operation latency.</p>
          )}
        </CardContent>
      </Card>
    </>
  );
}
