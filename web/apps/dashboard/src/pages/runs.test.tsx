import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test } from "vitest";
import { renderApp } from "@/test/render";

const dataRows = () => screen.getAllByRole("row").slice(1);

test("lists every run with verdict text in its badge", async () => {
  renderApp("/runs");
  expect(await screen.findByRole("link", { name: "run_20260923T065633Z_13376b99" })).toHaveAttribute(
    "href",
    "/runs/run_20260923T065633Z_13376b99",
  );
  expect(screen.getByText("16 of 16 shown")).toBeInTheDocument();
  expect(screen.getByText("runs · 16 in view")).toHaveClass("window-card-header");
  expect(screen.getByText("evidence on disk")).toHaveClass("eyebrow");
  // dashboard-01 pagination: 10 rows per page.
  expect(dataRows()).toHaveLength(10);
  expect(screen.getByText("Page 1 of 2")).toBeInTheDocument();
  await userEvent.setup().click(screen.getByRole("button", { name: "Go to next page" }));
  expect(dataRows()).toHaveLength(6);
  expect(screen.getByText("Page 2 of 2")).toBeInTheDocument();
  // Series have no detail page and cannot be compared.
  const series = dataRows().find((row) => row.textContent?.includes("series_20260923T043000Z_9a1b2c3d"))!;
  expect(within(series).queryByRole("link", { name: /series_/ })).toBeNull();
  expect(within(series).queryByRole("checkbox")).toBeNull();
});

test("filters by search text and seed", async () => {
  const user = userEvent.setup();
  renderApp("/runs");
  await screen.findByText("16 of 16 shown");
  await user.type(screen.getByRole("searchbox", { name: "Search runs" }), "tenant-isolation-kit");
  expect(dataRows()).toHaveLength(2);
  await user.type(screen.getByRole("textbox", { name: "Seed" }), "7");
  expect(await screen.findByText("No runs match these filters")).toBeInTheDocument();
  await user.click(screen.getAllByRole("button", { name: "Clear filters" })[0]!);
  expect(screen.getByText("16 of 16 shown")).toBeInTheDocument();
});

test("filters by verdict and scenario from the URL", async () => {
  renderApp("/runs?verdict=fail&scenario=acme-tenant-isolation-v2");
  await screen.findByText("3 of 16 shown");
  for (const row of dataRows()) expect(within(row).getAllByText("Fail").length).toBeGreaterThan(0);
});

test("phones get the verdict inside the run cell and drop the wide columns", async () => {
  renderApp("/runs");
  await screen.findByText("16 of 16 shown");
  // A 390px screen cannot fit run id + verdict + lifecycle + seed + finished; the verdict column was clipped off screen.
  for (const name of ["Verdict", "Lifecycle", "Seed", "Finished"]) {
    expect(screen.getByRole("columnheader", { name })).toHaveClass("hidden", "md:table-cell");
  }
  const runCell = within(dataRows()[0]!).getAllByRole("cell")[1]!;
  const phoneLine = runCell.querySelector(".md\\:hidden")!;
  expect(phoneLine).toHaveTextContent(/Fail|Pass|Inconclusive|Error|Activity/);
});

test("filters by verdict with the select", async () => {
  const user = userEvent.setup();
  renderApp("/runs");
  await screen.findByText("16 of 16 shown");
  await user.click(screen.getByRole("combobox", { name: "Verdict" }));
  await user.click(await screen.findByRole("option", { name: "Pass" }));
  expect(screen.getByText("9 of 16 shown")).toBeInTheDocument();
});

test("compare needs exactly two runs and puts the older one first", async () => {
  const user = userEvent.setup();
  renderApp("/runs", { "/api/diff": (await import("@/api/__fixtures__")).fixtures.diff });
  await screen.findByText("16 of 16 shown");
  const compare = screen.getByRole("button", { name: /Compare/ });
  expect(compare).toBeDisabled();
  await user.click(screen.getByRole("checkbox", { name: "Select run_20260923T065633Z_13376b99" }));
  expect(compare).toBeDisabled();
  await user.click(screen.getByRole("checkbox", { name: "Select run_20260923T054528Z_a2b342d2" }));
  expect(compare).toBeEnabled();
  await user.click(compare);
  expect(await screen.findByRole("heading", { name: "Compare runs" })).toBeInTheDocument();
  expect(screen.getByRole("combobox", { name: "Baseline" })).toHaveTextContent("run_20260923T054528Z_a2b342d2");
  expect(screen.getByRole("combobox", { name: "Candidate" })).toHaveTextContent("run_20260923T065633Z_13376b99");
});

test("auto-refresh is on by default and can be turned off", async () => {
  const user = userEvent.setup();
  renderApp("/runs");
  const toggle = await screen.findByRole("checkbox", { name: "Auto-refresh every 5 seconds" });
  expect(toggle).toBeChecked();
  await user.click(toggle);
  expect(toggle).not.toBeChecked();
});

test("empty and error states", async () => {
  const empty = renderApp("/runs", { "/api/runs": [] });
  expect(await screen.findByText("No runs yet")).toBeInTheDocument();
  empty.unmount();
  renderApp("/runs", { "/api/runs": [500, { error: "boom" }] });
  expect(await screen.findByRole("alert")).toHaveTextContent("boom");
});

test("view tabs split runs, series and activity, with counts", async () => {
  const user = userEvent.setup();
  renderApp("/runs");
  await screen.findByText("16 of 16 shown");
  expect(screen.getByRole("tab", { name: "Verify runs 14" })).toBeInTheDocument();
  await user.click(screen.getByRole("tab", { name: "Activity 1" }));
  expect(screen.getByText("1 of 16 shown")).toBeInTheDocument();
  expect(dataRows()).toHaveLength(1);
  expect(within(dataRows()[0]!).getByRole("link", { name: "activity_20260923T054400Z_5e1f0c2a" })).toBeInTheDocument();
});

test("columns can be hidden from the customize menu", async () => {
  const user = userEvent.setup();
  renderApp("/runs");
  await screen.findByText("16 of 16 shown");
  expect(screen.getByRole("columnheader", { name: "Lifecycle" })).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: /Customize columns/ }));
  await user.click(await screen.findByRole("menuitemcheckbox", { name: "Lifecycle" }));
  expect(screen.queryByRole("columnheader", { name: "Lifecycle" })).toBeNull();
  // The run column cannot be hidden.
  expect(screen.queryByRole("menuitemcheckbox", { name: "Run" })).toBeNull();
});

test("a third tick replaces the oldest selection", async () => {
  const user = userEvent.setup();
  renderApp("/runs");
  await screen.findByText("16 of 16 shown");
  const box = (id: string) => screen.getByRole("checkbox", { name: `Select ${id}` });
  await user.click(box("run_20260923T065633Z_13376b99"));
  await user.click(box("run_20260923T065626Z_2502f5dd"));
  await user.click(box("run_20260923T065614Z_46490021"));
  expect(box("run_20260923T065633Z_13376b99")).not.toBeChecked();
  expect(box("run_20260923T065626Z_2502f5dd")).toBeChecked();
  expect(box("run_20260923T065614Z_46490021")).toBeChecked();
  expect(screen.getByRole("button", { name: /Compare 2\/2/ })).toBeEnabled();
});
