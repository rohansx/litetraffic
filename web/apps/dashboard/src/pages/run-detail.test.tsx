import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test } from "vitest";
import { fixtures } from "@/api/__fixtures__";
import { PENDING, renderApp } from "@/test/render";

const FAIL = "run_20260923T023454Z_24eba6f8";

test("header shows verdict, lifecycle, seed, scenario, digest, target and the report link", async () => {
  renderApp(`/runs/${FAIL}`, { [`/api/runs/${FAIL}`]: fixtures.runFail });
  expect(await screen.findByRole("heading", { name: "acme-tenant-isolation-v2" })).toBeInTheDocument();
  expect(screen.getByText("At least one business check definitely failed.")).toBeInTheDocument();
  expect(screen.getByText("finished")).toBeInTheDocument();
  expect(screen.getByText("f9b65f2dcaf2")).toBeInTheDocument();
  expect(screen.getByText("http://127.0.0.1:14000")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Open report" })).toHaveAttribute("href", `/api/runs/${FAIL}/artifacts/report.html`);
});

test("assertions tab expands failing assertions to expected vs actual samples", async () => {
  const user = userEvent.setup();
  renderApp(`/runs/${FAIL}`, { [`/api/runs/${FAIL}`]: fixtures.runFail });
  expect(await screen.findByText("7 of 9 assertions failed.")).toBeInTheDocument();
  // The first failing assertion starts open.
  const first = screen.getByRole("button", { name: "Hide failing samples for cross_tenant_balance_read_blocked" });
  expect(first).toHaveAttribute("aria-expanded", "true");
  const panel = document.getElementById(first.getAttribute("aria-controls")!)!;
  expect(within(panel).getAllByText("Expected")).toHaveLength(3);
  expect(within(panel).getAllByText("200")).toHaveLength(3);
  expect(within(panel).getByText("Sample #3")).toBeInTheDocument();

  const other = screen.getByRole("button", { name: "Show failing samples for tenant_b_state_intact" });
  await user.click(other);
  expect(other).toHaveAttribute("aria-expanded", "true");
  expect(within(document.getElementById(other.getAttribute("aria-controls")!)!).getAllByText(/"marker_positions": 2/).length).toBeGreaterThan(0);
  // Passing assertions are not expandable.
  expect(screen.queryByRole("button", { name: /own_org_access_works/ })).toBeNull();
});

test("observations, metrics, limitations and artifacts tabs", async () => {
  const user = userEvent.setup();
  renderApp(`/runs/${FAIL}`, { [`/api/runs/${FAIL}`]: fixtures.runFail });
  await screen.findByText("7 of 9 assertions failed.");

  await user.click(screen.getByRole("tab", { name: "Observations" }));
  expect(screen.getByText("no_probe_positions_in_tenant_b")).toBeInTheDocument();
  expect(screen.getByText("Fixture")).toBeInTheDocument();

  await user.click(screen.getByRole("tab", { name: "Metrics" }));
  expect(screen.getByText("17.9 ms")).toBeInTheDocument();
  expect(screen.getByText("27.3 ms")).toBeInTheDocument();
  const ops = screen.getByRole("cell", { name: "position-create-cross" }).closest("tr")!;
  expect(within(ops).getByText("16.2 ms")).toBeInTheDocument();

  await user.click(screen.getByRole("tab", { name: "Limitations" }));
  expect(screen.getByText("No limitations")).toBeInTheDocument();
  expect(screen.getByText("workload is synthetic (no traces supplied)")).toBeInTheDocument();

  await user.click(screen.getByRole("tab", { name: "Artifacts" }));
  expect(screen.getByRole("link", { name: /events\/000001\.jsonl/ })).toHaveAttribute("href", `/api/runs/${FAIL}/artifacts/events/000001.jsonl`);
});

test("inconclusive run lists its limitations", async () => {
  const user = userEvent.setup();
  const id = "run_20260923T043011Z_929fe95f";
  renderApp(`/runs/${id}`, { [`/api/runs/${id}`]: fixtures.runInconclusive });
  expect(await screen.findByText("Evidence was missing or incomplete, so the run cannot pass.")).toBeInTheDocument();
  expect(screen.getByText("timed out")).toBeInTheDocument();
  await user.click(screen.getByRole("tab", { name: /Limitations/ }));
  expect(within(screen.getByRole("list", { name: "Limitations" })).getAllByRole("listitem")).toHaveLength(4);
});

test("the Reason column appears only when an assertion has a reason", async () => {
  const first = renderApp(`/runs/${FAIL}`, { [`/api/runs/${FAIL}`]: fixtures.runFail });
  await screen.findByText("7 of 9 assertions failed.");
  expect(screen.queryByRole("columnheader", { name: "Reason" })).toBeNull();
  first.unmount();

  const id = "run_20260923T043011Z_929fe95f";
  renderApp(`/runs/${id}`, { [`/api/runs/${id}`]: fixtures.runInconclusive });
  expect(await screen.findByRole("columnheader", { name: "Reason" })).toBeInTheDocument();
  expect(screen.getAllByRole("cell", { name: "engine did not finish" }).length).toBeGreaterThan(0);
});

test("header facts wrap instead of clipping values such as the start time", async () => {
  renderApp(`/runs/${FAIL}`, { [`/api/runs/${FAIL}`]: fixtures.runFail });
  const started = (await screen.findByText("Started")).nextElementSibling!;
  expect(started.tagName).toBe("DD");
  expect(started).not.toHaveClass("truncate");
});

test("an activity renders slices and no verdict", async () => {
  const id = "activity_20260923T054400Z_5e1f0c2a";
  renderApp(`/runs/${id}`, { [`/api/runs/${id}`]: fixtures.runActivity });
  expect(await screen.findByText("Background activity")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "run_20260923T065600Z_467925ae" })).toHaveAttribute("href", "/runs/run_20260923T065600Z_467925ae");
  expect(screen.getByText("2 of 2")).toBeInTheDocument();
  expect(screen.queryByRole("tab")).toBeNull();
  expect(screen.queryByText("Pass")).toBeNull();
});

