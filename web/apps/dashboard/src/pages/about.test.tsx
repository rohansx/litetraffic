import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test } from "vitest";
import { renderApp } from "@/test/render";

test("about explains verdicts and shows the runs folder", async () => {
  renderApp("/about");
  expect(screen.getByRole("heading", { name: "What is this dashboard?" })).toBeInTheDocument();
  expect(await screen.findAllByText("/home/dev/acme-api/runs")).not.toHaveLength(0);
  expect(screen.getByText("Every declared check held with complete evidence.")).toBeInTheDocument();
});

test("theme toggle forces light or dark on the root element", async () => {
  const user = userEvent.setup();
  renderApp("/about");
  await user.click(screen.getByRole("button", { name: "Theme: system" }));
  await user.click(await screen.findByRole("menuitemradio", { name: "Dark" }));
  expect(document.documentElement).toHaveClass("dark");
  await user.click(screen.getByRole("button", { name: "Theme: dark" }));
  await user.click(await screen.findByRole("menuitemradio", { name: "System" }));
  expect(document.documentElement).not.toHaveClass("dark");
});

test("unknown paths show a not-found page", () => {
  renderApp("/nope");
  expect(screen.getByRole("heading", { name: "Page not found" })).toBeInTheDocument();
});
