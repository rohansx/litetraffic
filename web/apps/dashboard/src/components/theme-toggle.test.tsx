import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import { renderApp } from "@/test/render";

afterEach(() => {
  vi.unstubAllGlobals();
  document.documentElement.classList.remove("light", "dark");
  localStorage.clear();
});

test("the header has a visible theme switch that sets light mode", async () => {
  const user = userEvent.setup();
  renderApp("/");
  const header = (await screen.findAllByRole("banner"))[0]!;

  await user.click(within(header).getByRole("button", { name: /theme/i }));
  await user.click(await screen.findByRole("menuitemradio", { name: "Light" }));

  expect(document.documentElement.classList.contains("light")).toBe(true);
  expect(localStorage.getItem("litetraffic-theme")).toBe("light");
});

test("header and sidebar footer share one theme", async () => {
  const user = userEvent.setup();
  renderApp("/");
  const header = (await screen.findAllByRole("banner"))[0]!;

  await user.click(within(header).getByRole("button", { name: /theme/i }));
  await user.click(await screen.findByRole("menuitemradio", { name: "Dark" }));

  // The footer's accessible name reports the theme it shows.
  expect(screen.getByRole("button", { name: /theme dark/i })).toBeInTheDocument();
  expect(document.documentElement.classList.contains("dark")).toBe(true);
});
