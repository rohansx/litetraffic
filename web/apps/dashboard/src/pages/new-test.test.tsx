import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test } from "vitest";
import { fixtures } from "@/api/__fixtures__";
import { JOB_POLL } from "@/lib/use-job";
import { renderApp } from "@/test/render";

const NAME = "my-api-tenant-isolation";
const DETAIL = { [`/api/library/${NAME}`]: fixtures.libraryDetail };
const JOB = fixtures.jobRunning;

beforeEach(() => {
  JOB_POLL.ms = 10;
});

function bodyOf(fetchMock: ReturnType<typeof renderApp>["fetchMock"], method: string, path: string) {
  const call = fetchMock.mock.calls.findLast(([url, init]) => url === path && init?.method === method);
  return call && JSON.parse(call[1]!.body as string);
}

test("wizard: choose, aim with one password input per secret, confirm, run", async () => {
  const user = userEvent.setup();
  const { fetchMock } = renderApp("/new", {
    ...DETAIL,
    "POST /api/jobs": [202, { id: JOB.id }],
    [`/api/jobs/${JOB.id}`]: JOB,
  });

  const steps = await screen.findByRole("list", { name: "Steps" });
  expect(within(steps).getByText("Choose a test").closest("li")).toHaveAttribute("aria-current", "step");
  await user.click(await screen.findByRole("button", { name: `Use ${NAME}` }));

  // Step 2: the target, one password input per secret_env name, and the permission gate.
  expect(await screen.findByRole("form", { name: "Aim the test" })).toBeInTheDocument();
  expect(within(screen.getByRole("list", { name: "Steps" })).getByText("Aim it").closest("li")).toHaveAttribute("aria-current", "step");
  expect(screen.getByText("What this test will do")).toBeInTheDocument();
  expect(screen.getByLabelText("TENANT_A_TOKEN")).toHaveAttribute("type", "password");
  expect(screen.getByLabelText("TENANT_B_TOKEN")).toHaveAttribute("type", "password");
  const next = screen.getByRole("button", { name: "Continue" });
  expect(next).toBeDisabled();

  await user.type(screen.getByLabelText("Target URL"), "http://127.0.0.1:8000");
  expect(screen.queryByText("This is not a local address")).not.toBeInTheDocument();
  expect(next).toBeDisabled(); // no permission yet
  await user.type(screen.getByLabelText("TENANT_A_TOKEN"), "token-a");
  await user.type(screen.getByLabelText("TENANT_B_TOKEN"), "token-b");
  await user.click(screen.getByRole("checkbox", { name: /I own this app or have permission/ }));
  expect(next).toBeEnabled();
  await user.click(next);

  // Step 3: names only, and the Run button needs the permission.
  const summary = await screen.findByText("Ready to run");
  expect(summary).toBeInTheDocument();
  expect(screen.getByText("TENANT_A_TOKEN, TENANT_B_TOKEN")).toBeInTheDocument();
  expect(screen.queryByText("token-a")).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Run test" }));

  expect(await screen.findByRole("log", { name: "Run log" })).toBeInTheDocument();
  expect(bodyOf(fetchMock, "POST", "/api/jobs")).toEqual({
    scenario: NAME,
    target: "http://127.0.0.1:8000",
    seed: 0,
    repeat: 1,
    env: { TENANT_A_TOKEN: "token-a", TENANT_B_TOKEN: "token-b" },
    confirm_target: true,
  });
  const post = fetchMock.mock.calls.find(([url, init]) => url === "/api/jobs" && init?.method === "POST");
  expect(post?.[1]?.headers).toMatchObject({ "X-LiteTraffic-Action": "job-run", "Content-Type": "application/json" });
});

test("?scenario= jumps to step 2; bad input and remote hosts are called out", async () => {
  const user = userEvent.setup();
  renderApp(`/new?scenario=${NAME}`, DETAIL);
  const target = await screen.findByLabelText("Target URL");
  await user.type(target, "example.com");
  expect(screen.getByText("Enter an address that starts with http:// or https://.")).toBeInTheDocument();
  await user.clear(target);
  await user.type(target, "https://staging.example.com");
  expect(screen.getByText("This is not a local address")).toBeInTheDocument();
  expect(screen.getByText(/Prefer a staging copy/)).toBeInTheDocument();
  await user.clear(screen.getByLabelText("Repeat"));
  await user.type(screen.getByLabelText("Repeat"), "21");
  expect(screen.getByText("Repeat between 1 and 20 times.")).toBeInTheDocument();
  await user.click(screen.getByRole("checkbox", { name: /I own this app/ }));
  expect(screen.getByRole("button", { name: "Continue" })).toBeDisabled();
});

