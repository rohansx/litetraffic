# Contributing

Thanks for helping improve LiteTraffic. Issues and pull requests are welcome.

## Setup

```bash
git clone https://github.com/rohansx/litetraffic.git
cd litetraffic
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
python -m pytest -q
```

The unit tests use engine doubles and do not need k6 or a running app. To check behavior end to end, install k6 v2.2.0 and follow the [quickstart](docs/quickstart.md).

### Web UI

The dashboard (`web/apps/dashboard`, Vite + React + shadcn/ui) and the landing page (`web/apps/site`, Astro) share one design system in `web/packages/design`. Colours live only in `web/packages/design/tokens.css`; a test rejects colour literals anywhere else. With Node 26 and pnpm 11:

```bash
pnpm -C web install --frozen-lockfile
pnpm -C web typecheck
pnpm -C web test
pnpm -C web build
```

Commit the rebuilt `src/litetraffic/dashboard_ui/` with any dashboard change; CI rebuilds it and fails if it differs.

## Pull requests

- Keep changes focused; one behavior per pull request.
- Add or update tests for any behavior change. A new example scenario should have both a correct and a faulty server mode and should fail on the faulty one.
- Update the relevant page under [`docs/`](docs/README.md) when commands, manifest fields, or verdict rules change.
- Use [Conventional Commits](https://www.conventionalcommits.org/) messages, for example `feat: add write counting` or `fix: reject empty observation path`.
- Make sure `python -m pytest -q` passes (and the `pnpm -C web` checks when you touch `web/`) and every example still validates with `litetraffic inspect`.

## Reporting bugs

Include the command, LiteTraffic version, k6 version, OS, and the run's `result.json` and `engine.stderr.log`. Remove tokens, internal hostnames, and any data you would not publish.

Security issues go through [SECURITY.md](SECURITY.md), not public issues.

By contributing, you agree that your contributions are licensed under the [MIT License](LICENSE).
