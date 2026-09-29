import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { vi } from "vitest";
import { AppRoutes } from "@/App";
import { fixtures } from "@/api/__fixtures__";

type Reply = unknown | [status: number, body: unknown];
/** Pending forever: for loading states. */
export const PENDING = Symbol("pending");

const DEFAULTS: Record<string, Reply> = {
  "/api/meta": fixtures.meta,
  "/api/runs": fixtures.runs,
  "/api/scenarios": fixtures.scenarios,
};

/** Stub fetch by pathname (query ignored unless the key has one) and render the app at `path`. */
export function renderApp(path: string, routes: Record<string, Reply | typeof PENDING> = {}) {
  const table = { ...DEFAULTS, ...routes };
  const fetchMock = vi.fn(async (input: string) => {
    const url = new URL(input, "http://localhost");
    const key = url.pathname + url.search in table ? url.pathname + url.search : url.pathname;
    const reply = table[key];
    if (reply === PENDING) return new Promise<Response>(() => {});
    if (reply === undefined) return new Response(JSON.stringify({ error: `no fixture for ${key}` }), { status: 404 });
    const [status, body] = Array.isArray(reply) && typeof reply[0] === "number" && reply.length === 2 ? reply : [200, reply];
    return new Response(JSON.stringify(body), { status });
  });
  vi.stubGlobal("fetch", fetchMock);
  const view = render(
    <MemoryRouter initialEntries={[path]}>
      <AppRoutes />
    </MemoryRouter>,
  );
  return { ...view, fetchMock };
}
