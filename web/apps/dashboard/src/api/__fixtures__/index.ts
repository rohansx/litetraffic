// Real run artifacts (trimmed, scrubbed, org names neutralised) typed as the API contract.
import type { Comparison, Meta, RunDetail, RunListEntry, ScenarioList, TrendPoint, ApiErrorBody } from "../types";
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
};
