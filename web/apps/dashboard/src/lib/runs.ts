import type { RunListEntry } from "@/api/types";

const time = (run: RunListEntry) => (run.finished_at ? Date.parse(run.finished_at) : Number.POSITIVE_INFINITY);

/** Oldest first; unfinished runs sort last (they are the newest). */
export function oldestFirst<T extends RunListEntry>(runs: T[]): T[] {
  return [...runs].sort((a, b) => time(a) - time(b));
}

export const JUDGED = new Set(["pass", "fail", "inconclusive", "error"]);

export interface ScenarioSummary {
  name: string;
  /** Verify runs, oldest first. */
  runs: RunListEntry[];
  latest?: RunListEntry;
}

export function summarizeScenarios(runs: RunListEntry[], names: string[] = []): ScenarioSummary[] {
  const groups = new Map<string, RunListEntry[]>(names.map((name) => [name, []]));
  for (const run of runs) {
    if (run.kind !== "run" || !run.scenario) continue;
    groups.set(run.scenario, [...(groups.get(run.scenario) ?? []), run]);
  }
  return [...groups]
    .map(([name, list]) => {
      const sorted = oldestFirst(list);
      return { name, runs: sorted, latest: sorted.at(-1) };
    })
    .sort((a, b) => a.name.localeCompare(b.name));
}
