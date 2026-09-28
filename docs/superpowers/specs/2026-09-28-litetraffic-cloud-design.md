# LiteTraffic Cloud: design spec

Status: draft, 2026-09-28. Implementation is gated on Phase 0 evidence (see [Milestones](#milestones)).

LiteTraffic Cloud is a hosted service that stores, compares and explains **finished** LiteTraffic runs uploaded from CI. It is the "separate authenticated job service" anticipated in [TECH_SPEC.md](../../TECH_SPEC.md) §13, minus the job part: the cloud never runs jobs.

## Goals

- Run history across CI builds for each project and scenario, with p95 trends and flakiness (the same scenario and commit giving different verdicts).
- GitHub PR status checks and one sticky PR comment per pull request.
- Team links: members of an org can open any run of that org without a local checkout.
- AI explanations generated with the service's own provider key. Default model: Claude Haiku (`claude-haiku-4-5-20251001`). Optional: OpenAI `gpt-4o-mini`.
- Hosting on Dokploy (Docker), with Postgres and S3-compatible object storage (R2 in production, MinIO locally and in tests).

## Non-goals

- **The cloud never sends traffic to customer apps.** It has no k6, no targets, no fixtures, no sandboxes. It only receives uploaded run artifacts.
- The CLI stays MIT, free and local-first. `verify`, `diff`, `dashboard` and every other command keep working with no account.
- No change to the local dashboard's security model: loopback bind, Host check, action header. The hosted server is a separate entrypoint.
- Tenancy beyond org, projects and members: no SSO, SCIM, custom roles or per-project ACLs.
- Metered billing, invoices or a plan UI. Billing is a hook only.
- Uploading `series_*.json` and `activity_*` records as first-class objects. Individual runs from `--repeat` and `up` can still be uploaded one by one.

## Architecture

```
 customer CI (their runner, their network)              LiteTraffic Cloud (Dokploy)
 ┌──────────────────────────────────────┐              ┌─────────────────────────────────────────┐
 │ litetraffic verify ... --target $URL │              │ Traefik (TLS)                           │
 │   -> .litetraffic/runs/run_X/        │              │   app.<domain>  -> cloud-api (+ SPA)    │
 │ litetraffic upload run_X             │  HTTPS POST  │   <usercontent-domain> -> artifact-srv  │
 │   builds allowlisted, scrubbed bundle│ ───────────> │                                         │
 │   Bearer ltu_... (upload token)      │  /v1/uploads │ cloud-api (Python, stdlib HTTP server)  │
 └──────────────────────────────────────┘              │   ├─ Postgres (orgs, runs, tokens, ...) │
                                                        │   ├─ S3/R2 (bundle files per run)       │
 browser (member) ── session cookie ──> app.<domain>    │   ├─ Anthropic / OpenAI (explain)       │
 browser (report) ── signed URL ──> usercontent domain  │   └─ GitHub API (checks, PR comments)   │
 GitHub ── webhooks (HMAC) ──> app.<domain>/github/...  └─────────────────────────────────────────┘
```

Code layout:

- A new `src/litetraffic_cloud/` package, installed with a `cloud` extra (`psycopg`, `boto3`). It reuses `summarize`, `build_prompt`, `compare_runs`, the `_detail` assembly, the NaN-safe `_json`, the CSP constants and the React SPA.
- `litetraffic upload` lives in the core package and uses `urllib` only, so it adds no new core dependency.
- One Docker image with two processes (`cloud-api` and `artifact-srv`), each behind its own Traefik router.

## `litetraffic upload` contract

```
litetraffic upload RUN [--runs-dir .litetraffic/runs] [--server https://app.<domain>]
                       [--project SLUG] [--target-label NAME] [--no-values] [--dry-run] [--json]
```

- `RUN` is a run directory or a run ID, resolved exactly as `diff` resolves it (runs.py:111).
- The token comes from `LITETRAFFIC_TOKEN` and is never accepted as a flag, so it does not end up in shell history or CI logs. `--project` is optional when the token is scoped to one project.
- Git and CI metadata come from the environment (`GITHUB_SHA`, `GITHUB_REF`, `GITHUB_REPOSITORY`, PR number from the event payload, run URL) or from `git rev-parse` as a fallback. The upload never runs any other command.
- The run must have finished: its lifecycle must be terminal and `artifacts.json` must be present.
- Exit codes: 0 when stored or already present, 1 on rejection (4xx), 3 on network or server failure. **The upload never changes the run's verdict exit code.** The CI step's result stays the same with or without the service.

### What files are sent (allowlist)

| File | Sent | Treatment |
|---|---|---|
| `result.json` | yes | scrubbed (below) |
| `run.json` | yes | `target` replaced with the target label |
| `observation.json` | yes, if present | scrubbed. `actual` values are dropped with `--no-values` |
| `fixture.json` | yes, if present | only `status`, `reason`, request counts and `duration_seconds`. `argv`, `stderr` and fixture IDs are dropped |
| `scenario.lock.json` | digest only | sends `scenario_sha256`, engine and file hashes. The manifest body is never sent |
| `artifacts.json` | yes | used by the server to check integrity |
| `report.html` | **no** | the server re-renders it from the scrubbed `result.json` with `report.py` |
| `metrics.jsonl`, `console.log`, `engine.*.log`, `events/*.jsonl` | **no** | contain full request URLs and undeclared output. Failing samples already appear in `result.json` |

### Redaction policy

The bundle builder is a pure function: `(run_dir, options) -> bundle dict`. `--dry-run` prints exactly what would be sent. It applies these steps in order:

1. **Declared secrets.** It re-applies the existing redaction to every string, covering raw, JSON-escaped and Go-escaped forms (auth.py:94-122). The CLI redacts what it can see in the environment. Values shorter than 8 characters are still exempt, and the dry-run output says so.
2. **Target.** The target URL is replaced by `--target-label`. Without a label it becomes `target-<first 12 hex of sha256(origin)>`, so trends stay stable without revealing the host. The same rule applies to any occurrence of the target origin inside strings, and to `allowed_origins` hosts.
3. **Pattern scrub.** It removes URL query strings, strings that look like JWTs (`eyJ…​.…​.…`), `Bearer …` values, `Authorization`/`Cookie` header values, and absolute filesystem paths (`/home/…`, `/Users/…`, `C:\…`) anywhere in a string.
4. **Values.** `--no-values` drops `expected`, `actual`, `detail` and `logical_key` from failing samples and observations, keeping only status, matcher and counts. AI explanations get weaker without these values, and the UI says so.

The server treats every upload as untrusted anyway. It re-validates the schema, re-applies step 3, and never trusts `artifacts.json` hashes as a signature. They are only an integrity check against the bytes it received.

### Limits and idempotency

- The body is JSON (`{manifest, files: {name: text}}`), gzip content-encoded. Limits: 1 MiB per file and 4 MiB per bundle **after decompression**. The server stops reading at the limit, which protects against decompression bombs. It returns 413 with the limit in `{error}`.
- The idempotency key is `(project_id, run_id, scenario_sha256)`. The server stores `content_sha256` of the canonicalised bundle.
  - Same key and same content: `200 {run: <existing>}`, with no new write. CI retries are safe.
  - Same key and different content: `409`. A run ID is never overwritten.
- Rate limits: 60 uploads/minute per token, with a monthly per-org run cap set by entitlements.

## Hosted API

All responses use the existing `{error}` envelope. The IDs in paths are public UUIDs or slugs. **Nothing in a response exposes a server path.** The absolute `path` field and `meta.runs_dir` from the local API are removed.

Upload (bearer upload token):

- `POST /v1/uploads` returns `201 {run_url, run_id}` or 200 (idempotent), 400, 401, 403, 409 or 413.

Session (browser) routes mirror the local dashboard so the SPA's client only gains a base prefix `/api/o/<org>/p/<project>`:

- `GET /api/me`: user, orgs and projects.
- `GET …/meta`: `{version, explain_provider, ai_budget_remaining}`. `explain_provider` replaces `explain_cli` and fixes the local bug where a key-only setup reads as "no CLI".
- `GET …/runs?scenario=&verdict=&branch=&sha=&cursor=`: paginated, backed by an indexed query.
- `GET …/runs/<id>` returns the detail shape (run, result, observation, fixture, `explanation`, `ai_explanation`, artifacts), plus git metadata.
- `GET …/scenarios`, `GET …/scenarios/<name>/trend`, `GET …/scenarios/<name>/flaky`.
- `GET …/diff?baseline=&candidate=` uses `compare_runs`.
- `GET …/runs/<id>/artifacts/<name>` returns `302` to a short-lived signed URL on the usercontent domain.
- `POST …/runs/<id>/explain` requires `X-LiteTraffic-Action: explain`.
- Org admin: `GET/POST /api/o/<org>/tokens`, `DELETE /api/o/<org>/tokens/<id>`, `GET/POST/DELETE /api/o/<org>/members`, `POST /api/o/<org>/projects`.

Auth and integrations:

- `GET /auth/github/login`, `GET /auth/github/callback`, `POST /auth/logout`.
- `POST /github/webhook` (HMAC).
- `POST /billing/webhook` (provider signature).
- `GET /healthz`.

## Data model (Postgres)

```
orgs(id uuid pk, slug unique, name, plan text default 'free', billing_customer_id, ai_enabled bool, created_at)
users(id uuid pk, github_id bigint unique, login, email, created_at)
memberships(org_id fk, user_id fk, role text check in ('owner','member'), pk(org_id,user_id))
projects(id uuid pk, org_id fk, slug, github_repo text null, unique(org_id, slug))
upload_tokens(id uuid pk, org_id fk, project_id fk null, name, prefix char(8), token_sha256 bytea unique,
              scopes text[], created_by fk, created_at, last_used_at, expires_at null, revoked_at null)
sessions(id_sha256 bytea pk, user_id fk, created_at, expires_at, last_seen_at)
runs(id uuid pk, org_id fk, project_id fk, run_id text, scenario, scenario_sha256, verdict, lifecycle,
     completeness, seed, started_at, finished_at, p95_ms, failed_rate, reqs_per_s, target_label,
     git_sha, git_ref, pr_number, ci_url, content_sha256, bytes, uploaded_at, token_id fk,
     unique(project_id, run_id, scenario_sha256))
run_files(run_pk fk, name, bytes, sha256, pk(run_pk, name))
explanations(run_pk fk pk, provider, model, input_tokens, output_tokens, cost_micros, text, created_at)
ai_usage(org_id fk, month date, cost_micros bigint, calls int, pk(org_id, month))
github_installations(installation_id bigint pk, org_id fk, account_login, created_at)
pr_comments(project_id fk, pr_number, comment_id bigint, pk(project_id, pr_number))
```

- Indexes: `runs(project_id, scenario, finished_at desc)` and `runs(project_id, git_sha)`.
- Object keys are always `orgs/<org_id>/runs/<run_pk>/<name>`. They are built by the server from database IDs and never from client input.
- Flakiness is a query rather than a table. For each `(scenario, git_sha)` with more than one run, it counts the distinct verdicts. A scenario is flaky if any commit in the window has both pass and fail. The rate is the share of such commits.
- Retention: a daily job deletes runs past the plan's retention period, removing the database row and the objects together.

## Auth model

| | Upload token | User session |
|---|---|---|
| Who | CI | a person in a browser |
| Format | `ltu_` + 32 random bytes, base64url | random 32 bytes in the `__Host-lt_session` cookie (Secure, HttpOnly, SameSite=Lax, Path=/) |
| Storage | `sha256(token)` only; the plaintext is shown once; `prefix` is kept for display | `sha256(id)` only; 14-day expiry, sliding |
| Scope | `runs:upload`, for one project or the whole org. No read access | member or owner of the orgs in `memberships` |
| Revocation | `revoked_at`, effective on the next request | logout, expiry, or removal from the org (checked on every request) |

- **Hashing.** The tokens are high-entropy, so a single sha256 is enough; bcrypt or argon2 is not needed. Lookup is by hash, so no plaintext comparison happens. Tokens and cookies never appear in logs, because the logger redacts `Authorization` and `Cookie`.
- **Login.** GitHub OAuth in Phase 1, because every target user has a GitHub account and Phase 3 needs GitHub anyway. The first login creates a personal org. Owners invite members by GitHub login.
- **CSRF.** Session POST, DELETE and PATCH requests require the action header plus an `Origin` that exactly matches the configured app origin. Together with SameSite=Lax and no CORS, this carries over the local model's header trick safely.
- **Response headers.** The existing CSP (`default-src 'self'; frame-ancestors 'none'; base-uri 'none'`), `nosniff` and `no-referrer`, plus HSTS.

## Tenant isolation

Guarantees:

1. Every query that reads or writes tenant data goes through one repository module. It requires an `org_id`, which comes from the authenticated principal and never from the request body.
2. Every lookup by path ID includes `org_id` (and `project_id`) in the `WHERE` clause. When the resource exists in another org, the response is **404, not 403**, so IDs cannot be probed.
3. Object keys are derived from database rows, and signed artifact URLs embed `(org_id, run_pk, name, expiry)` under HMAC. A URL from org A cannot name an object of org B.
4. Upload tokens cannot read anything. A token for project P cannot write to project Q.
5. Caches are keyed by `run_pk`: explanations, and later trends.

How it is tested:

- **Route matrix test.** A fixture creates orgs A and B, each with a project, a run, a token and a member. The test walks every registered route with every principal (A's member, B's member, A's token, anonymous) against every resource ID. Routes are enumerated from the router, so a new route with no matrix entry fails the test.
- **SQL guard test.** A test fails if any SQL string in the repository module lacks `org_id`.
- **Dogfood.** A LiteTraffic scenario built with `litetraffic init tenant-isolation` runs against a staging deployment on every release: own access, cross-tenant read and write rejection, and victim read-back. The product checks its own cloud.

Postgres RLS as a second layer is an open question. It is not required for Phase 1.

## Serving artifacts

`report.html` and any uploaded file are untrusted tenant content, even after re-rendering, because they embed strings from the customer's app.

- They are served only from a **separate registrable domain** (for example `litetraffic-usercontent.com`), which receives no cookies. A same-site subdomain is not acceptable.
- Access uses signed URLs with a 5-minute expiry, minted by `cloud-api` after it checks the session. `artifact-srv` checks the HMAC and streams the object from S3.
- Every response carries `Content-Security-Policy: sandbox; default-src 'none'; style-src 'unsafe-inline'`, plus `nosniff`, `no-referrer` and `Cross-Origin-Resource-Policy: same-origin`. The first two CSP directives are the existing `ARTIFACT_CSP`; the report's inline styles are the only addition.
- Only `.html` gets `text/html`. JSON is served as `application/json`, and everything else as `text/plain` with `Content-Disposition: attachment`.
- The SPA opens reports in a new tab or a sandboxed iframe (`sandbox=""`). It never renders them inside the app origin.

## AI explanations (hosted)

- **Providers.** Anthropic first (`claude-haiku-4-5-20251001`), then OpenAI `gpt-4o-mini` if configured. Keys are server environment variables. The CLI-subprocess path in `explain()` is disabled in hosted mode.
- **Prompt.** `build_prompt` is used unchanged: it caps evidence at 40k characters, drops fixture commands, and wraps the evidence in `<run>` marked as untrusted data. The model gets no tools. Its output is stored as plain text and rendered as text; it is never HTML and has no clickable links. We assume a hostile run can make the text say anything, so the output is never treated as an action.
- **Cost limits.**
  - A per-org monthly `ai_budget_micros` from entitlements, checked before each call and incremented from the provider's reported usage.
  - A limit of 5 explanations/minute per org.
  - One explanation per run. Regenerating an explanation counts against the budget.
  - Past the budget, the response is `402 {error: "AI budget reached for this month"}`.
- **Kill switch.** Orgs can set `ai_enabled=false`. A global environment kill switch also exists.
- **Errors.** Provider error text is logged server-side and replaced by a generic message in responses. This also fixes the local `except Exception` leak.
- **Concurrency.** The global `_EXPLAIN_LOCK` becomes a per-run in-flight guard using a Postgres advisory lock on `run_pk`.

## GitHub integration

**Decision: a GitHub App for checks and comments, plus a thin Action for uploading.**

- **Action (`litetraffic/upload-action`, composite).** It runs `litetraffic upload` with `LITETRAFFIC_TOKEN` from repo secrets. It posts nothing to GitHub itself, so it works identically on the free CLI flow with the service turned off.
- **GitHub App.** Permissions: `checks: write`, `pull_requests: write`, `metadata: read`. Events: `installation` and `installation_repositories` only.
- **Upload flow.** When a run arrives with `github_repository`, `git_sha` and `pr_number`, and that repo is linked to an installation owned by the same org, the server:
  1. Creates or updates a check run `LiteTraffic / <scenario>`. Verdict mapping: pass → success, fail → failure, inconclusive → neutral, error → failure. Each org can change the mapping.
  2. Upserts one sticky PR comment, tracked in `pr_comments`. It shows a table of scenarios × verdict × p95, the delta against the latest run for the same scenario on the base branch (via `compare_runs`), and team links.
- **Why not an Action posting with `GITHUB_TOKEN`?** Fork PRs get read-only tokens. There would be no durable identity for updating comments, and the result would duplicate the data the service already holds. It stays possible for free users as a documented recipe, not a product.
- **Security.**
  - Webhooks are verified with HMAC-SHA256 against the app secret.
  - The server posts only to repos in an installation linked to the uploading org. Claiming another org's repo in the upload metadata has no effect.
  - Installation tokens are minted per call and never stored.

## Billing hook

- `orgs.plan` plus one function, `entitlements(org) -> {max_members, max_projects, runs_per_month, retention_days, ai_budget_micros, github_app: bool}`. Every limit in this spec reads from it.
- `POST /billing/webhook` verifies the provider's signature and maps subscription events to `orgs.plan` and `billing_customer_id`. The provider (Stripe or Lemon Squeezy) is chosen in Phase 4. No other code depends on the choice.
- Until Phase 4, plans are set by hand in SQL for design partners.

## Landing page changes

- The headline stays "Users as an API." There is no new tagline.
- Add `Pricing.astro` between `#start` and the closing CTA, which means splitting the CTA out of `Start.astro`. Add `#pricing` to the nav and the footer's Product column.
- Two cards:
  - **Open source CLI**: free, MIT, available now, runs in your CI.
  - **LiteTraffic Cloud**: early access, pricing to be announced. It lists history, PR checks, team links and AI explanations. The CTA is "Join early access" (a form or mailto that collects the team name and CI provider).
- A line under the cards: "Cloud stores finished runs you upload. It never sends traffic to your app."
- Copy fixes:
  - "no hosted account" becomes "The CLI needs no hosted account".
  - Add "LiteTraffic Cloud (early access)" to the "Not shipped yet" chips until Phase 1 ships.
  - Update the roadmap under Next/Later.
  - Amend TECH_SPEC §1 and §15 to point to this spec.

## Milestones

Each phase ships on its own and stops if its evidence is weak.

**Phase 0: validate (no cloud code).**
- Work:
  - Publish a CI recipe (a GitHub Actions example that uses `verify --json` and the exit codes).
  - Recruit 5-10 teams to run the free CLI in CI.
  - Interview them about history, PR checks, sharing and willingness to pay.
- Accept when:
  - At least 5 teams run LiteTraffic in CI weekly for 3 weeks.
  - At least 3 teams ask for one of the paid features unprompted.
  - Pricing hypotheses are written down.

**Phase 1: minimal hosted.**
- Work:
  - `litetraffic upload` with `--dry-run`. This is useful on its own as a sanitised export.
  - `cloud-api`, Postgres, R2, GitHub login, orgs, projects and tokens.
  - The SPA with auth, run list and detail, and artifacts on the usercontent domain.
  - Deployed on Dokploy.
  - Landing page early-access card.
- Accept when:
  - A design partner's CI uploads and a teammate opens the run through a link.
  - The canary redaction test and the isolation matrix pass.
  - A re-upload is a no-op, and a 5 MiB bundle gets a 413.

**Phase 2: history and AI.**
- Work: trends, flaky view, diff across builds, AI explanations with budgets.
- Accept when:
  - The trend and flaky views match fixtures.
  - Budget exhaustion returns 402.
  - Provider errors are never echoed.

**Phase 3: GitHub.**
- Work: the App, check runs, the sticky comment, and the upload Action.
- Accept when:
  - A PR in a test repo shows a check per scenario and exactly one comment updated across pushes.
  - A spoofed repo in the metadata posts nothing.

**Phase 4: billing.**
- Work: the provider webhook drives `orgs.plan`, and limits are enforced from entitlements.
- Accept when a test-mode subscription upgrade raises the limits without a deploy.

## Testing strategy

- **Upload builder (unit, core CI).** Golden run directories carry planted canaries:
  - a token in a URL query
  - a JWT in `console.log` and stderr
  - an absolute home path in `argv`
  - the target host inside assertion details
  - a declared secret in escaped forms

  The test asserts that no canary string appears anywhere in the serialized bundle, and that the allowlist is exact (a new run file is excluded by default).
- **Server (integration).** Run against ephemeral Postgres and MinIO in Docker Compose:
  - upload validation and limits, including a gzip bomb
  - idempotency and 409
  - token hashing and revocation
  - session and CSRF rules
  - the isolation route matrix and the SQL guard
- **Artifact origin.** Header assertions on every response; expired and tampered signatures are rejected; cookies are never set on that domain.
- **AI.** A fake provider that reports token usage, for budget accounting, 402 and error redaction. `build_prompt` stays covered by the existing tests.
- **GitHub.** Recorded webhook payloads with valid and invalid signatures. The GitHub API is stubbed. The verdict-mapping table is tested.
- **End to end.** A CI job brings up the stack, runs `verify` on a bundled conformance example (fake k6 output is enough), runs `upload`, then reads the run back through the session API.
- **Release dogfood.** The tenant-isolation scenario runs against staging.

## Open questions

1. **Pricing.** Tiers, numbers, and whether AI and GitHub are paid-only. These are set from Phase 0 interviews, not in this spec.
2. **Values by default.** Should failing-sample values be uploaded by default, or should `--no-values` be the default with AI explanations degraded?
3. **Login.** Is GitHub-only login enough, or do some teams need email magic links?
4. **Postgres RLS.** Is it worth adding as defense in depth in Phase 1, or later?
5. **Retention and residency.** What retention does each plan get, and does any design partner need EU-only storage?
6. **Series.** Should `--repeat` series and `up` activities be uploadable as first-class objects once teams use them in CI?
7. **Self-hosting.** Should the cloud package be MIT like the CLI, or source-available, and can teams self-host it?
8. **Undeclared secrets.** Should the upload refuse to send, or only warn, when a pattern scrub fires? Firing means an undeclared secret reached the run artifacts.
