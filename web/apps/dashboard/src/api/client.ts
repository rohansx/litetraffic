import type {
  AiExplanation,
  ApiErrorBody,
  Comparison,
  DraftRequest,
  Job,
  JobSummary,
  LibraryDetail,
  LibraryEntry,
  LibrarySaved,
  LibrarySaveRequest,
  Meta,
  RunRequest,
  RunDetail,
  RunFilters,
  RunListEntry,
  ScenarioList,
  TrendPoint,
} from "./types";

/** Non-2xx response; `message` is the server's JSON `error` when it sent one. */
export class ApiError extends Error {
  readonly status: number;
  /** The whole JSON error body, for fields beyond `error` (`job_id`, `needs_trust`, `command`). */
  readonly body: ApiErrorBody | null;
  constructor(status: number, message: string, body: ApiErrorBody | null = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

const seg = encodeURIComponent;

async function getJson<T>(path: string, signal?: AbortSignal, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, { ...init, signal, headers: { Accept: "application/json", ...init.headers } });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as ApiErrorBody | null;
    throw new ApiError(response.status, body?.error ?? `${response.status} ${response.statusText}`, body);
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

/** A JSON write; `action` is the X-LiteTraffic-Action header the server requires on every POST/DELETE. */
function send<T>(action: string, method: "POST" | "DELETE", path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  return getJson<T>(path, signal, {
    method,
    headers: { "X-LiteTraffic-Action": action, ...(body === undefined ? {} : { "Content-Type": "application/json" }) },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
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
  /** Asks the local claude/codex CLI to explain a run; the server caches the answer. */
  explain: (runId: string, signal?: AbortSignal) =>
    getJson<AiExplanation>(`/api/runs/${seg(runId)}/explain`, signal, {
      method: "POST",
      headers: { "X-LiteTraffic-Action": "explain" },
    }),
  libraryList: (signal?: AbortSignal) => getJson<LibraryEntry[]>("/api/library", signal),
  library: (name: string, signal?: AbortSignal) => getJson<LibraryDetail>(`/api/library/${seg(name)}`, signal),
  /** 400 with `needs_trust` (and `command`) when the bundle has a setup command; 409 when it exists; 422 when invalid. */
  librarySave: (request: LibrarySaveRequest) => send<LibrarySaved>("library-save", "POST", "/api/library", request),
  libraryKit: (name: string, config: object) =>
    send<LibrarySaved>("library-kit", "POST", "/api/library/kit", { name, config }),
  libraryDelete: (name: string) => send<{ deleted: string }>("library-delete", "DELETE", `/api/library/${seg(name)}`),
  jobs: (signal?: AbortSignal) => getJson<JobSummary[]>("/api/jobs", signal),
  job: (id: string, signal?: AbortSignal) => getJson<Job>(`/api/jobs/${seg(id)}`, signal),
  /** 409 (body `job_id`) when another job is running. */
  runJob: (request: RunRequest) => send<{ id: string }>("job-run", "POST", "/api/jobs", request),
  cancelJob: (id: string) => send<{ state: string }>("job-cancel", "POST", `/api/jobs/${seg(id)}/cancel`),
  draftStart: (request: DraftRequest) => send<{ id: string }>("draft-start", "POST", "/api/draft", request),
  artifactText: async (runId: string, path: string, signal?: AbortSignal): Promise<string> => {
    const response = await fetch(artifactUrl(runId, path), { signal });
    if (!response.ok) throw new ApiError(response.status, `${response.status} ${response.statusText}`);
    return response.text();
  },
};
