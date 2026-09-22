# LiteTraffic documentation

[Repository home](../README.md)

LiteTraffic is a local CLI for running reviewed, stateful HTTP traffic and evaluating business assertions. These guides describe the implemented `0.1.0.dev0` preview.

## Start here

1. [Install LiteTraffic and k6](installation.md): Python, operating systems, dependencies, and prerequisite checks.
2. [Run the quickstart](quickstart.md): a passing checkout, a deliberately broken checkout, and the other scenarios.
3. [Read the output](results.md): verdicts, lifecycle, reports, repeat summaries, and baseline comparisons.
4. [Adapt a scenario to your app](scenarios.md): traffic profiles, request sequences, fixtures, identity, and evidence.

## Reference

- [CLI reference](cli.md): every current command, argument, default, and exit code.
- [Architecture](architecture.md): controller, k6 process, fixture/observer calls, and artifacts.
- [Safety and limitations](safety.md): trust boundaries, budget enforcement, secrets, and cleanup.
- [Troubleshooting](troubleshooting.md): engine version, reachability, incomplete evidence, and comparison failures.
- [Roadmap](roadmap.md): implemented scope versus future authoring and population work.
- [Contributing](../CONTRIBUTING.md) and [security reporting](../SECURITY.md).

## Design records

[Engine-fit results](ENGINE_FIT.md) record earlier real-engine validation. The [original technical specification](TECH_SPEC.md) is a design proposal, not a command reference: some interfaces in it are not implemented. Use the guides above for commands that work today.

The documentation lives at the repository's `/docs` path and renders directly on GitHub. LiteTraffic does not start a documentation web server or ship a marketing site.
