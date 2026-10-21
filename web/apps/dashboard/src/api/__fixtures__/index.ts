// Real run artifacts (trimmed, scrubbed, org names neutralised) typed as the API contract.
import type {
  Comparison,
  Job,
  JobSummary,
  LibraryDetail,
  LibraryEntry,
  Meta,
  RunDetail,
  RunListEntry,
  ScenarioList,
  TrendPoint,
  ApiErrorBody,
} from "../types";
import meta from "./meta.json";
import runs from "./runs.json";
import scenarios from "./scenarios.json";
import runFail from "./run-fail.json";
import runPass from "./run-pass.json";
import runInconclusive from "./run-inconclusive.json";
import runActivity from "./run-activity.json";
import diff from "./diff.json";
import diffIncomparable from "./diff-incomparable.json";
import diffError from "./diff-error.json";
import trend from "./trend.json";
import library from "./library.json";
import libraryDetail from "./library-detail.json";
import jobs from "./jobs.json";
import jobRunning from "./job-running.json";
import jobFinished from "./job-finished.json";
import jobDraftRunning from "./job-draft-running.json";
import jobDraftFinished from "./job-draft-finished.json";
import jobDraftFailed from "./job-draft-failed.json";

export const fixtures = {
  meta: meta as Meta,
  runs: runs as RunListEntry[],
  scenarios: scenarios as ScenarioList,
  runFail: runFail as unknown as RunDetail,
  runPass: runPass as unknown as RunDetail,
  runInconclusive: runInconclusive as unknown as RunDetail,
  runActivity: runActivity as unknown as RunDetail,
  diff: diff as unknown as Comparison,
  diffIncomparable: diffIncomparable as unknown as Comparison,
  diffError: diffError as ApiErrorBody,
  trend: trend as TrendPoint[],
  // Console: generated from `litetraffic inspect --json` on a tenant-isolation kit bundle, jobs shaped by the API contract.
  library: library as LibraryEntry[],
  libraryDetail: libraryDetail as unknown as LibraryDetail,
  jobs: jobs as JobSummary[],
  jobRunning: jobRunning as Job,
  jobFinished: jobFinished as Job,
  jobDraftRunning: jobDraftRunning as Job,
  jobDraftFinished: jobDraftFinished as Job,
  jobDraftFailed: jobDraftFailed as Job,
};
