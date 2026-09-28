import { FileBarChart } from "lucide-react";
import { useParams } from "react-router";
import { api, ApiError, artifactUrl } from "@/api/client";
import type { RunDetail } from "@/api/types";
import { Facts } from "@/components/facts";
import { Page } from "@/components/page";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { VERDICT_MEANING, VerdictBadge } from "@/components/verdict";
import { formatDateTime } from "@/lib/format";
import { useApi } from "@/lib/use-api";
import { ActivityView } from "@/pages/run/activity";
import { AssertionsTable } from "@/pages/run/assertions";
import { Journeys } from "@/pages/run/journeys";
import { ExplanationCard } from "@/pages/run/explanation";
import { Artifacts, Limitations, Metrics, Observations } from "@/pages/run/sections";

export function RunDetailPage() {
  const id = useParams().id ?? "";
  const { data, error, reload } = useApi(`run:${id}`, (signal) => api.run(id, signal));
  const crumbs = [{ label: "Runs", to: "/runs" }, { label: id }];

  if (!data) {
    return (
      <Page title={<span className="font-mono text-xl">{id}</span>} documentTitle={id} crumbs={crumbs}>
        {error instanceof ApiError && error.status === 404 ? (
          <EmptyState title="Run not found">No run named {id} exists in the runs folder.</EmptyState>
        ) : error ? (
          <ErrorState error={error} onRetry={reload} />
        ) : (
          <LoadingState label="Loading run" />
        )}
      </Page>
    );
  }
  if (data.activity) {
    return (
      <Page
        title={data.activity.scenario}
        crumbs={crumbs}
        description={<span className="font-mono">{id}</span>}
        actions={<Badge variant="secondary">Background activity</Badge>}
      >
        <ActivityView activity={data.activity} />
      </Page>
    );
  }
  return <VerifyRun id={id} detail={data} crumbs={crumbs} />;
}

function VerifyRun({ id, detail, crumbs }: { id: string; detail: RunDetail; crumbs: { label: string; to?: string }[] }) {
  const { run, result, artifacts } = detail;
  const report = result?.report && artifacts.some((file) => file.path === result.report) ? result.report : undefined;
  const verdict = result?.verdict;

  return (
    <Page
      title={run?.scenario ?? id}
      crumbs={crumbs}
      description={<span className="font-mono">{id}</span>}
      actions={
        <>
          {verdict ? <VerdictBadge verdict={verdict} className="h-7 px-3 text-sm" /> : <Badge variant="outline">No verdict yet</Badge>}
          {report && (
            <Button asChild variant="outline">
              <a href={artifactUrl(id, report)} target="_blank" rel="noreferrer">
                <FileBarChart aria-hidden />
                Open report
              </a>
            </Button>
          )}
        </>
      }
    >
      {detail.explanation && <ExplanationCard key={id} runId={id} explanation={detail.explanation} cached={detail.ai_explanation} />}

      <Card className="overflow-hidden pt-0">
        <div className="window-card-header">
          <span className="truncate">run.json · result.json</span>
        </div>
        <CardContent className="grid gap-4">
          {verdict && <p className="text-sm text-muted-foreground">{VERDICT_MEANING[verdict]}</p>}
          <Facts
            items={[
              ["Lifecycle", (result?.lifecycle ?? run?.lifecycle ?? "unknown").replace("_", " ")],
              ["Evidence", result ? result.completeness : "n/a"],
              ["Seed", result?.seed ?? run?.seed ?? "n/a"],
              ["Target", <span className="font-mono text-xs" title={run?.target}>{run?.target ?? "n/a"}</span>],
              ["Scenario digest", <span className="font-mono text-xs" title={run?.scenario_sha256}>{run?.scenario_sha256.slice(0, 12) ?? "n/a"}</span>],
              ["Engine", <span title={run?.engine}>{run?.engine.split(" (")[0] ?? "n/a"}</span>],
              ["Started", formatDateTime(run?.started_at)],
              ["Finished", formatDateTime(result?.finished_at ?? run?.finished_at)],
            ]}
          />
        </CardContent>
      </Card>

      {result ? (
        <Tabs defaultValue="assertions" className="gap-4">
          <div className="-mx-4 overflow-x-auto px-4 md:mx-0 md:px-0">
            <TabsList>
              <TabsTrigger value="assertions">
                Assertions
                <Badge variant="secondary" className="tabular-nums">{result.assertions.length}</Badge>
              </TabsTrigger>
              {result.journeys && <TabsTrigger value="journeys">Journeys</TabsTrigger>}
              <TabsTrigger value="observations">Observations</TabsTrigger>
              <TabsTrigger value="metrics">Metrics</TabsTrigger>
              <TabsTrigger value="limitations">
                Limitations
                {result.limitations.length > 0 && <Badge variant="secondary" className="tabular-nums">{result.limitations.length}</Badge>}
              </TabsTrigger>
              <TabsTrigger value="artifacts">Artifacts</TabsTrigger>
            </TabsList>
          </div>
          <TabsContent value="assertions">
            <AssertionsTable assertions={result.assertions} />
          </TabsContent>
          {result.journeys && (
            <TabsContent value="journeys">
              <Journeys journeys={result.journeys} />
            </TabsContent>
          )}
          <TabsContent value="observations">
            <Observations observation={detail.observation} fixture={detail.fixture} />
          </TabsContent>
          <TabsContent value="metrics">
            <Metrics metrics={result.metrics} />
          </TabsContent>
          <TabsContent value="limitations">
            <Limitations result={result} />
          </TabsContent>
          <TabsContent value="artifacts">
            <Artifacts runId={id} artifacts={artifacts} />
          </TabsContent>
        </Tabs>
      ) : (
        <>
          <EmptyState title="No result yet">
            {run ? "The run has not finished, or stopped before writing result.json." : "This folder has no readable run metadata."}
          </EmptyState>
          <Artifacts runId={id} artifacts={artifacts} />
        </>
      )}
    </Page>
  );
}
