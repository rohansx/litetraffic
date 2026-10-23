import { useEffect, useState, type ReactNode } from "react";
import { ExternalLink, Play, Sparkles, Square, TriangleAlert } from "lucide-react";
import { Link, useParams } from "react-router";
import { api, ApiError } from "@/api/client";
import type { Job, JobState } from "@/api/types";
import { DraftResultView } from "@/components/draft-result";
import { Facts } from "@/components/facts";
import { JobLog } from "@/components/job-log";
import { Page } from "@/components/page";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Signal, VERDICT_MEANING, verdictLabel } from "@/components/verdict";
import { formatDateTime } from "@/lib/format";
import { formatElapsed, useJob } from "@/lib/use-job";
import { cn } from "@/lib/utils";
import { runLink } from "@/pages/library";

const STATE: Record<JobState, { label: string; badge: string }> = {
  running: { label: "Running", badge: "bg-secondary text-secondary-foreground" },
  finished: { label: "Finished", badge: "bg-pass-muted text-pass" },
  failed: { label: "Failed", badge: "bg-error-muted text-error" },
  cancelled: { label: "Cancelled", badge: "bg-muted text-muted-foreground" },
};

/** What the CLI's exit code means (verify: 0 pass, 1 fail, 2 inconclusive, 3 error; 130 interrupted). */
export function exitMeaning(code: number | null): string {
  switch (code) {
    case 0:
      return "Exit 0: passed";
    case 1:
      return "Exit 1: failed";
    case 2:
      return "Exit 2: inconclusive";
    case 3:
      return "Exit 3: error";
    case 130:
      return "Exit 130: cancelled";
    case null:
      return "No exit code yet";
    default:
      return `Exit ${code}`;
  }
}

export function JobStateBadge({ state }: { state: JobState }) {
  const { label, badge } = STATE[state];
  return (
    <Badge className={cn("gap-1.5 font-mono", badge)}>
      {state === "running" && <span aria-hidden className="size-1.5 animate-pulse rounded-full bg-current motion-reduce:animate-none" />}
      {label}
    </Badge>
  );
}

/** Ticks once a second while the job runs; frozen at the finish time afterwards. */
function Elapsed({ job }: { job: Job }) {
  const running = job.state === "running";
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [running]);
  const start = Date.parse(job.started_at);
  const end = running ? now : Date.parse(job.finished_at ?? job.started_at);
  return <span className="tabular-nums">{Number.isNaN(start) ? "n/a" : formatElapsed((end - start) / 1000)}</span>;
}

export function JobPage() {
  const id = useParams().id ?? "";
  const { job, error } = useJob(id);
  const crumbs = [{ label: "Jobs" }, { label: id }];

  if (!job) {
    return (
      <Page title={<span className="font-mono text-xl">{id}</span>} documentTitle={id} crumbs={crumbs}>
        {error instanceof ApiError && error.status === 404 ? (
          <EmptyState title="Job not found">No job named {id} exists. Jobs are kept in memory while the console runs.</EmptyState>
        ) : error ? (
          <ErrorState error={error} />
        ) : (
          <LoadingState label="Loading job" />
        )}
      </Page>
    );
  }
  return <JobView job={job} crumbs={crumbs} pollError={error} />;
}

