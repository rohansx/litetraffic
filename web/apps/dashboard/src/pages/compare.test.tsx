import { screen, within } from "@testing-library/react";
import { expect, test } from "vitest";
import { fixtures } from "@/api/__fixtures__";
import { PENDING, renderApp } from "@/test/render";

const url = "/compare?baseline=a&candidate=b";

test("shows the verdict, correctness and p95 change of comparable runs", async () => {
  const { fetchMock } = renderApp(url, { "/api/diff": fixtures.diff });
  expect(await screen.findByText("p95 gate is inconclusive")).toBeInTheDocument();
  expect(fetchMock.mock.calls.map(([called]) => called)).toContain("/api/diff?baseline=a&candidate=b");
  expect(screen.getByText("No correctness regression.")).toBeInTheDocument();
  expect(screen.getByText("No assertion regressed.")).toBeInTheDocument();
  expect(screen.getByText("Inconclusive, limit 10%")).toBeInTheDocument();
  expect(screen.getByText("+5.6%")).toBeInTheDocument();
  const op = screen.getByRole("cell", { name: "usage-debit" }).closest("tr")!;
  expect(within(op).getByText("36.3 ms")).toBeInTheDocument();
  expect(within(op).getByText("+6.9%")).toBeInTheDocument();
  expect(screen.queryByText(/Not comparable/)).toBeNull();
});

test("explains incompatibilities of runs that cannot be judged", async () => {
  renderApp(url, { "/api/diff": fixtures.diffIncomparable });
  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("Not comparable, so performance is not judged");
  expect(alert).toHaveTextContent("The scenario file changed between the runs");
  expect(screen.getByText("Not comparable")).toBeInTheDocument();
});

test("lists per-assertion regressions", async () => {
  const regressed = {
    ...fixtures.diff,
    verdict: "fail",
    correctness: { baseline_verdict: "pass", candidate_verdict: "fail", regression: true, assertion_regressions: ["one_debit_response"] },
  };
  renderApp(url, { "/api/diff": regressed });
  expect(await screen.findByText("The candidate regressed.")).toBeInTheDocument();
  expect(within(screen.getByRole("list", { name: "Assertion regressions" })).getByText("one_debit_response")).toBeInTheDocument();
});

test("a 409 says the runs cannot be compared", async () => {
  renderApp(url, { "/api/diff": [409, fixtures.diffError] });
  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("These runs cannot be compared");
  expect(alert).toHaveTextContent(fixtures.diffError.error);
});

test("empty and loading states", async () => {
  const empty = renderApp("/compare");
  expect(await screen.findByText("Pick two runs to compare")).toBeInTheDocument();
  empty.unmount();
  renderApp(url, { "/api/diff": PENDING });
  expect(screen.getByRole("status", { name: "Comparing runs" })).toBeInTheDocument();
});
