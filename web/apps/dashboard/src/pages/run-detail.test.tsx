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