test("a busy console says so and links to the running job", async () => {
  const user = userEvent.setup();
  renderApp(`/new?scenario=${NAME}`, {
    ...DETAIL,
    "POST /api/jobs": [409, { error: "a job is already running", job_id: "job_busy" }],
  });
  await user.type(await screen.findByLabelText("Target URL"), "http://localhost:3000");
  await user.click(screen.getByRole("checkbox", { name: /I own this app/ }));
  await user.click(screen.getByRole("button", { name: "Continue" }));
  await user.click(await screen.findByRole("button", { name: "Run test" }));
  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("A test is already running");
  expect(within(alert).getByRole("link", { name: "Open the running job" })).toHaveAttribute("href", "/jobs/job_busy");
});

test("an unknown saved test and an empty library are explained", async () => {
  const missing = renderApp("/new?scenario=gone", {});
  expect(await screen.findByText("Test not found")).toBeInTheDocument();
  missing.unmount();
  renderApp("/new", { "/api/library": [] });
  expect(await screen.findByText("No saved tests yet")).toBeInTheDocument();
});

test("AI draft: asks for a key only when none is configured, polls the job, shows files, saves", async () => {
  const user = userEvent.setup();
  let polls = 0;
  const { fetchMock } = renderApp("/new", {
    "/api/meta": { ...fixtures.meta, console: { ...fixtures.meta.console, ai: { available: false, model: "" } } },
    "POST /api/draft": [202, { id: fixtures.jobDraftRunning.id }],
    [`/api/jobs/${fixtures.jobDraftRunning.id}`]: () => (++polls < 3 ? fixtures.jobDraftRunning : fixtures.jobDraftFinished),
    "POST /api/library": [201, { name: "drafted-api", digest: "abc", has_command: false }],
    "/api/library/drafted-api": fixtures.libraryDetail,
  });

  await user.click(await screen.findByRole("tab", { name: "Describe it (AI)" }));
  const submit = screen.getByRole("button", { name: "Draft with AI" });
  await user.type(screen.getByLabelText("Test name"), "drafted-api");
  await user.click(screen.getByLabelText("What the app is"));
  await user.paste("A multi-tenant invoicing API.");
  await user.click(screen.getByLabelText("Rules that must never break"));
  await user.paste("A tenant never reads another tenant's invoices.");
  const key = await screen.findByLabelText("Anthropic API key");
  expect(key).toHaveAttribute("type", "password");
  expect(submit).toBeDisabled();
  await user.type(key, "sk-ant-test");
  await user.click(submit);

  expect(await screen.findByRole("log", { name: "Draft progress" })).toHaveTextContent("attempt 1: asking the model for a scenario");
  expect(screen.getByText("What Claude is doing")).toBeInTheDocument();
  // The poll moves on to the finished job: files, validation and the review warning.
  expect(await screen.findByText("Review before saving: scenarios are code that runs on your machine")).toBeInTheDocument();
  expect(screen.getByText("Validates")).toBeInTheDocument();
  expect(screen.getByText(/1 attempt · 4,210 tokens in/)).toBeInTheDocument();
  expect(screen.getByLabelText("Contents of manifest.json")).toBeInTheDocument();
  expect(screen.getByLabelText("Contents of journeys.js")).toBeInTheDocument();
  expect(polls).toBeGreaterThanOrEqual(3);

  expect(bodyOf(fetchMock, "POST", "/api/draft")).toEqual({
    name: "drafted-api",
    app_description: "A multi-tenant invoicing API.",
    rules: "A tenant never reads another tenant's invoices.",
    api_key: "sk-ant-test",
  });
  await user.click(screen.getByRole("button", { name: "Save to library" }));
  expect(await screen.findByRole("form", { name: "Aim the test" })).toBeInTheDocument();
  expect(bodyOf(fetchMock, "POST", "/api/library")).toEqual({ name: "drafted-api", files: fixtures.jobDraftFinished.result!.files });
});

test("AI draft: no key field when the server has one; a failed draft shows its error and last files", async () => {
  const user = userEvent.setup();
  renderApp("/new", {
    "POST /api/draft": [202, { id: fixtures.jobDraftFailed.id }],
    [`/api/jobs/${fixtures.jobDraftFailed.id}`]: fixtures.jobDraftFailed,
  });
  await user.type(await screen.findByLabelText("Test name"), "drafted-api");
  expect(screen.queryByLabelText("Anthropic API key")).not.toBeInTheDocument();
  await user.click(screen.getByLabelText("What the app is"));
  await user.paste("An API.");
  await user.click(screen.getByLabelText("Rules that must never break"));
  await user.paste("No leaks.");
  await user.click(screen.getByRole("button", { name: "Draft with AI" }));

  expect(await screen.findByText("Does not validate")).toBeInTheDocument();
  expect(screen.getByText("manifest.json: schedule is required")).toBeInTheDocument();
  expect(screen.getByLabelText("Contents of manifest.json")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Save to library" })).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Edit and try again" }));
  expect(screen.getByLabelText("What the app is")).toHaveValue("An API.");
});

