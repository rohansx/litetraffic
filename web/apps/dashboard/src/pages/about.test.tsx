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

test("sidebar footer shows the runs folder and version, and its menu forces light or dark", async () => {
  const user = userEvent.setup();
  renderApp("/about");
  const footer = await screen.findByRole("button", { name: "Runs folder /home/dev/acme-api/runs, LiteTraffic v0.1.0, theme system" });
  await user.click(footer);
  await user.click(await screen.findByRole("menuitemradio", { name: "Dark" }));
  expect(document.documentElement).toHaveClass("dark");
  await user.click(screen.getByRole("button", { name: /theme dark$/ }));
  await user.click(await screen.findByRole("menuitemradio", { name: "System" }));
  expect(document.documentElement).not.toHaveClass("dark");
});

test("unknown paths show a not-found page", () => {
  renderApp("/nope");
  expect(screen.getByRole("heading", { name: "Page not found" })).toBeInTheDocument();
});
