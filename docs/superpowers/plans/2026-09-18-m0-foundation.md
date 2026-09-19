# LiteTraffic M0 Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Build an installable standalone CLI that validates, inspects, and preflights a bounded LiteTraffic scenario before any traffic is sent.

**Architecture:** A small Python package owns strict manifest models, conservative budget compilation, and CLI presentation. `doctor` performs read-only environment checks; `inspect` loads a scenario directory, validates the script path and proves its declared journey workload fits its budgets. The implementation deliberately stops before executing k6 so event framing can be selected from an engine-fit spike.

**Tech Stack:** Python 3.11+, Pydantic 2, HTTPX, argparse, pytest

**Spec:** `docs/TECH_SPEC.md`

## Global Constraints

- Runtime verification must not require an LLM account or E2B account.
- Scenario manifests are JSON, reject unknown fields, and never contain secret values.
- Scenario script paths cannot escape the scenario bundle.
- `doctor` is read-only and never installs software or mutates the target.
- Journey admission rate is distinct from HTTP request rate.
- Budget validation includes every declared journey admitted by every schedule phase.

---

### Task 1: Package, strict scenario model, and budget proof

**Files:**
- Create: `pyproject.toml`
- Create: `src/litetraffic/__init__.py`
- Create: `src/litetraffic/models.py`
- Create: `src/litetraffic/scenario.py`
- Create: `tests/test_scenario.py`

**Interfaces:**
- Produces: `load_scenario(path: Path) -> ScenarioBundle`
- Produces: `ScenarioManifest.planned_journeys`, `maximum_journey_requests`, and `maximum_journey_writes`

- [x] Write tests for a valid bundle, unknown fields, path escape, missing script, and insufficient request/write budgets.
- [x] Run `pytest tests/test_scenario.py -q` and confirm failures because the package does not exist.
- [x] Add package metadata and the smallest Pydantic models/loader that make those tests pass.
- [x] Run `pytest tests/test_scenario.py -q` and confirm all tests pass.

### Task 2: Read-only environment doctor

**Files:**
- Create: `src/litetraffic/doctor.py`
- Create: `tests/test_doctor.py`

**Interfaces:**
- Produces: `run_doctor(target: str | None, k6_path: str | None) -> DoctorReport`
- Consumes: injected executable path or PATH; optional HTTP target

- [x] Write tests for present/missing k6, reachable/unreachable target, and rejection of unsupported target schemes.
- [x] Run `pytest tests/test_doctor.py -q` and confirm failures because the module does not exist.
- [x] Implement subprocess version detection and a GET-only HTTP reachability check with bounded timeout.
- [x] Run `pytest tests/test_doctor.py -q` and confirm all tests pass.

### Task 3: CLI inspection and machine-readable output

**Files:**
- Create: `src/litetraffic/cli.py`
- Create: `tests/test_cli.py`
- Create: `examples/checkout/manifest.json`
- Create: `examples/checkout/journeys.js`
- Create: `.gitignore`
- Create: `README.md`

**Interfaces:**
- Produces: `litetraffic doctor [--target URL] [--k6-path PATH] [--json]`
- Produces: `litetraffic inspect SCENARIO [--json]`
- Consumes: `load_scenario` and `run_doctor`

- [x] Write CLI tests proving stable JSON output and exit status for valid/invalid scenarios and doctor failures.
- [x] Run `pytest tests/test_cli.py -q` and confirm failures because the CLI does not exist.
- [x] Implement argparse entry point and one minimal example bundle; document installation and current scope.
- [x] Run the complete test suite and both example commands.

### Task 4: Quality gate

**Files:**
- Modify only files found defective by verification.

- [x] Run `python -m pip install -e .` in the available environment.
- [x] Run `pytest -q` and confirm all tests pass with clean output.
- [x] Run `litetraffic inspect examples/checkout --json` and validate the JSON.
- [x] Run `litetraffic doctor --json`; a missing k6 is an expected failed check and nonzero command status, not a test failure.
- [x] Scan the plan and implementation for placeholders, target writes, secret values, path escapes, and unsupported claims.

