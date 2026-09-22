# Roadmap

[Documentation index](README.md)

The long-term goal is **users as an API**: give an application a believable, stateful population of users and get back evidence about what they changed. The developer preview builds the verification core that everything else depends on.

## Available now (`0.1.0.dev0`)

- `doctor`, `inspect`, `verify`, `diff`
- Explicit phases and seeded spiky, random-burst, and sustained-burst profiles
- Declared budgets with static and post-run checks, including counted write attempts and k6 `vus_max`
- Assertion evidence, verdicts, lifecycle, HTML report
- Run-owned HTTP fixtures and final observations
- Repeated seeds and baseline/candidate comparison with an optional p95 gate
- Local, reachable HTTP(S), and existing E2B sandbox targets
- Five conformance examples, each with a correct and a faulty server
- `up`: a foreground background activity of repeated bounded `verify` slices, reported with `mode=background` and no verdict

## Next

- macOS release qualification and a PyPI release

## Later

- Repository discovery and assisted scenario authoring
- Population mode with persistent synthetic users across slices
- Managed sandbox provisioning

Items under Next and Later are plans, not commitments or shipped features. Open an issue to discuss priorities.
