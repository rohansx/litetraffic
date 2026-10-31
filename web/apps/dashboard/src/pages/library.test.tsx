import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test } from "vitest";
import { fixtures } from "@/api/__fixtures__";
import { PENDING, renderApp } from "@/test/render";

const NAME = "my-api-tenant-isolation";

test("lists saved tests with journeys, checks, secrets, setup badge and run links", async () => {
  renderApp("/library");
  const table = await screen.findByRole("table", { name: "Saved tests" });
  const rows = within(table).getAllByRole("row").slice(1);
  expect(rows).toHaveLength(3);

  const kit = rows[0]!;
  expect(within(kit).getByRole("link", { name: NAME })).toHaveAttribute("href", `/library/${NAME}`);
  expect(within(kit).getByText("10")).toBeInTheDocument();
  expect(within(kit).getByText("5")).toBeInTheDocument();
  expect(within(kit).getByText("TENANT_A_TOKEN")).toBeInTheDocument();
  expect(within(kit).getByRole("link", { name: `Run ${NAME}` })).toHaveAttribute("href", `/new?scenario=${NAME}`);
  expect(within(kit).queryByText("Setup command")).not.toBeInTheDocument();

  expect(within(rows[1]!).getByText("Setup command")).toBeInTheDocument();
  // A bundle that no longer validates says why and cannot be run.
  expect(within(rows[2]!).getByText(/Invalid: manifest.json: unknown field/)).toBeInTheDocument();
  expect(within(rows[2]!).getByRole("button", { name: "Run broken-bundle" })).toBeDisabled();
  expect(screen.getAllByRole("link", { name: "New test" }).every((link) => link.getAttribute("href") === "/new")).toBe(true);
});

test("delete asks first, then calls the library and refreshes", async () => {
  const user = userEvent.setup();
  let list = fixtures.library;
  const { fetchMock } = renderApp("/library", {
    "/api/library": () => list,
    "DELETE /api/library/checkout-smoke": () => {
      list = list.filter((entry) => entry.name !== "checkout-smoke");
      return { deleted: "checkout-smoke" };
    },
  });
  await user.click(await screen.findByRole("button", { name: "Delete checkout-smoke" }));
  const dialog = await screen.findByRole("dialog", { name: "Delete checkout-smoke?" });
  expect(fetchMock.mock.calls.some(([, init]) => init?.method === "DELETE")).toBe(false);
  await user.click(within(dialog).getByRole("button", { name: "Delete" }));

  await waitFor(() => expect(screen.queryByRole("button", { name: "Delete checkout-smoke" })).not.toBeInTheDocument());
  const call = fetchMock.mock.calls.find(([, init]) => init?.method === "DELETE")!;
  expect(call[0]).toBe("/api/library/checkout-smoke");
  expect(call[1]?.headers).toMatchObject({ "X-LiteTraffic-Action": "library-delete" });
});

test("a refused delete keeps the dialog open with the server's reason; cancel closes it", async () => {
  const user = userEvent.setup();
  renderApp("/library", { "DELETE /api/library/checkout-smoke": [500, { error: "disk is read-only" }] });
  await user.click(await screen.findByRole("button", { name: "Delete checkout-smoke" }));
  const dialog = await screen.findByRole("dialog");
  await user.click(within(dialog).getByRole("button", { name: "Delete" }));
  expect(await within(dialog).findByText("disk is read-only")).toBeInTheDocument();
  await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
});

test("library loading, empty and error states", async () => {
  const loading = renderApp("/library", { "/api/library": PENDING });
  expect(screen.getByRole("status", { name: "Loading library" })).toBeInTheDocument();
  loading.unmount();
  const empty = renderApp("/library", { "/api/library": [] });
  expect(await screen.findByText("Your library is empty")).toBeInTheDocument();
  empty.unmount();
  renderApp("/library", { "/api/library": [500, { error: "library folder unreadable" }] });
  expect(await screen.findByRole("alert")).toHaveTextContent("library folder unreadable");
});

test("detail shows the inspect summary and the files read-only", async () => {
  renderApp(`/library/${NAME}`, { [`/api/library/${NAME}`]: fixtures.libraryDetail });
  expect(await screen.findByText("What this test does")).toBeInTheDocument();
  expect(screen.getByText("Planned journeys")).toBeInTheDocument();
  expect(screen.getByText("cross_tenant_read_blocked")).toBeInTheDocument();
  expect(screen.getByText("warmup: 1 journeys/s for 1 s")).toBeInTheDocument();
  expect(screen.getByText(/TENANT_A_TOKEN, TENANT_B_TOKEN/)).toBeInTheDocument();
  const manifest = screen.getByLabelText("Contents of manifest.json");
  expect(manifest.tagName).toBe("PRE");
  expect(screen.getByLabelText("Contents of journeys.js")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Run" })).toHaveAttribute("href", `/new?scenario=${NAME}`);
});

test("detail delete returns to the library; unknown names are not found", async () => {
  const user = userEvent.setup();
  renderApp(`/library/${NAME}`, {
    [`/api/library/${NAME}`]: fixtures.libraryDetail,
    [`DELETE /api/library/${NAME}`]: { deleted: NAME },
  });
  await user.click(await screen.findByRole("button", { name: "Delete" }));
  await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Delete" }));
  expect(await screen.findByRole("heading", { level: 1, name: "Library" })).toBeInTheDocument();
});

test("detail: not found and server error", async () => {
  const missing = renderApp("/library/nope", { "/api/library/nope": [404, { error: "no such test" }] });
  expect(await screen.findByText("Test not found")).toBeInTheDocument();
  missing.unmount();
  renderApp("/library/bad", { "/api/library/bad": [500, { error: "boom" }] });
  expect(await screen.findByRole("alert")).toHaveTextContent("boom");
});
