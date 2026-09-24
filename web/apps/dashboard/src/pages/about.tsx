import type { ReactNode } from "react";
import { api } from "@/api/client";
import { Page } from "@/components/page";
import { VERDICT_MEANING, VerdictBadge } from "@/components/verdict";
import { useApi } from "@/lib/use-api";

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="grid gap-2 border-t py-6 md:grid-cols-[12rem_1fr] md:gap-8">
      <h2 className="font-heading text-lg font-semibold">{title}</h2>
      <div className="max-w-prose space-y-3 text-sm leading-relaxed">{children}</div>
    </section>
  );
}

export function AboutPage() {
  const meta = useApi("meta", (signal) => api.meta(signal));
  return (
    <Page
      title="What is this dashboard?"
      crumbs={[{ label: "About" }]}
      description={
        <>
          A local, read-only view of the results LiteTraffic writes to disk. Every <code>litetraffic verify</code> run,{" "}
          <code>--repeat</code> series and <code>up</code> background activity in the runs folder shows up here; nothing is sent
          anywhere and nothing is changed.
        </>
      }
    >
      <div>
        <p className="pb-6 text-sm text-muted-foreground">
          Runs folder: <code className="font-mono text-foreground">{meta.data?.runs_dir ?? "loading"}</code>
        </p>
        <Section title="Runs">
          <p>Newest first. Each run has a verdict:</p>
          <dl className="grid grid-cols-[max-content_1fr] gap-x-3 gap-y-2">
            {(Object.keys(VERDICT_MEANING) as (keyof typeof VERDICT_MEANING)[]).map((verdict) => (
              <div key={verdict} className="col-span-2 grid grid-cols-subgrid items-baseline">
                <dt>
                  <VerdictBadge verdict={verdict} />
                </dt>
                <dd>{VERDICT_MEANING[verdict]}</dd>
              </div>
            ))}
          </dl>
          <p>Filter by scenario, verdict or seed; the list refreshes every 5 seconds.</p>
        </Section>
        <Section title="Run page">
          <p>
            Open a run to see its assertions, the first failing samples with expected vs actual values, final observations,
            per-operation latency, the client-side overlap check, limitations, and links to <code>report.html</code> and every
            artifact.
          </p>
        </Section>
        <Section title="Compare">
          <p>
            Tick exactly two runs on the Runs page and press Compare. The older run is the baseline. Compare shows whether
            correctness regressed, whether the runs are comparable (same scenario digest, seed, engine and schedule), and the p95
            latency change.
          </p>
        </Section>
        <Section title="Scenario trends">
          <p>Each scenario opens a trend chart of p95 latency per run over time, with verdict markers and the data as a table.</p>
        </Section>
        <Section title="Safety">
          <p>
            The dashboard listens on 127.0.0.1 only, answers only requests addressed to localhost for its own port, never follows
            symlinks out of the runs folder, and serves artifacts under a restrictive content security policy.
          </p>
        </Section>
      </div>
    </Page>
  );
}
