import { screen, within } from "@testing-library/react";
import { expect, test } from "vitest";
import { fixtures } from "@/api/__fixtures__";
import { PENDING, renderApp } from "@/test/render";

const NAME = "acme-tenant-isolation-v2";
const TREND = `/api/scenarios/${NAME}/trend`;

test("scenario list shows latest verdict and run count", async () => {
  renderApp("/scenarios");
  const link = await screen.findByRole("link", { name: NAME });
  const row = link.closest("tr")!;
  expect(within(row).getByText("Fail")).toBeInTheDocument();
  expect(within(row).getByText("3")).toBeInTheDocument();
  expect(screen.getAllByRole("row")).toHaveLength(6);
});

test("trend page has a data table with every point, newest first", async () => {
  renderApp(`/scenarios/${NAME}`, { [TREND]: fixtures.trend });
  const table = await screen.findByRole("table", { name: "Trend data" });
  const rows = within(table).getAllByRole("row").slice(1);
  expect(rows).toHaveLength(fixtures.trend.length);
  expect(within(rows[0]!).getByRole("link", { name: "run_20260923T065626Z_2502f5dd" })).toBeInTheDocument();
  expect(within(rows[0]!).getByText("17.1 ms")).toBeInTheDocument();
  expect(within(rows[0]!).getByText("Fail")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "View runs" })).toHaveAttribute("href", `/runs?scenario=${NAME}`);
});

test("trend loading, empty, not found and error states", async () => {
  const loading = renderApp(`/scenarios/${NAME}`, { [TREND]: PENDING });
  expect(screen.getByRole("status", { name: "Loading trend" })).toBeInTheDocument();
  loading.unmount();
  const empty = renderApp(`/scenarios/${NAME}`, { [TREND]: [] });
  expect(await screen.findByText("No finished runs yet")).toBeInTheDocument();
  empty.unmount();
  const missing = renderApp(`/scenarios/${NAME}`, {});
  expect(await screen.findByText("Scenario not found")).toBeInTheDocument();
  missing.unmount();
  renderApp(`/scenarios/${NAME}`, { [TREND]: [500, { error: "trend failed" }] });
  expect(await screen.findByRole("alert")).toHaveTextContent("trend failed");
});
