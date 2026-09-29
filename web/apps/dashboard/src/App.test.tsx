import { render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { App } from "@/App";
import { api, ApiError, artifactUrl } from "@/api/client";
import { fixtures } from "@/api/__fixtures__";

afterEach(() => vi.unstubAllGlobals());

function stubFetch(status: number, body: unknown) {
  const fetchMock = vi.fn(async (_url: string) => new Response(JSON.stringify(body), { status }));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

test("renders the app shell with sidebar navigation", async () => {
  stubFetch(200, []);
  render(<App />);
  expect(await screen.findByRole("heading", { name: "Overview" })).toBeInTheDocument();
  for (const name of ["Overview", "Runs", "Scenarios", "Compare", "About"]) {
    expect(screen.getAllByRole("link", { name }).length).toBeGreaterThan(0);
  }
});

test("client builds filtered run list URLs and returns the fixture", async () => {
  const fetchMock = stubFetch(200, fixtures.runs);
  const runs = await api.runs({ scenario: "acme tenant", verdict: "fail", seed: "" });
  expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/runs?scenario=acme+tenant&verdict=fail");
  expect(runs.map((run) => run.kind)).toContain("activity");
});

test("client surfaces a 409 comparison error body", async () => {
  stubFetch(409, fixtures.diffError);
  const error = await api.diff("a", "b").catch((caught: unknown) => caught);
  expect(error).toBeInstanceOf(ApiError);
  expect(error).toMatchObject({ status: 409, message: fixtures.diffError.error });
});

test("artifact URLs encode each path segment", () => {
  expect(artifactUrl("run_1", "events/000001 a.jsonl")).toBe("/api/runs/run_1/artifacts/events/000001%20a.jsonl");
});

test("fixtures carry the shapes the contract promises", () => {
  expect(fixtures.runFail.result?.verdict).toBe("fail");
  expect(fixtures.runFail.result?.assertions.some((a) => a.failures?.length)).toBe(true);
  expect(fixtures.runActivity.activity?.slices.length).toBeGreaterThan(0);
  expect(fixtures.diff.comparable).toBe(true);
  expect(fixtures.diffIncomparable.incompatibilities).toContain("scenario_sha256");
});