function JobView({ job, crumbs, pollError }: { job: Job; crumbs: { label: string }[]; pollError?: Error }) {
  const running = job.state === "running";
  const isDraft = job.kind === "draft";
  const title = isDraft ? `Draft ${job.result?.name ?? ""}`.trim() : (job.scenario ?? job.id);

  return (
    <Page
      title={title}
      crumbs={crumbs}
      description={<span className="font-mono text-xs">{job.id}</span>}
      actions={
        <>
          <JobStateBadge state={job.state} />
          {running && <CancelButton id={job.id} />}
          {job.run_id && running && (
            <Button asChild variant="outline">
              <Link to={`/runs/${encodeURIComponent(job.run_id)}`}>
                <ExternalLink aria-hidden />
                Open run evidence
              </Link>
            </Button>
          )}
        </>
      }
    >
      {pollError && (
        <Alert variant="destructive">
          <TriangleAlert aria-hidden />
          <AlertTitle>Lost contact with the console</AlertTitle>
          <AlertDescription>{pollError.message}</AlertDescription>
        </Alert>
      )}
      {!running && !isDraft && <Outcome job={job} />}
      <Card>
        <CardHeader>
          <CardTitle>{isDraft ? "Draft" : "Run"} details</CardTitle>
        </CardHeader>
        <CardContent>
          <Facts
            items={[
              ["State", STATE[job.state].label],
              ["Elapsed", <Elapsed key="elapsed" job={job} />],
              ["Started", formatDateTime(job.started_at)],
              ...(isDraft
                ? []
                : ([
                    ["Target", <span key="t" className="font-mono">{job.target ?? "n/a"}</span>],
                    ["Seed", String(job.seed ?? "n/a")],
                    ["Repeat", String(job.repeat ?? 1)],
                    [
                      "Secrets provided",
                      job.env_names.length ? <span key="e" className="font-mono">{job.env_names.join(", ")}</span> : "None",
                    ],
                  ] as [string, ReactNode][])),
            ]}
          />
        </CardContent>
      </Card>
      <section aria-label="Output" className="grid gap-2">
        <h2 className="text-lg font-semibold">Log</h2>
        <JobLog lines={job.log} label={isDraft ? "Draft log" : "Run log"} />
        <p className="text-xs text-muted-foreground">Shows the last 300 lines.</p>
      </section>
      {isDraft && job.result && (
        <section aria-label="Draft result" className="grid gap-3">
          <h2 className="text-lg font-semibold">Result</h2>
          <DraftResultView result={job.result} />
          <p className="text-sm text-muted-foreground">
            To save a draft to your library, start it from <Link to="/new" className="underline">New test</Link>.
          </p>
        </section>
      )}
    </Page>
  );
}

function CancelButton({ id }: { id: string }) {
  const [state, setState] = useState<"idle" | "busy" | "sent">("idle");
  const [error, setError] = useState<Error>();
  async function cancel() {
    setState("busy");
    setError(undefined);
    try {
      await api.cancelJob(id);
      setState("sent");
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error(String(caught)));
      setState("idle");
    }
  }
  return (
    <>
      <Button variant="outline" onClick={cancel} disabled={state !== "idle"}>
        <Square aria-hidden />
        {state === "sent" ? "Cancelling" : "Cancel"}
      </Button>
      {error && (
        <span role="alert" className="text-sm text-destructive">
          {error.message}
        </span>
      )}
    </>
  );
}

function Outcome({ job }: { job: Job }) {
  const cancelled = job.state === "cancelled" || job.exit_code === 130;
  const verdict = job.verdict;
  return (
    <Card role="region" aria-label="Outcome" className={cn(verdict && "border-2")}>
      <CardHeader>
        <CardDescription className="font-mono text-xs">{exitMeaning(job.exit_code)}</CardDescription>
        <CardTitle className="flex flex-wrap items-center gap-3 text-2xl md:text-3xl">
          {cancelled ? (
            "Cancelled"
          ) : verdict ? (
            <>
              <Signal verdict={verdict} className="[--signal-dot:1rem]" />
              {verdictLabel(verdict)}
            </>
          ) : (
            "The run did not finish"
          )}
        </CardTitle>
      </CardHeader>
      <CardContent className="grid gap-4">
        <p className="text-sm text-muted-foreground">
          {cancelled
            ? "You stopped this run before it finished. Evidence written so far may be incomplete."
            : verdict
              ? VERDICT_MEANING[verdict]
              : "It ended without a verdict. Read the log below for the reason."}
        </p>
        <div className="flex flex-wrap gap-2">
          {job.run_id && (
            <Button asChild>
              <Link to={`/runs/${encodeURIComponent(job.run_id)}`}>
                <ExternalLink aria-hidden />
                Open full run
              </Link>
            </Button>
          )}
          {job.scenario && (
            <Button asChild variant="outline">
              <Link to={runLink(job.scenario)}>
                <Play aria-hidden />
                Run again
              </Link>
            </Button>
          )}
          {job.run_id && (
            <Button asChild variant="outline">
              <Link to={`/runs/${encodeURIComponent(job.run_id)}`}>
                <Sparkles aria-hidden />
                Explain with AI
              </Link>
            </Button>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
