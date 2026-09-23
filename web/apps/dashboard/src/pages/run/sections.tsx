import { ExternalLink, FileText, TriangleAlert } from "lucide-react";
import { artifactUrl } from "@/api/client";
import type { ArtifactFile, Fixture, Observation, RunMetrics, RunResult } from "@/api/types";
import { Facts, Value } from "@/components/facts";
import { EmptyState } from "@/components/states";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { VerdictBadge } from "@/components/verdict";
import { formatBytes, formatMs, formatNumber, formatRate } from "@/lib/format";

const title = "font-display text-lg font-semibold";

export function Observations({ observation, fixture }: { observation?: Observation; fixture?: Fixture }) {
  const records = observation ? (Array.isArray(observation) ? observation : [observation]) : [];
  return (
    <div className="grid grid-cols-1 gap-4">
      {records.length ? (
        records.map((record, index) => (
          <Card key={`${record.assertion}-${index}`}>
            <CardHeader>
              <CardTitle className="flex flex-wrap items-center gap-2 font-mono text-sm">
                {record.assertion}
                <VerdictBadge verdict={record.status} />
              </CardTitle>
              <CardDescription>
                {record.reason ?? "Final state read from the target after traffic stopped."}
                {record.attempts != null && ` ${record.attempts} attempts.`}
              </CardDescription>
            </CardHeader>
            <CardContent className="grid gap-3">
              {Object.entries(record.checks ?? {}).map(([name, check]) => (
                <div key={name} className="grid gap-2 md:grid-cols-2">
                  <div>
                    <p className="mb-1 text-xs text-muted-foreground">Expected {name ? <code>{name}</code> : "value"}</p>
                    <Value value={check.matcher} />
                  </div>
                  <div>
                    <p className="mb-1 flex items-center gap-2 text-xs text-muted-foreground">
                      Actual <VerdictBadge verdict={check.pass ? "pass" : "fail"} />
                    </p>
                    <Value value={check.actual} className={check.pass ? undefined : "text-fail"} />
                  </div>
                </div>
              ))}
              {!record.checks && (record.expected || record.actual) && (
                <div className="grid gap-2 md:grid-cols-2">
                  <Value value={record.expected} />
                  <Value value={record.actual} />
                </div>
              )}
            </CardContent>
          </Card>
        ))
      ) : (
        <EmptyState title="No observations">This scenario did not read final state from the target.</EmptyState>
      )}
      {fixture && <FixtureCard fixture={fixture} />}
    </div>
  );
}

