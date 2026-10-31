import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { vi } from "vitest";
import { AppRoutes } from "@/App";
import { fixtures } from "@/api/__fixtures__";

type Reply = unknown | [status: number, body: unknown];
/** A function reply is called per request with the parsed JSON body, so a poll can answer differently each time. */
type Handler = (body: unknown) => Reply | Promise<Reply>;
/** Pending forever: for loading states. */
export const PENDING = Symbol("pending");

const DEFAULTS: Record<string, Reply> = {
  "/api/meta": fixtures.meta,
  "/api/runs": fixtures.runs,
  "/api/scenarios": fixtures.scenarios,
  "/api/jobs": [],
  "/api/library": fixtures.library,
};

/**
 * Stub fetch by pathname (query ignored unless the key has one) and render the app at `path`.
 * A key may start with a method ("POST /api/library") to match only that method; plain keys match any.
 */
export function renderApp(path: string, routes: Record<string, Reply | Handler | typeof PENDING> = {}) {
  const table = { ...DEFAULTS, ...routes };
  const fetchMock = vi.fn(async (input: string, init?: RequestInit) => {
    const url = new URL(input, "http://localhost");
    const method = init?.method ?? "GET";
    const candidates = [`${method} ${url.pathname}${url.search}`, `${method} ${url.pathname}`, url.pathname + url.search, url.pathname];
    const key = candidates.find((candidate) => candidate in table) ?? url.pathname;
    let reply = table[key];
    if (typeof reply === "function") reply = await (reply as Handler)(typeof init?.body === "string" ? JSON.parse(init.body) : undefined);
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
