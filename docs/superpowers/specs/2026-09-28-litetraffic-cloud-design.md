# LiteTraffic Cloud: design spec

Status: draft, 2026-09-28, revised after review. Implementation is gated on Phase 0 evidence (see [Milestones](#milestones)).

LiteTraffic Cloud is a hosted service that stores, compares and explains **finished** LiteTraffic runs uploaded from CI. It is the "separate authenticated job service" anticipated in [TECH_SPEC.md](../../TECH_SPEC.md) §13, minus the job part: the cloud never runs jobs.

## Goals

- Run history across CI builds for each project and scenario, with p95 trends and flakiness (the same scenario and commit giving different verdicts).
- GitHub PR status checks and one sticky PR comment per pull request.
- Team links: members of an org can open any run of that org without a local checkout.
- AI explanations generated with the service's own provider key. Default model: Claude Haiku (`claude-haiku-4-5-20251001`). Optional: OpenAI `gpt-4o-mini`.
- Hosting on Dokploy (Docker), with Postgres as the only store.

## Non-goals

- **The cloud never sends traffic to customer apps.** It has no k6, no targets, no fixtures, no sandboxes. It only receives uploaded run artifacts.
- The CLI stays MIT, free and local-first. `verify`, `diff`, `dashboard` and every other command keep working with no account.
- No change to the local dashboard's security model: loopback bind, Host check, action header. The hosted server is a separate entrypoint.
- Tenancy beyond org, projects and members: no SSO, SCIM, custom roles or per-project ACLs.
- Metered billing, invoices or a plan UI. Billing is a hook only.
- Uploading `series_*.json` and `activity_*` records as first-class objects. Individual runs from `--repeat` and `up` can still be uploaded one by one.
- In Phase 1: object storage, a hosted `report.html` and a usercontent domain (see [Serving run files](#serving-run-files)).

## Architecture

```
 customer CI (their runner, their network)              LiteTraffic Cloud (Dokploy)
 ┌──────────────────────────────────────┐              ┌─────────────────────────────────────────┐
 │ litetraffic verify ... --target $URL │              │ Traefik (TLS, body + per-IP limits)     │
 │   -> .litetraffic/runs/run_X/        │              │   app.<domain>  -> cloud-api (+ SPA)    │
 │ litetraffic upload run_X             │  HTTPS POST  │                                         │
 │   builds allowlisted, scrubbed bundle│ ───────────> │ cloud-api (Python, stdlib HTTP server)  │
 │   Bearer ltu_... (upload token)      │  /v1/uploads │   ├─ Postgres (orgs, runs, files, ...)  │
 └──────────────────────────────────────┘              │   ├─ Anthropic / OpenAI (explain)       │
 browser (member) ── session cookie ──> app.<domain>    │   └─ GitHub API (checks, PR comments)   │
 GitHub ── webhooks (HMAC) ──> app.<domain>/github/...  └─────────────────────────────────────────┘
```

Code layout:

- A new `src/litetraffic_cloud/` package, installed with a `cloud` extra (`psycopg`). It reuses `summarize`, `build_prompt`, `compare_runs`, the `_detail` assembly, the NaN-safe `_json`, the CSP constants and the React SPA.
- `litetraffic upload` lives in the core package and uses `urllib` only, so it adds no new core dependency.
- One Docker image, one process, one instance.

**Server hardening.** The stdlib server is kept, with these changes (the local dashboard has none of them):

- A bounded worker pool (`ThreadPoolExecutor`, e.g. 16 workers) instead of a thread per connection, and socket timeouts on headers and body (e.g. 10 s idle, 30 s total).
- For uploads, in this order: authenticate the bearer token, require `Content-Length` of at most 1 MiB compressed (chunked bodies get 411), read, then inflate with `zlib.decompressobj(...).decompress(data, max_length)`. Anonymous requests never reach gzip or JSON parsing.
- JSON nesting depth and string count are capped.
- Traefik adds a buffering middleware (`maxRequestBodyBytes`) and per-IP rate and in-flight limits, which cover unauthenticated traffic.
- `log_message` is overridden so headers are never logged.
- Rate limits live in an in-process dict. `ponytail: single instance; move to Postgres if there is more than one replica.`

## `litetraffic upload` contract

```
litetraffic upload RUN [--runs-dir .litetraffic/runs] [--server https://app.<domain>]
                       [--project SLUG] [--target-label NAME] [--no-values]
                       [--dry-run [--out PATH] [--print]] [--allow-scrubbed] [--strict] [--json]
```

- `RUN` is a run directory or a run ID, resolved exactly as `diff` resolves it (runs.py:111).
- The token comes from `LITETRAFFIC_TOKEN` and is never accepted as a flag, so it does not end up in shell history or CI logs.
- The project defaults to `GITHUB_REPOSITORY` (or `--project`). The server creates the project on first upload.
- Git and CI metadata come from the environment (`GITHUB_SHA`, `GITHUB_REF`, `GITHUB_BASE_REF`, `GITHUB_REPOSITORY`, PR number from the event payload, run URL) or from `git rev-parse` as a fallback. The upload never runs any other command.
- The run must have finished: its lifecycle must be terminal and `artifacts.json` must be present.
- `--dry-run` writes the bundle to `--out` (default `litetraffic-upload-dryrun.json`) and prints only file names, sizes and scrub-hit counts, because CI logs can be public. `--print` also prints the content.
- Exit codes: 0 when stored or already present; 1 on rejection (4xx) or when scrubbing refused the bundle; on network or server failure, a warning and exit 0 by default, or 3 with `--strict`. **The upload never changes the run's verdict exit code**, and an outage of the service never turns CI red unless the team opts in with `--strict`.

### What files are sent (allowlist)

| File | Sent | Treatment |
|---|---|---|
| `result.json` | yes | scrubbed (below) |
| `run.json` | yes | `target` replaced with the target label |
| `observation.json` | yes, if present | scrubbed. `actual` values are dropped with `--no-values` |
| `fixture.json` | yes, if present | only `status`, `reason`, request counts and `duration_seconds`. `argv`, `stderr` and fixture IDs are dropped |
| `scenario.lock.json` | digest only | sent as `scenario.lock.digest.json`: `scenario_sha256`, engine and file hashes. The manifest body is never sent |
| `artifacts.json` | **no** | it hashes the unscrubbed files and lists files that are never sent. The bundle's own `manifest.files` replaces it |
| `report.html` | **no** | not hosted in Phase 1 |
| `metrics.jsonl`, `console.log`, `engine.*.log`, `events/*.jsonl` | **no** | contain full request URLs and undeclared output. Failing samples already appear in `result.json` |

The server accepts only the file names `result.json`, `run.json`, `observation.json`, `fixture.json` and `scenario.lock.digest.json`. Any other name gets 400.

### Redaction policy

The primary protection is the runner's own scrub of declared secrets (runner.py:61-78, 277). The bundle builder is a pure function: `(run_dir, options) -> bundle dict`, and it adds these steps in order:

0. **Redact before truncating (Phase 1 gate).** `evidence.py`, `observation.py` and `fixture.py` redact declared secrets before applying their caps (500-char `detail`, 2 KB `actual`, 4 KB stderr), so a secret cut at a boundary cannot leak a prefix or suffix. The builder also redacts any run of 8 or more characters at the start or end of a string that is a prefix or suffix of a declared secret.
1. **Declared-secret assertion.** The upload step often runs without the secret env vars. When they are present, the builder fails the upload if any of them (raw, JSON-escaped or Go-escaped, auth.py:94-122) appears in the bundle. Values shorter than 8 characters are exempt, and the dry-run output says so.
2. **Hosts.** Every URL in every string is rewritten to `scheme://<host-label>/<path>`, with path segments over 16 characters of high-entropy content redacted, and the query string removed. Every host gets a label, not only the target's. Hosts are matched case-insensitively, as bare hosts and IPs, and in raw and JSON-escaped (`https:\/\/`) forms. The target's label is `--target-label`, or else the client sends `sha256(origin)` and the server stores only `HMAC(project_salt, that hash)`, truncated to 12 hex characters. The salt is random per project and never leaves the server, so the label is stable per project but not dictionary-reversible.
3. **Pattern scrub.** Removes strings that look like JWTs (`eyJ….….…`), `Bearer …` values, `Authorization`/`Cookie` header values, absolute filesystem paths (`/home/…`, `/Users/…`, `C:\…`), and a gitleaks-style set of known credential prefixes (`sk_live_`, `ghp_`, `github_pat_`, `AKIA…`, `xoxb-`/`xoxp-`, `postgres://user:pass@…`, and others).
4. **Values.** `--no-values` drops `expected`, `actual`, `detail` and `logical_key` from failing samples and observations, keeping only status, matcher and counts. AI explanations get weaker without these values, and the UI says so.

If step 3 fires, the upload is **refused by default**: exit 1, listing the fields that matched. `--allow-scrubbed` sends the scrubbed bundle instead.

The server treats every upload as untrusted. It re-validates the schema and the file allowlist, re-applies steps 2 and 3, and recomputes `manifest.files` hashes against the bytes it received.

### Validation of metadata

- `git_sha` must be 40 hex characters and `pr_number` a positive integer.
- `scenario` and `target_label` must match `^[A-Za-z0-9._-]{1,64}$`.
- `ci_url` is kept only if it matches `^https://github\.com/<github_repository>/actions/runs/\d+(/attempts/\d+)?$`. Otherwise it is stored as null.

### Limits and idempotency

- The body is JSON (`{manifest, files: {name: text}}`), gzip content-encoded. Limits: 1 MiB compressed, and 1 MiB per file and 4 MiB per bundle **after decompression**. The server returns 413 with the limit in `{error}`.
- `manifest.files` holds the sha256 of each scrubbed file as the client sent it.
- The idempotency key is `(project_id, run_id, scenario_sha256)`. `content_sha256` is computed over the files only (not git or CI metadata), before the server re-applies any scrub, so it does not depend on server scrub versions.
  - Same key and same content: `200 {run: <existing>}`, with no new write (only `ci_url` may be updated). CI retries and "Re-run job" attempts are safe.
  - Same key and different content: `409`. A run ID is never overwritten.
- Rate limits: 60 uploads/minute per token. Entitlements set a monthly per-org run cap and a `storage_bytes_per_month` cap. The byte cap is checked atomically at upload time against `storage_usage(org_id, month, bytes)`. Past either cap, the response is 402.

## Hosted API

All responses use the existing `{error}` envelope. The IDs in paths are public UUIDs or slugs. **Nothing in a response exposes a server path.** The absolute `path` field and `meta.runs_dir` from the local API are removed.

Upload (bearer upload token):

- `POST /v1/uploads` returns `201 {run_url, run_id}` or 200 (idempotent), 400, 401, 402, 403, 409, 411 or 413.

Session (browser) routes mirror the local dashboard so the SPA's client only gains a base prefix `/api/o/<org>/p/<project>`:

- `GET /api/me`: user, orgs and projects.
- `GET …/meta`: `{version, explain_provider, ai_budget_remaining}`. `explain_provider` replaces `explain_cli` and fixes the local bug where a key-only setup reads as "no CLI".
- `GET …/runs?scenario=&verdict=&branch=&sha=&cursor=`: paginated, backed by an indexed query.
- `GET …/runs/<id>` returns the detail shape (run, result, observation, fixture, `explanation`, `ai_explanation`, files), plus git metadata.
- `GET …/scenarios`, `GET …/scenarios/<name>/trend`, `GET …/scenarios/<name>/flaky`.
- `GET …/diff?baseline=&candidate=` uses `compare_runs`.
- `GET …/runs/<id>/files/<name>` downloads a stored file (see [Serving run files](#serving-run-files)).
- `POST …/runs/<id>/explain` requires `X-LiteTraffic-Action: explain`.
- Org admin, **owner only** (Phase 1): `GET/POST /api/o/<org>/tokens`, `DELETE /api/o/<org>/tokens/<id>`, `POST /api/o/<org>/members` (add by GitHub login), `DELETE /api/o/<org>/members/<user>`. A member gets 403 on these routes (403, not 404, because membership is already established). The last owner cannot be removed.

Auth and integrations:

- `GET /auth/github/login`, `GET /auth/github/callback`, `POST /auth/logout`.
- `GET /github/setup` (App setup URL callback, session required).
- `POST /github/webhook` (HMAC).
- `POST /billing/webhook` (provider signature).
- `GET /healthz`.

## Data model (Postgres)

```
orgs(id uuid pk, slug unique, name, plan text default 'free', billing_customer_id,
     ai_enabled bool default false, ai_values bool default false, created_at)
users(id uuid pk, github_id bigint unique, login, email, created_at)
memberships(org_id fk, user_id fk, role text check in ('owner','member'), pk(org_id,user_id))
projects(id uuid, org_id fk, slug, github_repo text null, target_salt bytea,
         pk(id), unique(org_id, id), unique(org_id, slug))
upload_tokens(id uuid pk, org_id fk, name, prefix char(8), token_sha256 bytea unique,
              created_by fk, created_at, last_used_at, expires_at, revoked_at null)
sessions(id_sha256 bytea pk, user_id fk, created_at, expires_at, last_seen_at)
runs(id uuid, org_id, project_id, run_id text, scenario, scenario_sha256, verdict, lifecycle,
     completeness, seed, started_at, finished_at, p95_ms, failed_rate, reqs_per_s, target_label,
     git_sha, git_ref, base_ref, pr_number, ci_url, content_sha256, bytes, uploaded_at, token_id fk,
     pk(id), unique(org_id, id), fk(org_id, project_id) -> projects(org_id, id),
     unique(project_id, run_id, scenario_sha256))
run_files(org_id, run_pk, name, bytes, sha256, body bytea,
          fk(org_id, run_pk) -> runs(org_id, id) on delete cascade, pk(run_pk, name))
explanations(org_id, run_pk pk, provider, model, input_tokens, output_tokens, cost_micros, text, created_at,
             fk(org_id, run_pk) -> runs(org_id, id) on delete cascade)
ai_usage(org_id fk, month date, cost_micros bigint, calls int, pk(org_id, month))
storage_usage(org_id fk, month date, bytes bigint, pk(org_id, month))
github_installations(installation_id bigint pk, org_id fk, account_login, linked_by fk, created_at)
pr_comments(org_id, project_id, pr_number, comment_id bigint,
            fk(org_id, project_id) -> projects(org_id, id), pk(project_id, pr_number))
```

- Every tenant table carries `org_id`, and child rows reference parents by `(org_id, id)`, so a row cannot point to another org's parent.
- Indexes: `runs(project_id, scenario, finished_at desc)` and `runs(project_id, git_sha)`.
- Run files live in Postgres (`run_files.body`). `ponytail: move blobs to R2 once the database exceeds a few GB.`
- Flakiness is a query rather than a table. For each `(scenario, git_sha)` with more than one run, it counts the distinct verdicts. A scenario is flaky if any commit in the window has both pass and fail. The rate is the share of such commits.
- Retention: a daily job runs `DELETE FROM runs WHERE …` past the plan's retention period, and files and explanations go with it by cascade.

## Auth model

| | Upload token | User session |
|---|---|---|
| Who | CI | a person in a browser |
| Format | `ltu_` + 32 random bytes, base64url, + a 6-character checksum suffix | random 32 bytes in the `__Host-lt_session` cookie (Secure, HttpOnly, SameSite=Lax, Path=/) |
| Storage | `sha256(token)` only; the plaintext is shown once; `prefix` is kept for display | `sha256(id)` only; 14-day expiry, sliding |
| Scope | upload only, for the whole org. No read access | member or owner of the orgs in `memberships` |
| Expiry | 1 year by default; "never" only by explicit choice. `last_used_at` is shown in the UI | 14 days, sliding |
| Revocation | `revoked_at`, effective on the next request | logout, expiry, or removal from the org (checked on every request) |

- **Hashing.** The tokens are high-entropy, so a single sha256 is enough; bcrypt or argon2 is not needed. Lookup is by hash, so no plaintext comparison happens. Tokens and cookies never appear in logs.
- **Login.** GitHub OAuth in Phase 1. Each login generates a `state` value and a PKCE verifier, stored in a short-lived `__Host-` cookie and compared in constant time on the callback, so login CSRF cannot sign a victim into the attacker's account. The first login creates a personal org.
- **Members.** An owner adds a person who has already logged in once, by looking up their GitHub login in `users` and storing their numeric `github_id`. There are no invites. `ponytail: add an invites table keyed by github_id if pre-login invites are needed.`
- **CSRF.** Session POST, DELETE and PATCH requests, including `POST /auth/logout`, require the action header plus an `Origin` that exactly matches the configured app origin. Together with SameSite=Lax and no CORS, this carries over the local model's header trick safely.
- **Response headers.** The existing CSP (`default-src 'self'; frame-ancestors 'none'; base-uri 'none'`), `nosniff` and `no-referrer`, plus HSTS.

## Tenant isolation

Guarantees:

1. Every query that reads or writes tenant data goes through one repository module. It requires an `org_id`, which comes from the authenticated principal and never from the request body.
2. Every lookup by path ID includes `org_id` (and `project_id`) in the `WHERE` clause. When the resource exists in another org, the response is **404, not 403**, so IDs cannot be probed.
3. Composite foreign keys on `(org_id, id)` make a cross-org reference impossible in the schema itself.
4. Upload tokens cannot read anything, and write only to their own org.
5. Caches are keyed by `run_pk`: explanations, and later trends.

How it is tested:

- **Route matrix test.** A fixture creates orgs A and B, each with a project, a run, a token, an owner and a member. The test walks every registered route with every principal (A's owner, A's member, B's member, A's token, anonymous) against every resource ID, and expects 403 for a member on owner routes. Routes are enumerated from the router, so a new route with no matrix entry fails the test.
- **SQL guard test.** A test fails if any SQL string in the repository module lacks `org_id`. Principal-resolution queries (sessions by hash, tokens by hash, users by `github_id`, installations by ID) live in a separate small `auth` module, so the guard needs no exemptions.
- **Dogfood (after Phase 1, not a release gate).** A LiteTraffic scenario built with `litetraffic init tenant-isolation` runs against staging: own access, cross-tenant read and write rejection, and victim read-back. It depends on tenant-isolation kit backlog item G01 (cookie jars), and on a staging-only session-minting endpoint that is enabled by an env variable and absent in production.

Postgres RLS as a second layer is an open question. It is not required for Phase 1.

## Serving run files

Stored files are untrusted tenant content, because they embed strings from the customer's app.

- **Phase 1.** `cloud-api` serves only the stored JSON files, as `application/json` with `Content-Disposition: attachment`, `nosniff`, `no-referrer` and the existing `ARTIFACT_CSP`. The name is looked up in `run_files` for that org and run. The SPA renders run detail from `result.json` through the `_detail` shape, so no HTML report is hosted.
- **Later, if a customer asks for a shareable HTML report.** Re-render `report.html` from the scrubbed `result.json` and serve it only from a separate registrable domain with 5-minute HMAC-signed URLs. Reports open in a new tab only. That domain gets its own `HOSTED_ARTIFACT_CSP` constant with `frame-ancestors 'none'`, rather than reusing the local one.

## AI explanations (hosted)

- **Providers.** Anthropic first (`claude-haiku-4-5-20251001`), then OpenAI `gpt-4o-mini` if configured. Keys are server environment variables. The CLI-subprocess path in `explain()` is disabled in hosted mode.
- **Consent.** `ai_enabled` defaults to false. An owner turns it on through a consent screen that names the provider, and the providers are listed as subprocessors. The model gets the `--no-values` view of the run unless the org also opts in to sending values (`ai_values`).
- **Prompt.** `build_prompt` caps evidence at 40k characters, drops fixture commands, and wraps the evidence in `<run>` marked as untrusted data. The serialized evidence escapes `<` as `\u003c`, so a run string containing `</run>` cannot close the block early. The model gets no tools. Its output is stored as plain text and rendered as text; it is never HTML and has no clickable links. We assume a hostile run can make the text say anything, so the output is never treated as an action.
- **Cost limits.**
  - Every provider call sets `max_tokens` (1024).
  - A per-org monthly `ai_budget_micros` from entitlements. Before each call the server reserves the worst case (the estimated input from the capped prompt plus `max_tokens`) with `UPDATE ai_usage SET cost_micros = cost_micros + :reserve WHERE … AND cost_micros + :reserve <= :budget`, then reconciles to the reported usage. Concurrent explains cannot overspend.
  - A limit of 5 explanations/minute per org.
  - One explanation per run. Regenerating an explanation counts against the budget.
  - Past the budget, the response is `402 {error: "AI budget reached for this month"}`.
- **Kill switch.** Orgs can set `ai_enabled=false`. A global environment kill switch also exists.
- **Errors.** Provider error text is logged server-side and replaced by a generic message in responses. This also fixes the local `except Exception` leak.
- **Concurrency.** The global `_EXPLAIN_LOCK` becomes a per-run in-flight guard using a Postgres advisory lock on `run_pk`.

## GitHub integration

**Decision: a GitHub App for checks and comments, plus a thin Action for uploading.**

- **Action (`litetraffic/upload-action`, composite).** It runs `litetraffic upload` with `LITETRAFFIC_TOKEN` from repo secrets, and writes the run link to `$GITHUB_STEP_SUMMARY`. It posts nothing else to GitHub, so it works identically on the free CLI flow with the service turned off.
- **GitHub App.** Permissions: `checks: write`, `pull_requests: write`, `metadata: read`. Events: `installation` and `installation_repositories` only.
- **Linking an installation to an org.** Only through `GET /github/setup`, the App's setup URL:
  1. The user starts from the org settings page, which issues a `state` nonce bound to the `__Host-` session and the org, and requires the user to be an owner of that org.
  2. On the callback, the server checks `state`, then calls `GET /user/installations` with the user's own OAuth token and confirms that the `installation_id` is in that list.
  3. The user must administer the GitHub account: it is their own account, or `GET /orgs/{org}/memberships/{user}` returns `role=admin`.
  4. An installation already bound to another org is rejected, never reassigned.
  A forged `installation_id` on the callback fails step 2, and an integration test covers this. Webhooks only update or delete existing rows, never create links.
- **Upload flow.** When a run arrives with `github_repository`, `git_sha` and `pr_number`, the project's repo equals `github_repository`, and that repo is in an installation linked to the same org, the server:
  1. Checks the repo is in the installation live (`GET /installation/repositories`), that the commit exists in the repo, and, for PR builds, that PR `pr_number` has `head.sha == git_sha`. If any check fails, the run is stored and nothing is posted.
  2. Creates or updates a check run `LiteTraffic / <scenario>`. Verdict mapping: pass → success, fail → failure, inconclusive → neutral, error → failure. Each org can change the mapping.
  3. Upserts one sticky PR comment, tracked in `pr_comments`, under `pg_advisory_xact_lock(hash(project_id, pr_number))` so concurrent uploads from one CI job cannot create two comments. It shows a table of scenarios × verdict × p95, the delta against the latest run for the same scenario on `base_ref` (via `compare_runs`), and team links. Every uploaded string is rendered inside a code span with backticks escaped, and links are built only from server IDs. On public repos the comment defaults to verdict counts plus a link; an org or repo setting allows the full table.
- **Why not an Action posting with `GITHUB_TOKEN`?** Fork PRs get read-only tokens. There would be no durable identity for updating comments, and the result would duplicate the data the service already holds. It stays possible for free users as a documented recipe, not a product.
- **Security.**
  - Webhooks are verified with HMAC-SHA256 against the app secret.
  - Installation tokens are minted per call and never stored.
  - Check runs are self-reported CI evidence: anyone holding an upload token can create them for real commits. The docs say not to make them the only required check. `ponytail: accept GitHub Actions OIDC tokens in place of ltu_ later, taking repository, sha and ref from the signed claims.`

## Billing hook

- `orgs.plan` plus one function, `entitlements(org) -> {max_members, max_projects, runs_per_month, storage_bytes_per_month, max_bundle_bytes, retention_days, ai_budget_micros, github_app: bool}`. Every limit in this spec reads from it.
- `POST /billing/webhook` verifies the provider's signature and maps subscription events to `orgs.plan` and `billing_customer_id`. The provider (Stripe or Lemon Squeezy) is chosen in Phase 4. No other code depends on the choice.
- Until Phase 4, plans are set by hand in SQL for design partners.

## Landing page changes

Shipped in Phase 0, since the early-access card is how Phase 0 measures demand.

- The headline stays "Users as an API." There is no new tagline.
- Add `Pricing.astro` between `#start` and the closing CTA, which means splitting the CTA out of `Start.astro`. Add `#pricing` to the nav and the footer's Product column.
- Two cards:
  - **Open source CLI**: free, MIT, available now, runs in your CI.
  - **LiteTraffic Cloud**: early access, pricing to be announced. It lists history, PR checks, team links and AI explanations. The CTA is "Join early access", a mailto that asks for the team name and CI provider. No form, because a form needs a backend.
- A line under the cards: "Cloud stores finished runs you upload. It never sends traffic to your app."
- Copy fixes:
  - "no hosted account" becomes "The CLI needs no hosted account".
  - Add "LiteTraffic Cloud (early access)" to the "Not shipped yet" chips until Phase 1 ships.
  - Update the roadmap under Next/Later.
  - Amend TECH_SPEC §1 and §15 to point to this spec.

## Milestones

Each phase ships on its own and stops if its evidence is weak. **The order of Phases 2 and 3 is chosen by which feature Phase 0 teams asked for most.**

**Phase 0: validate (no cloud code).**
- Work:
  - Publish a CI recipe (a GitHub Actions example that uses `verify --json` and the exit codes).
  - Ship the landing page changes above, including the early-access card.
  - Recruit 5-10 teams to run the free CLI in CI.
  - Interview them about history, PR checks, sharing and willingness to pay.
- Accept when:
  - At least 5 teams run LiteTraffic in CI weekly for 3 weeks.
  - At least 5 early-access emails name a CI provider, and at least 3 interview notes ask for history, PR checks or sharing without being prompted.
  - Pricing hypotheses are written down.

**Phase 1: minimal hosted.**
- Work:
  - `litetraffic upload` with `--dry-run`. This is useful on its own as a sanitised export.
  - Redact-before-truncate in the engine (redaction step 0).
  - `cloud-api` and Postgres, GitHub login, orgs, auto-created projects and org tokens.
  - The SPA with auth, run list and detail, and JSON file downloads.
  - The upload Action with the job-summary link.
  - Deployed on Dokploy.
- Accept when:
  - A design partner's CI uploads and a teammate opens the run through the job-summary link.
  - The canary redaction test and the isolation matrix pass.
  - A re-upload, including a "Re-run job" attempt, is a no-op, and a 5 MiB bundle gets a 413.
  - With the server unreachable, the CI job still passes.

**Phase 2: history and AI.**
- Work: trends, flaky view, diff across builds, AI explanations with consent and budgets.
- Accept when:
  - The trend and flaky views match fixtures.
  - Budget exhaustion returns 402, and concurrent explains cannot exceed the budget.
  - Provider errors are never echoed.

**Phase 3: GitHub.**
- Work: the App, installation linking, check runs and the sticky comment.
- Accept when:
  - A PR in a test repo shows a check per scenario and exactly one comment updated across pushes, including concurrent uploads.
  - A spoofed repo, a spoofed sha, a mismatched PR number and a forged installation link all post nothing.

**Phase 4: billing.**
- Work: the provider webhook drives `orgs.plan`, and limits are enforced from entitlements.
- Accept when a test-mode subscription upgrade raises the limits without a deploy.

## Testing strategy

- **Upload builder (unit, core CI).** Golden run directories carry planted canaries:
  - a token in a URL query and a token in a URL path segment
  - a JWT in `console.log` and stderr
  - an absolute home path in `argv`
  - the target host inside assertion details, plus a non-target internal host and a JSON-escaped URL
  - an `sk_live_` key
  - a declared secret in escaped forms, and one straddling the 500-character `detail` cap

  The test asserts that no canary string or fragment appears anywhere in the serialized bundle, that step 3 hits refuse the upload without `--allow-scrubbed`, and that the allowlist is exact (a new run file is excluded by default).
- **Server (integration).** Run against ephemeral Postgres in Docker Compose:
  - upload validation and limits, including an anonymous gzip bomb, a slowloris client, a chunked body and a disallowed file name
  - idempotency and 409
  - token hashing, expiry and revocation
  - session, OAuth `state` and CSRF rules
  - the isolation route matrix and the SQL guard
  - file downloads: headers, and a name not in `run_files` gets 404
- **AI.** A fake provider that reports token usage, for budget reservation under concurrency, 402 and error redaction. A test that a `</run>` string in evidence stays inside the block. `build_prompt` stays covered by the existing tests.
- **GitHub.** Recorded webhook payloads with valid and invalid signatures. The GitHub API is stubbed. Tests cover the verdict-mapping table, the forged installation link, sha and PR checks, comment escaping and the comment lock.
- **End to end.** A CI job brings up the stack, runs `verify` on a bundled conformance example (fake k6 output is enough), runs `upload`, then reads the run back through the session API.

## Open questions

1. **Pricing.** Tiers, numbers, and whether AI and GitHub are paid-only. These are set from Phase 0 interviews, not in this spec.
2. **Values by default.** Should failing-sample values be uploaded by default, or should `--no-values` be the default with AI explanations degraded?
3. **Login.** Is GitHub-only login enough, or do some teams need email magic links?
4. **Postgres RLS.** Is it worth adding as defense in depth in Phase 1, or later?
5. **Retention and residency.** What retention does each plan get, and does any design partner need EU-only storage?
6. **Series.** Should `--repeat` series and `up` activities be uploadable as first-class objects once teams use them in CI?
7. **Self-hosting.** Should the cloud package be MIT like the CLI, or source-available, and can teams self-host it?

## Review notes

Review issues rejected or deferred, and why:

- **Replace the stdlib server with uvicorn or gunicorn.** Rejected. A bounded pool, socket timeouts, auth before body and Traefik limits cover the threat with no new dependency. A second review asked to keep the stdlib server.
- **Shannon-entropy check on tokens of 20 or more characters.** Rejected. Bundles carry sha256 hashes, UUIDs and run IDs, so an entropy rule would fire on every upload and make refuse-by-default unusable. Known credential prefixes and path-segment redaction are used instead.
- **A per-project salt returned in an upload preflight.** Rejected in favour of an HMAC applied server-side to the client's hash, which needs no extra round trip.
- **An invites table.** Deferred. Members are added after their first login, by `github_id`, which also removes the recyclable-username risk.
- **GitHub secret scanning partner registration.** Deferred until there are enough users. The checksum suffix is added now, so it can be registered later.
- **GitHub Actions OIDC in place of `ltu_` tokens.** Deferred beyond Phase 3. Live sha and PR verification closes the main spoofing gap now.
- **Sandboxed iframe for reports.** Moot. There is no hosted report in Phase 1, and a later report opens in a new tab only.
- **Project-scoped tokens and `POST /projects`.** Dropped from Phase 1. Projects are auto-created from the repo, and tokens are org-scoped. Add both when a customer asks.
