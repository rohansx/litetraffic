import { useEffect, useMemo, useState } from "react";
import { GitCompareArrows, Search } from "lucide-react";
import { Link, useNavigate, useSearchParams } from "react-router";
import { api } from "@/api/client";
import type { RunListEntry } from "@/api/types";
import { Page } from "@/components/page";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { VerdictBadge } from "@/components/verdict";
import { formatDateTime, formatRelative } from "@/lib/format";
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
  const scenarios = useMemo(() => [...new Set(all.flatMap((run) => (run.scenario ? [run.scenario] : [])))].sort(), [all]);
  const visible = useMemo(() => applyFilters(all, params), [all, params]);
  const filtered = [...params.keys()].some((key) => params.get(key));

  function setFilter(key: string, value: string) {
    const next = new URLSearchParams(params);
    if (value && value !== ALL) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  }

  function toggle(runId: string, on: boolean) {
    setSelected((current) => (on ? [...current.filter((id) => id !== runId), runId].slice(-2) : current.filter((id) => id !== runId)));
  }

  function compare() {
    const [baseline, candidate] = oldestFirst(all.filter((run) => selected.includes(run.run_id)));
    if (!baseline || !candidate) return;
    navigate(`/compare?${new URLSearchParams({ baseline: baseline.run_id, candidate: candidate.run_id })}`);
  }

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
          <Button onClick={compare} disabled={selected.length !== 2}>
            <GitCompareArrows aria-hidden />
            Compare {selected.length}/2
          </Button>
        </>
      }
    >
      <div role="search" aria-label="Filter runs" className="flex flex-wrap items-center gap-2">
        <div className="relative w-full sm:w-64">
          <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
          <Input
            type="search"
            placeholder="Search run or scenario"
            aria-label="Search runs"
            className="pl-8"
            value={params.get("q") ?? ""}
            onChange={(event) => setFilter("q", event.target.value)}
          />
        </div>
        <Select value={params.get("scenario") ?? ALL} onValueChange={(value) => setFilter("scenario", value)}>
          <SelectTrigger aria-label="Scenario" className="w-full sm:w-64">
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
          <SelectTrigger aria-label="Verdict" className="w-[calc(50%-0.25rem)] sm:w-40">
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
          className="w-[calc(50%-0.25rem)] sm:w-24"
          value={params.get("seed") ?? ""}
          onChange={(event) => setFilter("seed", event.target.value)}
        />
        {filtered && (
          <Button variant="ghost" size="sm" onClick={() => setParams({}, { replace: true })}>
            Clear filters
          </Button>
        )}
        <span role="status" className="ml-auto text-xs text-muted-foreground">
          {runs.data ? `${visible.length} of ${all.length} shown` : ""}
          {runs.error && runs.data ? ". Refresh failed, showing the last list." : ""}
        </span>
      </div>

      {!runs.data ? (
        runs.error ? <ErrorState error={runs.error} onRetry={reload} /> : <LoadingState label="Loading runs" />
      ) : !all.length ? (
        <EmptyState title="No runs yet">
          Run <code className="font-mono">litetraffic verify scenario.yaml</code>; the list refreshes on its own.
        </EmptyState>
      ) : !visible.length ? (
        <EmptyState
          title="No runs match these filters"
          action={
            <Button variant="outline" onClick={() => setParams({}, { replace: true })}>
              Clear filters
            </Button>
          }
        />
      ) : (
        <RunsTable runs={visible} selected={selected} onToggle={toggle} />
      )}
    </Page>
  );
}

function RunsTable({ runs, selected, onToggle }: { runs: RunListEntry[]; selected: string[]; onToggle: (id: string, on: boolean) => void }) {
  return (
    <div className="rounded-xl border bg-card">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="w-10">
              <span className="sr-only">Select</span>
            </TableHead>
            <TableHead>Run</TableHead>
            <TableHead className="hidden md:table-cell">Scenario</TableHead>
            <TableHead>Verdict</TableHead>
            <TableHead>Lifecycle</TableHead>
            <TableHead className="text-right">Seed</TableHead>
            <TableHead>Finished</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {runs.map((run) => {
            const isSelected = selected.includes(run.run_id);
            return (
              <TableRow key={run.run_id} data-state={isSelected ? "selected" : undefined}>
                <TableCell>
                  {run.kind === "run" && (
                    <Checkbox
                      checked={isSelected}
                      onCheckedChange={(value) => onToggle(run.run_id, value === true)}
                      aria-label={`Select ${run.run_id}`}
                    />
                  )}
                </TableCell>
                <TableCell className="font-mono text-xs">
                  {run.kind === "series" ? (
                    <span title="Series have no detail page">{run.run_id}</span>
                  ) : (
                    <Link to={`/runs/${encodeURIComponent(run.run_id)}`} className="hover:underline focus-visible:underline">
                      {run.run_id}
                    </Link>
                  )}
                  {run.kind !== "run" && <span className="ml-2 font-sans text-muted-foreground">{run.kind}</span>}
                  {/* ponytail: phones drop the Scenario column so the verdict stays on screen; the name moves here. */}
                  <div className="mt-0.5 font-sans text-muted-foreground md:hidden">{run.scenario ?? "Unknown scenario"}</div>
                </TableCell>
                <TableCell className="hidden md:table-cell">
                  {run.scenario ? (
                    <Link to={`/scenarios/${encodeURIComponent(run.scenario)}`} className="hover:underline focus-visible:underline">
                      {run.scenario}
                    </Link>
                  ) : (
                    <span className="text-muted-foreground">Unknown</span>
                  )}
                </TableCell>
                <TableCell>
                  <VerdictBadge verdict={run.verdict} />
                </TableCell>
                <TableCell className="text-muted-foreground">{run.lifecycle?.replace("_", " ") ?? "Unknown"}</TableCell>
                <TableCell className="text-right tabular-nums">{run.seed ?? "n/a"}</TableCell>
                <TableCell className="text-muted-foreground" title={formatDateTime(run.finished_at)}>
                  {formatRelative(run.finished_at)}
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}
