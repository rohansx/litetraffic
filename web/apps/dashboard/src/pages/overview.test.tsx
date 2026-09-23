import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test } from "vitest";
import { fixtures } from "@/api/__fixtures__";
import { bucketRuns } from "@/components/chart-area-interactive";
import { PENDING, renderApp } from "@/test/render";

test("shows KPIs computed from the runs list", async () => {
  renderApp("/");
  const kpis = await screen.findByRole("region", { name: "Key figures" });
  // 14 verify runs (activity and series excluded), 9 passed.
  expect(within(kpis).getByText("14")).toBeInTheDocument();
  expect(within(kpis).getByText("64%")).toBeInTheDocument();
  expect(within(kpis).getByText("9 of 14 judged runs passed")).toBeInTheDocument();
  // Latest runs of both tenant-isolation scenarios failed.
  expect(within(kpis).getByText("2")).toBeInTheDocument();
  expect(within(kpis).getByText("acme-position-draft-create-v2")).toBeInTheDocument();
  // Trend badges: every run finished within a day of the newest one.
  expect(within(kpis).getByText("+14 in 24h")).toBeInTheDocument();
  expect(within(kpis).getByText("2 of 5")).toBeInTheDocument();
});

test("runs-over-time chart summarizes the chosen range", async () => {
  const user = userEvent.setup();
  renderApp("/");
  expect(await screen.findByRole("img", { name: "Last 24 hours: 14 judged runs, 9 passed, 5 did not pass" })).toBeInTheDocument();
  await user.click(screen.getByRole("radio", { name: "Last 7 days" }));
  expect(screen.getByRole("img", { name: /^Last 7 days: 14 judged runs/ })).toBeInTheDocument();
});

test("bucketRuns counts judged verify runs per hour, ending at the newest run", () => {
  const hours = bucketRuns(fixtures.runs, "24h");
  expect(hours).toHaveLength(24);
  expect(hours.reduce((sum, b) => sum + b.passed, 0)).toBe(9);
  expect(hours.reduce((sum, b) => sum + b.notPassed, 0)).toBe(5);
  // The newest run (06:56Z) lands in the last bucket.
  expect(hours.at(-1)).toMatchObject({ start: Date.parse("2026-09-23T06:00:00Z") });
  expect(hours.at(-1)!.passed + hours.at(-1)!.notPassed).toBeGreaterThan(0);
  expect(bucketRuns(fixtures.runs, "7d")).toHaveLength(7);
  expect(bucketRuns([], "30d")).toEqual([]);
});

test("lists recent failures newest first, linked to their runs", async () => {
  renderApp("/");
  const list = await screen.findByRole("list", { name: "Recent failures" });
  const links = within(list).getAllByRole("link");
  expect(links).toHaveLength(5);
  expect(links[0]).toHaveAttribute("href", "/runs/run_20260923T065626Z_2502f5dd");
  expect(within(links[0]!).getByText("Fail")).toBeInTheDocument();
});

test("shows a status row per scenario with its latest verdict and history", async () => {
  renderApp("/");
  const grid = await screen.findByRole("list", { name: "Scenario status" });
  const rows = within(grid).getAllByRole("listitem").filter((item) => item.parentElement === grid);
  expect(rows).toHaveLength(5);
  const kit = rows.find((row) => row.textContent?.includes("acme-tenant-isolation-kit"))!;
  expect(within(kit).getByRole("link", { name: "acme-tenant-isolation-kit" })).toHaveAttribute("href", "/scenarios/acme-tenant-isolation-kit");
  expect(within(kit).getByRole("img", { name: "run_20260923T065538Z_01fc0c38: Fail" })).toBeInTheDocument();
});

test("loading, empty and error states", async () => {
  const loading = renderApp("/", { "/api/runs": PENDING });
  expect(screen.getByRole("status", { name: "Loading overview" })).toBeInTheDocument();
  loading.unmount();

  const empty = renderApp("/", { "/api/runs": [], "/api/scenarios": [] });
  expect(await screen.findByText("No runs yet")).toBeInTheDocument();
  empty.unmount();

  renderApp("/", { "/api/runs": [500, { error: "runs folder unreadable" }] });
  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("runs folder unreadable");
  expect(within(alert).getByRole("button", { name: "Try again" })).toBeInTheDocument();
});
