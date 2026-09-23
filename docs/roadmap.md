# Roadmap

[Documentation index](README.md)

The long-term goal is **users as an API**: give an application a believable, stateful population of users and get back evidence about what they changed. The developer preview builds the verification core that everything else depends on.

## Available now (`0.1.0.dev0`)

- `doctor`, `inspect`, `verify`, `diff`
- `approve` and `verify --require-approval`/`--approved-digest`: local approvals bound to the scenario digest and target origin
- `dashboard`: a read-only local page on `127.0.0.1:8780` for runs, series, activities, per-scenario p95 trends and diffs, with filters, auto-refresh and a light/dark theme
- `prune`: run retention by count or age
- Explicit phases and seeded spiky, random-burst, and sustained-burst profiles
- Declared budgets with static and post-run checks, including counted write attempts and k6 `vus_max`
- Assertion evidence, verdicts, lifecycle, HTML report
- Run-owned HTTP fixtures, `fixtures.command` setup/teardown hooks, and a per-journey `fixtures.pool`
- Final observations with matchers, `${planned_journeys}` expected-value expressions, and a second origin from `allowed_origins` with `headers_env` secrets
- Actor `auth` recipes: per-actor-class HS256 JWTs passed to k6 as `LT_TOKEN_<CLASS>` (or, with `per_identity`, `count` distinct identities as `LT_TOKENS_<CLASS>`, picked with `tokenFor()`), redacted, with every declared credential, from kept artifacts and output
- Concurrency proof: observed peak in-flight requests per operation (`metrics.overlap`) checked against journey `min_overlap`
- Per-operation `expected_statuses` and `metrics.unexpected_http_failure_rate`
- Examples emit expected/actual values on failing evidence samples
- Repeated seeds (consecutive or `--same-seed`) with dispersion stats, and baseline/candidate comparison with an optional p95 gate and per-operation p95
- Local, reachable HTTP(S), and existing E2B sandbox targets
- Five conformance examples, each with a correct and a faulty server, built on a bundled k6 runtime helper
- `up`: a foreground background activity of repeated bounded `verify` slices, reported with `mode=background` and no verdict

## Next

- macOS release qualification and a PyPI release

## Later

- Repository discovery and assisted scenario authoring
- Population mode with persistent synthetic users across slices
- Managed sandbox provisioning

Items under Next and Later are plans, not commitments or shipped features. Open an issue to discuss priorities.
