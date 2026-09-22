# Roadmap

[Documentation index](README.md)

The long-term goal is **users as an API**: give an application a believable, stateful population of users and get back evidence about what they changed. The developer preview builds the verification core that everything else depends on.

## Available now (`0.1.0.dev0`)

- `doctor`, `inspect`, `verify`, `diff`
- Explicit phases and seeded spiky, random-burst, and sustained-burst profiles
- Declared budgets with static and post-run checks
- Assertion evidence, verdicts, lifecycle, HTML report
- Run-owned HTTP fixtures and final observations
- Repeated seeds and baseline/candidate comparison with an optional p95 gate
- Local, reachable HTTP(S), and existing E2B sandbox targets
- Five conformance examples, each with a correct and a faulty server

## Next

- Digest scripts and imports alongside the manifest for stronger comparisons
- Run-time write counting and in-flight enforcement
- macOS release qualification and a PyPI release

## Later

- Repository discovery and assisted scenario authoring
- Longer-running population mode with persistent synthetic users
- Managed sandbox provisioning

Items under Next and Later are plans, not commitments or shipped features. Open an issue to discuss priorities.
