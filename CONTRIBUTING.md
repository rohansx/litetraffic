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

## Pull requests

- Keep changes focused; one behavior per pull request.
- Add or update tests for any behavior change. A new example scenario should have both a correct and a faulty server mode and should fail on the faulty one.
- Update the relevant page under [`docs/`](docs/README.md) when commands, manifest fields, or verdict rules change.
- Use [Conventional Commits](https://www.conventionalcommits.org/) messages, for example `feat: add write counting` or `fix: reject empty observation path`.
- Make sure `python -m pytest -q` passes and every example still validates with `litetraffic inspect`.

## Reporting bugs

Include the command, LiteTraffic version, k6 version, OS, and the run's `result.json` and `engine.stderr.log`. Remove tokens, internal hostnames, and any data you would not publish.

Security issues go through [SECURITY.md](SECURITY.md), not public issues.

By contributing, you agree that your contributions are licensed under the [MIT License](LICENSE).
