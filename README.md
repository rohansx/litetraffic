# LiteTraffic

LiteTraffic is a standalone tool for describing believable, bounded application traffic and verifying its effects. The product architecture lives in [docs/TECH_SPEC.md](docs/TECH_SPEC.md).

The current M0 foundation validates portable scenario bundles before any traffic is sent. It proves schedule-derived request and write bounds, confines scripts to their bundle, checks for k6, and performs an optional read-only target reachability check.

## Development setup

Python 3.11 or newer is required.

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
pytest -q
```

Inspect the included scenario:

```bash
litetraffic inspect examples/checkout
litetraffic inspect examples/checkout --json
```

Check local prerequisites and an optional running target:

```bash
litetraffic doctor
litetraffic doctor --target http://localhost:3000/health
```

`doctor` only makes a GET request and does not install software. The first execution slice will be implemented after the engine-fit spike fixes a supported k6 version and validates structured event capture. The example `journeys.js` is therefore intentionally non-executing in M0.

## Scenario safety contract

- Manifests are JSON and reject unknown fields.
- Scenario scripts must remain inside the bundle directory.
- Schedule rates are journey admissions per second, not HTTP requests per second.
- Maximum requests and writes are derived conservatively from every admitted journey.
- A bundle that exceeds its declared duration, request, or write budget is rejected.
- Runtime secrets and target URLs do not belong in portable manifests.

