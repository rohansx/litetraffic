/**
 * Typed contract for the dashboard JSON API (src/litetraffic/dashboard*.py).
 * Shapes mirror what the Python engine writes; sources are named on each type.
 * Artifacts on disk are untrusted: every field the engine may omit is optional,
 * and anything the engine writes as `null` is typed `| null`.
 */

/** Terminal verdict of a verify run (runner.py). */
export type Verdict = "pass" | "fail" | "inconclusive" | "error";
/** Verdict column in the runs list: unreadable metadata, or a background activity (runs.py `_entry`). */
export type ListVerdict = Verdict | "unreadable" | "background";
/** run.json/result.json `lifecycle` (runner.py `_record_stage` + final lifecycle). */
export type Lifecycle =
  | "preparing"
  | "running"
  | "observing"
  | "finalizing"
  | "finished"
  | "timed_out"
  | "cancelled"
  | "crashed";
/** activity.json `status` (activity.py). */
export type ActivityStatus = "running" | "completed" | "stopped" | "error";
export type AssertionStatus = "pass" | "fail" | "unknown";

// ---------- GET /api/meta ----------
export interface Meta {
  runs_dir: string;
  version: string;
}

// ---------- GET /api/runs?scenario=&verdict=&seed= (runs.py `list_runs`) ----------
export type RunKind = "run" | "series" | "activity";
export interface RunListEntry {
  run_id: string;
  kind: RunKind;
  scenario: string | null;
  verdict: ListVerdict;
  /** Run lifecycle; for an activity this is its status. */
  lifecycle: Lifecycle | ActivityStatus | null;
  finished_at: string | null;
  seed: number | null;
  /** Absolute path of the run directory (or series file). */
  path: string;
}
export interface RunFilters {
  scenario?: string;
  verdict?: ListVerdict;
  seed?: string | number;
}

// ---------- GET /api/scenarios ----------
export type ScenarioList = string[];

// ---------- run.json (runner.py `verify`) ----------
export interface SchedulePhase {
  name: string;
  rate: number;
  seconds: number;
}
export interface RunManifest {
  schema_version: 1;
  run_id: string;
  scenario: string;
  mode: "verify";
  target: string;
  seed: number;
  scenario_sha256: string;
  engine: string;
  lifecycle: Lifecycle;
  resolved_schedule: SchedulePhase[];
  started_at: string;
  finished_at?: string;
  engine_exit_code?: number | null;
  engine_started_at?: string;
  engine_finished_at?: string;
}

// ---------- result.json (runner.py `verify`) ----------
export interface AssertionFailure {
  sequence: number;
  logical_key: string;
  expected: unknown;
  actual: unknown;
  detail: string | null;
}
export interface AssertionResult {
  id: string;
  status: AssertionStatus;
  samples: number;
  reason?: string;
  expected?: Record<string, unknown>;
  actual?: Record<string, unknown>;
  /** First failing samples, expected vs actual. */
  failures?: AssertionFailure[];
}
export interface LatencySummary {
  average?: number;
  p50?: number;
  p95?: number;
  max?: number;
  samples?: number;
}
export interface RateSummary {
  rate: number;
  failed?: number;
  samples?: number;
}
export interface OperationMetrics {
  p95?: number;
  failed_rate?: number;
  samples?: number;
}
export interface RunMetrics {
  http_req_duration_ms?: LatencySummary;
  http_req_failed_rate?: RateSummary;
  unexpected_http_failure_rate?: RateSummary;
  http_reqs?: number;
  http_reqs_per_second?: number;
  iterations?: number;
  iterations_per_second?: number;
  elapsed_seconds?: number;
  vus_max?: number;
  write_attempts?: number;
  observer_requests?: number;
  fixture_requests?: number;
  total_http_reqs?: number;
  dropped_iterations?: number;
  /** Per-operation latency and failure rate, keyed by operation name. */
  by_operation?: Record<string, OperationMetrics>;
  /** Client-side peak concurrent journeys per operation. */
  overlap?: Record<string, number>;
}
export interface RunResult {
  schema_version: 1;
  run_id: string;
  mode: "verify";
  seed: number;
  planned_journeys: number;
  planned_journeys_per_second: number;
  report: string;
  lifecycle: Lifecycle;
  engine_exit_code: number | null;
  finished_at: string;
  verdict: Verdict;
  completeness: "complete" | "incomplete";
  assertions: AssertionResult[];
  /** Empty object when the run was cancelled before metrics existed. */
  metrics: RunMetrics;
  limitations: string[];
  notes: string[];
}

