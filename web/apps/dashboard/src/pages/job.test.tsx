import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test } from "vitest";
import { fixtures } from "@/api/__fixtures__";
import type { Job } from "@/api/types";
import { formatElapsed, JOB_POLL } from "@/lib/use-job";
import { renderApp } from "@/test/render";
import { exitMeaning } from "@/pages/job";

const RUNNING = fixtures.jobRunning;
const PATH = `/api/jobs/${RUNNING.id}`;

beforeEach(() => {
  JOB_POLL.ms = 10;
});

test("running job: details, live log, no outcome yet, cancel button", async () => {
  renderApp(`/jobs/${RUNNING.id}`, { [PATH]: RUNNING });
  expect(await screen.findByRole("heading", { level: 1, name: "my-api-tenant-isolation" })).toBeInTheDocument();
  expect(screen.getAllByText("Running").length).toBeGreaterThan(0);
  expect(screen.getByText("http://127.0.0.1:8000")).toBeInTheDocument();
  // Secrets are shown by name only.
  expect(screen.getByText("TENANT_A_TOKEN, TENANT_B_TOKEN")).toBeInTheDocument();
  const log = screen.getByRole("log", { name: "Run log" });
  expect(log).toHaveAttribute("aria-live", "polite");
  expect(log).toHaveTextContent("warmup: 1 journeys/s for 1 s");
  expect(screen.getByRole("button", { name: "Cancel" })).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "Open run evidence" })).not.toBeInTheDocument();
  expect(screen.queryByRole("region", { name: "Outcome" })).not.toBeInTheDocument();
});

test("polls while running: the run link appears once run_id exists, then the verdict card replaces Cancel, and polling stops", async () => {
  let stage = 0;
  const withRunId: Job = { ...RUNNING, run_id: fixtures.jobFinished.run_id, log: [...RUNNING.log, "run started"] };
  const { fetchMock } = renderApp(`/jobs/${RUNNING.id}`, {
    [PATH]: () => [RUNNING, withRunId, fixtures.jobFinished][stage],
  });
  await screen.findByRole("button", { name: "Cancel" });
  expect(screen.queryByRole("link", { name: "Open run evidence" })).not.toBeInTheDocument();

  stage = 1;
  const live = await screen.findByRole("link", { name: "Open run evidence" });
  expect(live).toHaveAttribute("href", `/runs/${fixtures.jobFinished.run_id}`);
  expect(screen.getByRole("button", { name: "Cancel" })).toBeInTheDocument();
  expect(screen.getByRole("log", { name: "Run log" })).toHaveTextContent("run started");

  stage = 2;
  const outcome = await screen.findByRole("region", { name: "Outcome" });
  expect(within(outcome).getByText("Fail")).toBeInTheDocument();
  expect(within(outcome).getByText("Exit 1: failed")).toBeInTheDocument();
  expect(within(outcome).getByText("At least one business check definitely failed.")).toBeInTheDocument();
  expect(within(outcome).getByRole("link", { name: "Open full run" })).toHaveAttribute("href", `/runs/${fixtures.jobFinished.run_id}`);
  expect(within(outcome).getByRole("link", { name: "Run again" })).toHaveAttribute("href", "/new?scenario=my-api-tenant-isolation");
  expect(within(outcome).getByRole("link", { name: "Explain with AI" })).toHaveAttribute("href", `/runs/${fixtures.jobFinished.run_id}`);
  expect(screen.queryByRole("button", { name: "Cancel" })).not.toBeInTheDocument();
  expect(screen.getByRole("log", { name: "Run log" })).toHaveTextContent("verdict: fail");

  const seen = fetchMock.mock.calls.length;
  await act(() => new Promise((resolve) => setTimeout(resolve, 80)));
  expect(fetchMock.mock.calls.length).toBe(seen); // not running any more: no more polling
});

test("cancel posts job-cancel and the page follows the job to cancelled", async () => {
  const user = userEvent.setup();
  let cancelled = false;
  const { fetchMock } = renderApp(`/jobs/${RUNNING.id}`, {
    [PATH]: () => (cancelled ? { ...RUNNING, state: "cancelled", exit_code: 130, finished_at: "2026-09-23T07:00:03+00:00" } : RUNNING),
    [`POST ${PATH}/cancel`]: () => {
      cancelled = true;
      return { state: "cancelled" };
    },
  });
  await user.click(await screen.findByRole("button", { name: "Cancel" }));
  const outcome = await screen.findByRole("region", { name: "Outcome" });
  expect(within(outcome).getByText("Cancelled")).toBeInTheDocument();
  expect(within(outcome).getByText("Exit 130: cancelled")).toBeInTheDocument();
  const call = fetchMock.mock.calls.find(([, init]) => init?.method === "POST")!;
  expect(call[0]).toBe(`${PATH}/cancel`);
  expect(call[1]?.headers).toMatchObject({ "X-LiteTraffic-Action": "job-cancel" });
});

test("a failed cancel shows the server's error and keeps the button", async () => {
  const user = userEvent.setup();
  renderApp(`/jobs/${RUNNING.id}`, { [PATH]: RUNNING, [`POST ${PATH}/cancel`]: [500, { error: "could not signal the process" }] });
  await user.click(await screen.findByRole("button", { name: "Cancel" }));
  expect(await screen.findByText("could not signal the process")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Cancel" })).toBeEnabled();
});

test("error and inconclusive outcomes map from verdict and exit code", async () => {
  const error = renderApp(`/jobs/${RUNNING.id}`, {
    [PATH]: { ...fixtures.jobFinished, state: "failed", verdict: "error", exit_code: 3, run_id: null },
  });
  const outcome = await screen.findByRole("region", { name: "Outcome" });
  expect(within(outcome).getByText("Error")).toBeInTheDocument();
  expect(within(outcome).getByText("Exit 3: error")).toBeInTheDocument();
  expect(within(outcome).queryByRole("link", { name: "Open full run" })).not.toBeInTheDocument();
  error.unmount();

  renderApp(`/jobs/${RUNNING.id}`, { [PATH]: { ...fixtures.jobFinished, verdict: "inconclusive", exit_code: 2 } });
  expect(await screen.findByText("Inconclusive")).toBeInTheDocument();
  expect(exitMeaning(0)).toBe("Exit 0: passed");
  expect(exitMeaning(7)).toBe("Exit 7");
});

test("a draft job renders its log and result", async () => {
  const draft = fixtures.jobDraftFinished;
  renderApp(`/jobs/${draft.id}`, { [`/api/jobs/${draft.id}`]: draft });
  expect(await screen.findByRole("log", { name: "Draft log" })).toHaveTextContent("attempt 1: valid");
  expect(screen.getByText("Validates")).toBeInTheDocument();
  expect(screen.getByLabelText("Contents of journeys.js")).toBeInTheDocument();
  expect(screen.queryByRole("region", { name: "Outcome" })).not.toBeInTheDocument();
});

test("unknown job and server errors", async () => {
  const missing = renderApp("/jobs/nope", { "/api/jobs/nope": [404, { error: "no such job" }] });
  expect(await screen.findByText("Job not found")).toBeInTheDocument();
  missing.unmount();
  renderApp("/jobs/bad", { "/api/jobs/bad": [500, { error: "console fell over" }] });
  await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("console fell over"));
});

test("elapsed time formats as m:ss", () => {
  expect(formatElapsed(9)).toBe("0:09");
  expect(formatElapsed(125.7)).toBe("2:05");
  expect(formatElapsed(3725)).toBe("1:02:05");
});