test("AI draft: a server refusal is shown", async () => {
  const user = userEvent.setup();
  renderApp("/new", { "POST /api/draft": [422, { error: "rules are blank" }] });
  await user.type(await screen.findByLabelText("Test name"), "x");
  await user.click(screen.getByLabelText("What the app is"));
  await user.paste("An API.");
  await user.click(screen.getByLabelText("Rules that must never break"));
  await user.paste("a");
  await user.click(screen.getByRole("button", { name: "Draft with AI" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("rules are blank");
});

test("paste files: needs_trust shows the command and gates saving on the trust checkbox", async () => {
  const user = userEvent.setup();
  let attempts = 0;
  const { fetchMock } = renderApp("/new", {
    "POST /api/library": () =>
      ++attempts === 1
        ? [400, { error: "bundle has a setup command", needs_trust: true, command: ["docker", "compose", "up", "-d"] }]
        : [201, { name: "pasted", digest: "abc", has_command: true }],
    "/api/library/pasted": fixtures.libraryDetail,
  });
  await user.click(await screen.findByRole("tab", { name: "Paste files" }));
  expect(screen.getByLabelText("File 1 path")).toHaveValue("manifest.json");
  expect(screen.getByLabelText("File 2 path")).toHaveValue("journeys.js");
  await user.type(screen.getByLabelText("Test name"), "pasted");
  await user.click(screen.getByLabelText("File 1 content"));
  await user.paste('{"name":"pasted"}');
  await user.click(screen.getByLabelText("File 2 content"));
  await user.paste("export default {};");
  expect(screen.queryByRole("checkbox", { name: "Trust setup commands" })).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Save to library" }));

  expect(await screen.findByText("docker compose up -d")).toBeInTheDocument();
  const trust = screen.getByRole("checkbox", { name: "Trust setup commands" });
  expect(screen.getByRole("button", { name: "Save to library" })).toBeDisabled();
  await user.click(trust);
  await user.click(screen.getByRole("button", { name: "Save to library" }));

  expect(await screen.findByRole("form", { name: "Aim the test" })).toBeInTheDocument();
  expect(bodyOf(fetchMock, "POST", "/api/library")).toEqual({
    name: "pasted",
    files: { "manifest.json": '{"name":"pasted"}', "journeys.js": "export default {};" },
    trust_command: true,
  });
});

test("paste files: an existing name offers overwrite; an invalid bundle shows the validation text", async () => {
  const user = userEvent.setup();
  let attempts = 0;
  const { fetchMock } = renderApp("/new", {
    "POST /api/library": () => (++attempts === 1 ? [409, { error: "pasted already exists" }] : [422, { error: "manifest.json: bad\nline two" }]),
  });
  await user.click(await screen.findByRole("tab", { name: "Paste files" }));
  await user.type(screen.getByLabelText("Test name"), "pasted");
  await user.click(screen.getByLabelText("File 1 content"));
  await user.paste("{}");
  await user.click(screen.getByLabelText("File 2 content"));
  await user.paste("x");
  await user.click(screen.getByRole("button", { name: "Save to library" }));
  await user.click(await screen.findByRole("button", { name: "Overwrite existing test" }));
  const alert = await screen.findByText(/manifest.json: bad/);
  expect(alert).toHaveTextContent("line two");
  await waitFor(() => expect(attempts).toBe(2));
  expect(JSON.parse(fetchMock.mock.calls.at(-1)![1]!.body as string)).toMatchObject({ overwrite: true });
});

test("tenant isolation kit: load example, then save through library/kit", async () => {
  const user = userEvent.setup();
  const { fetchMock } = renderApp("/new", {
    "POST /api/library/kit": [201, { name: NAME, digest: "abc", has_command: false }],
    ...DETAIL,
  });
  await user.click(await screen.findByRole("tab", { name: "Tenant isolation kit" }));
  const save = screen.getByRole("button", { name: "Save to library" });
  expect(save).toBeDisabled();
  await user.click(screen.getByRole("button", { name: "Load example" }));
  expect(screen.getByLabelText("Test name")).toHaveValue(NAME);
  expect(JSON.parse((screen.getByLabelText("Kit config (JSON)") as HTMLTextAreaElement).value)).toMatchObject({ identities: expect.any(Array) });
  await user.click(save);
  expect(await screen.findByRole("form", { name: "Aim the test" })).toBeInTheDocument();
  expect(bodyOf(fetchMock, "POST", "/api/library/kit")).toMatchObject({ name: NAME, config: { name: NAME } });
});

test("kit: broken JSON is flagged before anything is sent", async () => {
  const user = userEvent.setup();
  renderApp("/new");
  await user.click(await screen.findByRole("tab", { name: "Tenant isolation kit" }));
  await user.type(screen.getByLabelText("Test name"), "kit");
  await user.click(screen.getByLabelText("Kit config (JSON)"));
  await user.paste("{nope");
  expect(screen.getByText(/Not valid JSON/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Save to library" })).toBeDisabled();
});