function FixtureCard({ fixture }: { fixture: Fixture }) {
  const steps =
    "setup" in fixture
      ? [
          ["Setup", fixture.setup.status, fixture.setup.reason ?? fixture.setup.argv.join(" ")],
          ...(fixture.teardown ? [["Teardown", fixture.teardown.status, fixture.teardown.reason ?? fixture.teardown.argv.join(" ")]] : []),
        ]
      : [
          ["Create", fixture.create.status, fixture.create.reason ?? `${fixture.create.requests} requests`],
          ...(fixture.cleanup ? [["Cleanup", fixture.cleanup.status, fixture.cleanup.reason ?? `${fixture.cleanup.requests} requests`]] : []),
        ];
  return (
    <Card>
      <CardHeader>
        <CardTitle className={title}>Fixture</CardTitle>
        <CardDescription>State prepared before traffic and cleaned up after it.</CardDescription>
      </CardHeader>
      <CardContent>
        <ul className="grid gap-2">
          {steps.map(([step, status, detail]) => (
            <li key={step} className="grid gap-1 sm:grid-cols-[6rem_6rem_1fr] sm:items-baseline">
              <span className="font-medium">{step}</span>
              <span className={status === "error" ? "text-fail" : "text-muted-foreground"}>{status}</span>
              <code className="truncate font-mono text-xs text-muted-foreground" title={detail}>
                {detail}
              </code>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}

export function Metrics({ metrics }: { metrics: RunMetrics }) {
  if (!Object.keys(metrics).length) {
    return <EmptyState title="No metrics">The run stopped before the engine reported any.</EmptyState>;
  }
  const latency = metrics.http_req_duration_ms ?? {};
  const operations = [...new Set([...Object.keys(metrics.by_operation ?? {}), ...Object.keys(metrics.overlap ?? {})])].sort();
  return (
    <div className="grid grid-cols-1 gap-4">
      <Card>
        <CardHeader>
          <CardTitle className={title}>Latency and throughput</CardTitle>
          <CardDescription>All HTTP requests the engine sent, including observer requests.</CardDescription>
        </CardHeader>
        <CardContent>
          <Facts
            items={[
              ["p50", formatMs(latency.p50)],
              ["p95", formatMs(latency.p95)],
              ["Max", formatMs(latency.max)],
              ["Average", formatMs(latency.average)],
              ["HTTP requests", formatNumber(metrics.http_reqs)],
              ["Requests per second", formatNumber(metrics.http_reqs_per_second, 2)],
              ["Iterations", formatNumber(metrics.iterations)],
              ["Elapsed", metrics.elapsed_seconds == null ? "n/a" : `${metrics.elapsed_seconds.toFixed(1)} s`],
              ["HTTP failure rate", formatRate(metrics.http_req_failed_rate?.rate)],
              ["Unexpected failure rate", formatRate(metrics.unexpected_http_failure_rate?.rate)],
              ["Peak virtual users", formatNumber(metrics.vus_max)],
              ["Dropped iterations", formatNumber(metrics.dropped_iterations ?? 0)],
            ]}
          />
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle className={title}>Per operation</CardTitle>
          <CardDescription>Peak in flight is the client-side count of concurrent journeys for that operation.</CardDescription>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Operation</TableHead>
                <TableHead className="text-right">Samples</TableHead>
                <TableHead className="text-right">p95</TableHead>
                <TableHead className="text-right">Failed rate</TableHead>
                <TableHead className="text-right">Peak in flight</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {operations.map((name) => {
                const op = metrics.by_operation?.[name];
                return (
                  <TableRow key={name}>
                    <TableCell className="font-mono text-xs">{name}</TableCell>
                    <TableCell className="text-right tabular-nums">{formatNumber(op?.samples)}</TableCell>
                    <TableCell className="text-right tabular-nums">{formatMs(op?.p95)}</TableCell>
                    <TableCell className="text-right tabular-nums">{formatRate(op?.failed_rate)}</TableCell>
                    <TableCell className="text-right tabular-nums">{formatNumber(metrics.overlap?.[name])}</TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  );
}

export function Limitations({ result }: { result: RunResult }) {
  return (
    <div className="grid grid-cols-1 gap-4">
      {result.limitations.length ? (
        <ul className="grid gap-2" aria-label="Limitations">
          {result.limitations.map((text) => (
            <li key={text} className="flex gap-2 rounded-lg border border-inconclusive/40 bg-inconclusive-muted p-3 text-sm">
              <TriangleAlert className="mt-0.5 size-4 shrink-0 text-inconclusive" aria-hidden />
              {text}
            </li>
          ))}
        </ul>
      ) : (
        <EmptyState title="No limitations">Evidence was complete for every assertion.</EmptyState>
      )}
      {result.notes.length > 0 && (
        <div>
          <h2 className="mb-2 text-sm font-semibold">Notes</h2>
          <ul className="list-disc space-y-1 pl-5 text-sm text-muted-foreground">
            {result.notes.map((note) => (
              <li key={note}>{note}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

export function Artifacts({ runId, artifacts }: { runId: string; artifacts: ArtifactFile[] }) {
  if (!artifacts.length) return <EmptyState title="No artifact files" />;
  return (
    <div className="rounded-xl border bg-card">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>File</TableHead>
            <TableHead className="text-right">Size</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {artifacts.map((file) => (
            <TableRow key={file.path}>
              <TableCell>
                <a
                  href={artifactUrl(runId, file.path)}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-2 font-mono text-xs hover:underline focus-visible:underline"
                >
                  <FileText className="size-3.5 text-muted-foreground" aria-hidden />
                  {file.path}
                  <ExternalLink className="size-3 text-muted-foreground" aria-label="opens in a new tab" />
                </a>
              </TableCell>
              <TableCell className="text-right text-muted-foreground tabular-nums">{formatBytes(file.size)}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
