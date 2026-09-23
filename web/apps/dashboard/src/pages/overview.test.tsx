import { screen, within } from "@testing-library/react";
import { expect, test } from "vitest";
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
