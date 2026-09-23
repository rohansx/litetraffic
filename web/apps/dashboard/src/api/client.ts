import type {
  ApiErrorBody,
  Comparison,
  Meta,
  RunDetail,
  RunFilters,
  RunListEntry,
  ScenarioList,
  TrendPoint,
} from "./types";

/** Non-2xx response; `message` is the server's JSON `error` when it sent one. */
export class ApiError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

const seg = encodeURIComponent;

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(path, { signal, headers: { Accept: "application/json" } });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as ApiErrorBody | null;
    throw new ApiError(response.status, body?.error ?? `${response.status} ${response.statusText}`);
  }
  return (await response.json()) as T;
}

function query(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

/** URL of a raw run artifact (e.g. "report.html", "events/000001.jsonl"); each segment is encoded. */
export function artifactUrl(runId: string, path: string): string {
  return `/api/runs/${seg(runId)}/artifacts/${path.split("/").map(seg).join("/")}`;
}

export const api = {
  meta: (signal?: AbortSignal) => getJson<Meta>("/api/meta", signal),
  runs: (filters: RunFilters = {}, signal?: AbortSignal) =>
    getJson<RunListEntry[]>(`/api/runs${query({ ...filters })}`, signal),
  scenarios: (signal?: AbortSignal) => getJson<ScenarioList>("/api/scenarios", signal),
  run: (runId: string, signal?: AbortSignal) => getJson<RunDetail>(`/api/runs/${seg(runId)}`, signal),
  /** Throws ApiError with status 409 when the runs cannot be compared. */
  diff: (baseline: string, candidate: string, signal?: AbortSignal) =>
    getJson<Comparison>(`/api/diff${query({ baseline, candidate })}`, signal),
  trend: (scenario: string, signal?: AbortSignal) =>
    getJson<TrendPoint[]>(`/api/scenarios/${seg(scenario)}/trend`, signal),
  artifactText: async (runId: string, path: string, signal?: AbortSignal): Promise<string> => {
    const response = await fetch(artifactUrl(runId, path), { signal });
    if (!response.ok) throw new ApiError(response.status, `${response.status} ${response.statusText}`);
    return response.text();
  },
};
