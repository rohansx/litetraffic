import { useEffect, useMemo, useState } from "react";
import { GitCompareArrows, Search } from "lucide-react";
import { useNavigate, useSearchParams } from "react-router";
import { api } from "@/api/client";
import type { RunListEntry } from "@/api/types";
import { RunsDataTable, VIEWS, type View } from "@/components/data-table";
import { Page } from "@/components/page";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { oldestFirst } from "@/lib/runs";
import { useApi } from "@/lib/use-api";

const REFRESH_MS = 5000;
const ALL = "all";
const VERDICTS = ["pass", "fail", "inconclusive", "error", "background", "unreadable"] as const;
const VERDICT_LABELS: Record<(typeof VERDICTS)[number], string> = {
  pass: "Pass",
  fail: "Fail",
  inconclusive: "Inconclusive",
  error: "Error",
  background: "Activity",
  unreadable: "Unreadable",
};

/** Every URL filter except the view tab (`kind`), which the tab counts are taken against. */
function applyFilters(runs: RunListEntry[], params: URLSearchParams): RunListEntry[] {
  const scenario = params.get("scenario");
  const verdict = params.get("verdict");
  const seed = params.get("seed")?.trim();
  const query = params.get("q")?.trim().toLowerCase();
  return runs.filter(
    (run) =>
      (!scenario || run.scenario === scenario) &&
      (!verdict || run.verdict === verdict) &&
      (!seed || String(run.seed) === seed) &&
      (!query || `${run.run_id} ${run.scenario ?? ""}`.toLowerCase().includes(query)),
  );
}

function viewOf(params: URLSearchParams): View {
  const kind = params.get("kind");
  return VIEWS.some((view) => view.value === kind) ? (kind as View) : "all";
}

export function RunsPage() {
  const runs = useApi("runs", (signal) => api.runs({}, signal));
  const [params, setParams] = useSearchParams();
  const [live, setLive] = useState(true);
  const [selected, setSelected] = useState<string[]>([]);
  const navigate = useNavigate();
  const { reload } = runs;

  useEffect(() => {
    if (!live) return;
    const timer = window.setInterval(reload, REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [live, reload]);

  const all = runs.data ?? [];
  const view = viewOf(params);
  const scenarios = useMemo(() => [...new Set(all.flatMap((run) => (run.scenario ? [run.scenario] : [])))].sort(), [all]);
  const matching = useMemo(() => applyFilters(all, params), [all, params]);
  const visible = view === "all" ? matching : matching.filter((run) => run.kind === view);
  const counts = Object.fromEntries(
    VIEWS.map(({ value }) => [value, value === "all" ? matching.length : matching.filter((run) => run.kind === value).length]),
  ) as Record<View, number>;
  const filtered = [...params.keys()].some((key) => params.get(key));

  function setFilter(key: string, value: string) {
    const next = new URLSearchParams(params);
    if (value && value !== ALL) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  }

  function select(ids: string[]) {
    // At most two: a third tick drops the oldest tick.
    setSelected((current) => [...current.filter((id) => ids.includes(id)), ...ids.filter((id) => !current.includes(id))].slice(-2));
  }

  function compare() {
    const [baseline, candidate] = oldestFirst(all.filter((run) => selected.includes(run.run_id)));
    if (!baseline || !candidate) return;
    navigate(`/compare?${new URLSearchParams({ baseline: baseline.run_id, candidate: candidate.run_id })}`);
  }

  const clear = () => setParams({}, { replace: true });

  const toolbar = (
    <div role="search" aria-label="Filter runs" className="flex flex-wrap items-center gap-2">
      <div className="relative w-full sm:w-64">
        <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
        <Input
          type="search"
          placeholder="Search run or scenario"
          aria-label="Search runs"
          className="h-8 pl-8"
          value={params.get("q") ?? ""}
          onChange={(event) => setFilter("q", event.target.value)}
        />
      </div>
      <Select value={params.get("scenario") ?? ALL} onValueChange={(value) => setFilter("scenario", value)}>
        <SelectTrigger size="sm" aria-label="Scenario" className="w-full sm:w-64">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={ALL}>All scenarios</SelectItem>
          {scenarios.map((name) => (
            <SelectItem key={name} value={name}>
              {name}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <Select value={params.get("verdict") ?? ALL} onValueChange={(value) => setFilter("verdict", value)}>
        <SelectTrigger size="sm" aria-label="Verdict" className="w-[calc(50%-0.25rem)] sm:w-40">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={ALL}>All verdicts</SelectItem>
          {VERDICTS.map((verdict) => (
            <SelectItem key={verdict} value={verdict}>
              {VERDICT_LABELS[verdict]}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <Input
        inputMode="numeric"
        placeholder="Seed"
        aria-label="Seed"
        className="h-8 w-[calc(50%-0.25rem)] sm:w-24"
        value={params.get("seed") ?? ""}
        onChange={(event) => setFilter("seed", event.target.value)}
      />
      {filtered && (
        <Button variant="ghost" size="sm" onClick={clear}>
          Clear filters
        </Button>
      )}
      <span role="status" className="ml-auto text-xs text-muted-foreground">
        {runs.data ? `${visible.length} of ${all.length} shown` : ""}
        {runs.error && runs.data ? ". Refresh failed, showing the last list." : ""}
      </span>
    </div>
  );

  return (
    <Page
      title="Runs"
      crumbs={[{ label: "Runs" }]}
      description="Every verify run, series and background activity in the runs folder, newest first. Tick two runs to compare them."
      actions={
        <>
          <label className="flex items-center gap-2 text-sm text-muted-foreground">
            <Checkbox checked={live} onCheckedChange={(value) => setLive(value === true)} aria-label="Auto-refresh every 5 seconds" />
            Auto-refresh
          </label>
          <Button size="sm" onClick={compare} disabled={selected.length !== 2}>
            <GitCompareArrows aria-hidden />
            Compare {selected.length}/2
          </Button>
        </>
      }
    >
      {!runs.data ? (
        runs.error ? <ErrorState error={runs.error} onRetry={reload} /> : <LoadingState label="Loading runs" />
      ) : !all.length ? (
        <EmptyState title="No runs yet">
          Run <code className="font-mono">litetraffic verify scenario.yaml</code>; the list refreshes on its own.
        </EmptyState>
      ) : (
        <RunsDataTable
          runs={visible}
          view={view}
          counts={counts}
          onViewChange={(value) => setFilter("kind", value)}
          selected={selected}
          onSelectedChange={select}
          toolbar={toolbar}
          empty={
            <div className="flex flex-col items-center gap-2">
              <p className="font-medium">No runs match these filters</p>
              <Button variant="outline" size="sm" onClick={clear}>
                Clear filters
              </Button>
            </div>
          }
        />
      )}
    </Page>
  );
}