// ---------- observation.json (observation.py; object for a legacy single observation, else a list) ----------
export interface ObservationCheck {
  actual: unknown;
  matcher: Record<string, unknown>;
  pass: boolean;
}
export interface ObservationRecord {
  assertion: string;
  status: AssertionStatus;
  reason?: string;
  expected?: Record<string, unknown>;
  actual?: Record<string, unknown>;
  checks?: Record<string, ObservationCheck>;
  expressions?: Record<string, unknown>;
  attempts?: number;
  elapsed_seconds?: number;
}
export type Observation = ObservationRecord | ObservationRecord[];

// ---------- fixture.json (fixture.py; command hooks OR an owned HTTP fixture) ----------
export interface FixtureHook {
  argv: string[];
  exit_code: number | null;
  status: "ok" | "error" | "cancelled";
  stderr: string;
  duration_seconds?: number;
  reason?: string;
}
export interface FixtureHooks {
  setup: FixtureHook;
  teardown?: FixtureHook;
}
export interface OwnedFixtureStep {
  status: "created" | "deleted" | "error" | "cancelled";
  requests: number;
  fixture_id?: string;
  reason?: string;
}
export interface OwnedFixture {
  create: OwnedFixtureStep;
  cleanup?: OwnedFixtureStep;
}
export type Fixture = FixtureHooks | OwnedFixture;

// ---------- activity.json (activity.py `up`) ----------
export interface ActivitySlice {
  run_id: string;
  seed: number;
  lifecycle: Lifecycle;
  finished_at: string | null;
  iterations: number | null;
  http_reqs: number | null;
}
export interface Activity {
  schema_version: 1;
  activity_id: string;
  mode: "background";
  scenario: string;
  scenario_sha256: string;
  target: string;
  starting_seed: number;
  max_slices: number | null;
  artifact_dir: string;
  status: ActivityStatus;
  started_at: string;
  finished_at?: string;
  error?: string;
  slices: ActivitySlice[];
}

// ---------- GET /api/runs/{id} ----------
export interface ArtifactFile {
  /** Path relative to the run directory, "/"-separated. */
  path: string;
  size: number;
}
export interface RunDetail {
  /** run.json; null for an activity (`up`) directory or unreadable metadata. */
  run: RunManifest | null;
  /** result.json; null for an activity or a run that never finished. */
  result: RunResult | null;
  observation?: Observation;
  fixture?: Fixture;
  activity?: Activity;
  artifacts: ArtifactFile[];
}

// ---------- GET /api/diff?baseline=&candidate= (compare.py `compare_runs`) ----------
export type CompatibilityField = "scenario_sha256" | "seed" | "engine" | "resolved_schedule";
export type P95Status = "reported" | "unavailable" | "inconclusive" | "regression" | "within_limit" | "incomparable";
export interface RateChange {
  baseline: number | null;
  candidate: number | null;
  change_percentage_points: number | null;
}
export interface OperationP95Change {
  baseline_p95_ms: number;
  candidate_p95_ms: number;
  change_percent: number | null;
}
export interface Comparison {
  schema_version: 1;
  baseline_run_id: string;
  candidate_run_id: string;
  comparable: boolean;
  incompatibilities: CompatibilityField[];
  verdict: Verdict;
  reasons: string[];
  correctness: {
    baseline_verdict: Verdict;
    candidate_verdict: Verdict;
    regression: boolean;
    assertion_regressions: string[];
  };
  progress: {
    iterations: { baseline: number | null; candidate: number | null };
    http_reqs: { baseline: number | null; candidate: number | null };
  };
  performance: {
    p95: {
      baseline_ms: number | null;
      candidate_ms: number | null;
      change_percent: number | null;
      threshold_percent: number | null;
      samples: { baseline: number; candidate: number };
      status: P95Status;
    };
    http_error_rate: RateChange;
    unexpected_http_error_rate?: RateChange;
    http_reqs_per_second: { baseline: number | null; candidate: number | null; change_percent: number | null };
    by_operation: Record<string, OperationP95Change>;
  };
}

// ---------- GET /api/scenarios/{name}/trend ----------
export interface TrendPoint {
  run_id: string;
  finished_at: string | null;
  p95: number | null;
  verdict: ListVerdict;
}

/** JSON error body: 404 unknown /api path or run, 409 ComparisonError. */
export interface ApiErrorBody {
  error: string;
}