test("loading, not found and error states", async () => {
  const loading = renderApp("/runs/x", { "/api/runs/x": PENDING });
  expect(screen.getByRole("status", { name: "Loading run" })).toBeInTheDocument();
  loading.unmount();
  const missing = renderApp("/runs/x", {});
  expect(await screen.findByText("Run not found")).toBeInTheDocument();
  missing.unmount();
  renderApp("/runs/x", { "/api/runs/x": [500, { error: "result.json is corrupt" }] });
  expect(await screen.findByRole("alert")).toHaveTextContent("result.json is corrupt");
});

test("explains the run in plain English and asks the local CLI on demand", async () => {
  const user = userEvent.setup();
  const answer = { cli: "claude", created_at: "2026-09-26T08:00:00+00:00", text: "What happened\nTenant A read tenant B data." };
  const { fetchMock } = renderApp(`/runs/${FAIL}`, {
    [`/api/runs/${FAIL}`]: fixtures.runFail,
    [`/api/runs/${FAIL}/explain`]: answer,
  });
  expect(await screen.findByText("7 of 9 checks failed, starting with: cross tenant balance read blocked.")).toBeInTheDocument();
  const failed = screen.getByRole("region", { name: "What failed" });
  expect(within(failed).getByText("Cross tenant balance read blocked: expected [401, 403, 404] but got 200 in 3 of 9 samples.")).toBeInTheDocument();
  expect(screen.getByRole("region", { name: "Next steps" })).toHaveTextContent("accepted requests it should have refused");

  await user.click(await screen.findByRole("button", { name: "Explain with AI" }));
  expect(await screen.findByText(/Tenant A read tenant B data\./)).toBeInTheDocument();
  expect(screen.getByText(/Written by claude/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Explain again" })).toBeEnabled();
  const call = fetchMock.mock.calls.find(([url]) => String(url).endsWith("/explain")) as unknown as [string, RequestInit];
  expect(call[1].method).toBe("POST");
  expect(call[1].headers).toMatchObject({ "X-LiteTraffic-Action": "explain" });
});

test("AI explanation shows server errors", async () => {
  const user = userEvent.setup();
  renderApp(`/runs/${FAIL}`, {
    [`/api/runs/${FAIL}`]: fixtures.runFail,
    [`/api/runs/${FAIL}/explain`]: [502, { error: "claude failed: not logged in" }],
  });
  await user.click(await screen.findByRole("button", { name: "Explain with AI" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("claude failed: not logged in");
});

test("AI explanation button is enabled with an API provider", async () => {
  renderApp(`/runs/${FAIL}`, { [`/api/runs/${FAIL}`]: fixtures.runFail, "/api/meta": { ...fixtures.meta, explain_cli: "gpt-4o-mini" } });
  expect(await screen.findByText(/Uses the gpt-4o-mini API\./)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Explain with AI" })).toBeEnabled();
});

test("AI explanation button is disabled when no provider is available", async () => {
  renderApp(`/runs/${FAIL}`, { [`/api/runs/${FAIL}`]: fixtures.runFail, "/api/meta": { ...fixtures.meta, explain_cli: null } });
  expect(await screen.findByText(/install the claude or codex CLI, to get a written explanation\./)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Explain with AI" })).toBeDisabled();
});

test("server tab shows container peaks and top error signatures, only when captured", async () => {
  const user = userEvent.setup();
  const first = renderApp(`/runs/${FAIL}`, { [`/api/runs/${FAIL}`]: fixtures.runFail });
  await screen.findByText("7 of 9 assertions failed.");
  await user.click(screen.getByRole("tab", { name: "Server" }));
  const api = screen.getByRole("cell", { name: "api" }).closest("tr")!;
  expect(within(api).getByText("0.4%")).toBeInTheDocument();
  expect(within(api).getByText("63.8%")).toBeInTheDocument();
  expect(within(api).getByText("177 MiB")).toBeInTheDocument();
  expect(within(api).getByText("37")).toBeInTheDocument();
  const signatures = screen.getByRole("table", { name: "Error signatures for api" });
  expect(within(signatures).getByText("ERROR tenant scope missing for org <uuid> on GET /balances/<uuid>")).toBeInTheDocument();
  expect(within(signatures).getByText("27")).toBeInTheDocument();
  // db logged no errors, so it has no signature card.
  expect(screen.queryByRole("table", { name: "Error signatures for db" })).toBeNull();
  first.unmount();

  const id = "run_20260923T043011Z_929fe95f";
  renderApp(`/runs/${id}`, { [`/api/runs/${id}`]: fixtures.runInconclusive });
  await screen.findByText("Evidence was missing or incomplete, so the run cannot pass.");
  expect(screen.queryByRole("tab", { name: "Server" })).toBeNull();
});

test("server tab flags truncated logs and capture problems", async () => {
  const user = userEvent.setup();
  const server = fixtures.runFail.server!;
  const detail = {
    ...fixtures.runFail,
    server: { ...server, problems: ["worker: Error: No such container: worker"], containers: { api: { ...server.containers.api, truncated: true } } },
  };
  renderApp(`/runs/${FAIL}`, { [`/api/runs/${FAIL}`]: detail });
  await screen.findByText("7 of 9 assertions failed.");
  await user.click(screen.getByRole("tab", { name: "Server" }));
  expect(screen.getByText("truncated")).toBeInTheDocument();
  expect(within(screen.getByRole("list", { name: "Capture problems" })).getByText("worker: Error: No such container: worker")).toBeInTheDocument();
});
