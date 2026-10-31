"""The console behind `litetraffic dashboard`: a scenario library, one background job at a time, and AI scenario drafting.

Everything here is reached through the dashboard's loopback server (see dashboard.py for the request guards). Scenarios are
trusted code, so the console never runs anything the user has not saved: a bundle with a setup command must be explicitly
trusted when saved, and a trusted bundle that changes on disk must be saved again before it runs.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from datetime import UTC, datetime
from pathlib import Path

import httpx
from pydantic import ValidationError

from litetraffic.auth import redact
from litetraffic.capture import parse_spec
from litetraffic.inspection import inspect_payload
from litetraffic.kits import tenant_isolation
from litetraffic.scenario import ScenarioBundle, ScenarioError, load_scenario
from litetraffic.target import validate_target

NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,62}")
PART = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
ENV = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
MAX_BUNDLE_BYTES = 2 * 1024 * 1024
MAX_BUNDLE_FILES = 50
MAX_SPEC_BYTES = 300 * 1024
MAX_DRAFT_ATTEMPTS = 4
MAX_LOG_LINES = 300
MAX_LINE = 2000
MAX_JOBS = 50
MIN_REDACTED = 8  # like the runner: a shorter value would mangle ordinary log text
CANCEL_GRACE_SECONDS = 15
DRAFT_MODEL = "claude-sonnet-5-5"
DRAFT_TOKENS = 20000
GUIDE = Path(__file__).parent / "authoring" / "scenarios.md"
# The child never inherits the console's own secrets (an ANTHROPIC_API_KEY, cloud credentials): only what a runner needs.
INHERITED_ENV = ("PATH", "HOME", "USER", "LANG", "LC_ALL", "TMPDIR", "TERM", "SHELL", "SYSTEMROOT", "PYTHONPATH", "VIRTUAL_ENV",
                 "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy")
FORBIDDEN_ENV = re.compile(
    r"(PATH|HOME|USER|SHELL|TMPDIR|PWD|LANG|TERM|SYSTEMROOT|VIRTUAL_ENV|LD_.*|DYLD_.*|PYTHON.*|NODE_.*|K6_.*|SSL_.*|REQUESTS_.*|"
    r".*_PROXY|ANTHROPIC_.*|OPENAI_.*|LT_TOKENS?_.*|LITETRAFFIC_.*)"
)


class ConsoleError(Exception):
    """A request the console refuses; `status` and `extra` become the JSON error response."""

    def __init__(self, status: int, message: str, **extra: object) -> None:
        super().__init__(message)
        self.status, self.extra = status, extra


def _now() -> str:
    return datetime.now(UTC).isoformat()


# A console started in the background (nohup, a service manager) inherits SIGINT as ignored, and Python then never turns Ctrl-C
# into KeyboardInterrupt: Cancel would be ignored and the run would finish. Restore it before anything else runs.
SIGINT_BOOT = "import signal; signal.signal(signal.SIGINT, signal.default_int_handler); "


def verify_argv(scenario: Path, args: list[str]) -> list[str]:
    """The command that runs a scenario; tests replace it with a stand-in that needs no k6."""
    code = SIGINT_BOOT + "import sys; from litetraffic.cli import entrypoint; sys.argv[0] = 'litetraffic'; entrypoint()"
    return [sys.executable, "-c", code, "verify", str(scenario), *args]


def anthropic_client(api_key: str):
    try:
        import anthropic
    except ImportError as exc:
        raise ConsoleError(422, "Install litetraffic[ai] to draft scenarios with Claude.") from exc
    return anthropic.Anthropic(api_key=api_key)


def ai_available() -> bool:
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def draft_model() -> str:
    return os.environ.get("LITETRAFFIC_DRAFT_MODEL") or DRAFT_MODEL


def _message(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        problems = []
        for error in exc.errors():
            where = ".".join(str(part) for part in error["loc"])
            text = error["msg"].removeprefix("Value error, ")
            problems.append(f"{where}: {text}" if where else text)
        return "; ".join(problems)
    return str(exc)


# ---- library --------------------------------------------------------------------------------------------------------


def _check_name(name: object) -> str:
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise ConsoleError(422, "name must be 1-63 characters: lower-case letters, digits, '-' or '_', starting with a letter or digit")
    return name


def _check_files(files: object) -> dict[str, str]:
    if not isinstance(files, dict) or not files:
        raise ConsoleError(422, "files must be an object of file path to text, and include manifest.json")
    total = 0
    for path, text in files.items():
        parts = path.split("/") if isinstance(path, str) else []
        if not (1 <= len(parts) <= 4 and all(PART.fullmatch(part) and part not in {".", ".."} for part in parts)):
            raise ConsoleError(422, f"file path {path!r} is not allowed: use plain relative names such as journeys.js")
        if not isinstance(text, str) or "\x00" in text:
            raise ConsoleError(422, f"{path} must be text")
        total += len(text.encode())
    if len(files) > MAX_BUNDLE_FILES:
        raise ConsoleError(422, f"too many files (limit {MAX_BUNDLE_FILES})")
    if total > MAX_BUNDLE_BYTES:
        raise ConsoleError(422, f"bundle too large ({total} bytes, limit {MAX_BUNDLE_BYTES})")
    if "manifest.json" not in files:
        raise ConsoleError(422, "manifest.json is required")
    return files


def _write_bundle(root: Path, files: dict[str, str]) -> None:
    root.mkdir(mode=0o700)
    for path, text in files.items():
        target = root / path
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        target.chmod(0o600)


def _load(root: Path) -> ScenarioBundle:
    try:
        return load_scenario(root)
    except (ScenarioError, ValidationError, ValueError, OSError) as exc:
        raise ConsoleError(422, _message(exc)) from None


class Library:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._lock = threading.Lock()

    def _dir(self, name: str) -> Path | None:
        if not NAME.fullmatch(name):
            return None
        path = self.root / name
        return path if path.is_dir() and not path.is_symlink() else None

    def _trust_file(self, name: str) -> Path:
        return self.root / ".trust" / name

    def names(self) -> list[str]:
        if not self.root.is_dir():
            return []
        return sorted(path.name for path in self.root.iterdir() if NAME.fullmatch(path.name) and path.is_dir() and not path.is_symlink())

    def entry(self, name: str) -> dict:
        path = self._dir(name)
        modified = datetime.fromtimestamp((path / "manifest.json").stat().st_mtime if (path / "manifest.json").exists() else 0, UTC).isoformat()
        base = {"name": name, "digest": None, "planned_journeys": None, "assertions": [], "secret_env": [], "has_command": False, "modified_at": modified}
        try:
            payload = inspect_payload(load_scenario(path))
        except Exception as exc:  # noqa: BLE001 - a broken saved bundle is shown, never fatal
            return {**base, "error": _message(exc) or type(exc).__name__}
        return {
            **base,
            "digest": payload["scenario_sha256"],
            "planned_journeys": payload["planned_journeys"],
            "assertions": list(payload["assertions"]),
            "secret_env": payload["secret_env"],
            "has_command": bool(payload["fixture"]["command"]),
            "error": None,
        }

    def list(self) -> list[dict]:
        return [self.entry(name) for name in self.names()]

    def get(self, name: str) -> dict:
        path = self._dir(name)
        if path is None:
            raise ConsoleError(404, "no such scenario")
        files = {}
        for item in sorted(path.rglob("*")):
            relative = item.relative_to(path)
            if item.is_file() and not item.is_symlink() and not any(part.startswith(".") for part in relative.parts) and item.stat().st_size <= MAX_BUNDLE_BYTES:
                try:
                    files[relative.as_posix()] = item.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    continue
        entry = self.entry(name)
        inspect = None
        if entry["error"] is None:
            inspect = inspect_payload(load_scenario(path))
        return {"name": name, "digest": entry["digest"], "files": files, "inspect": inspect, "error": entry["error"]}

    def save(self, name: object, files: object, trust_command: bool = False, overwrite: bool = False) -> dict:
        name, files = _check_name(name), _check_files(files)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self._lock:
            if (self.root / name).exists() and not overwrite:
                raise ConsoleError(409, f"a scenario called {name} already exists")
            staging = Path(tempfile.mkdtemp(prefix=".new-", dir=self.root))
            try:
                bundle_dir = staging / "bundle"
                _write_bundle(bundle_dir, files)
                bundle = _load(bundle_dir)
                command = bundle.manifest.fixtures.command
                if command and not trust_command:
                    raise ConsoleError(400, "this scenario runs a setup command on your machine; review it and trust it to save", needs_trust=True, command=list(command.setup))
                final = self.root / name
                old = None
                if final.exists() or final.is_symlink():
                    old = staging / "old"
                    final.rename(old)
                bundle_dir.rename(final)
                bundle = load_scenario(final)  # the digest to remember is the one at the final path
            finally:
                shutil.rmtree(staging, ignore_errors=True)
            trust = self._trust_file(name)
            if command and trust_command:
                trust.parent.mkdir(mode=0o700, exist_ok=True)
                trust.write_text(bundle.digest, encoding="utf-8")
            else:
                trust.unlink(missing_ok=True)
        return {"name": name, "digest": bundle.digest, "has_command": bool(command)}

    def from_kit(self, name: object, config: object) -> dict:
        name = _check_name(name)
        if not isinstance(config, dict):
            raise ConsoleError(422, "config must be a JSON object")
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp, "kit.json")
            source.write_text(json.dumps(config), encoding="utf-8")
            try:
                tenant_isolation.generate(source, Path(tmp, "out"))
            except ValidationError as exc:
                raise ConsoleError(422, f"invalid kit config: {_message(exc)}") from None
            except (ScenarioError, ValueError) as exc:
                raise ConsoleError(422, f"invalid kit config: {exc}") from None
            files = {path.name: path.read_text(encoding="utf-8") for path in Path(tmp, "out").iterdir() if path.is_file()}
        return self.save(name, files)

    def delete(self, name: str) -> dict:
        with self._lock:
            path = self._dir(name)
            if path is None:
                raise ConsoleError(404, "no such scenario")
            shutil.rmtree(path)
            self._trust_file(name).unlink(missing_ok=True)
        return {"deleted": name}

    def runnable(self, name: object) -> Path:
        """The scenario directory to run, or a refusal. A scenario with a setup command only runs if it is still what was trusted."""
        path = self._dir(name) if isinstance(name, str) else None
        if path is None:
            raise ConsoleError(404 if isinstance(name, str) and NAME.fullmatch(name) else 422, "no such scenario")
        bundle = _load(path)
        if bundle.manifest.fixtures.command:
            trust = self._trust_file(name)
            if not trust.is_file() or trust.read_text(encoding="utf-8").strip() != bundle.digest:
                raise ConsoleError(422, f"{name} runs a setup command and has changed since you approved it: review it and save it again with the command trusted")
        return path


# ---- jobs -----------------------------------------------------------------------------------------------------------


class Job:
    def __init__(self, kind: str, **fields: object) -> None:
        self.id = "job_" + secrets.token_hex(6)
        self.kind = kind
        self.state = "running"
        self.scenario = self.target = self.seed = self.repeat = None
        self.env_names: list[str] = []
        self.started_at, self.finished_at = _now(), None
        self.exit_code = self.verdict = self.run_id = self.result = None
        self.log: deque[str] = deque(maxlen=MAX_LOG_LINES)
        self.secrets: list[str] = []
        self.cancel_requested = False
        self.stage: str | None = None
        self.process: subprocess.Popen | None = None
        self.__dict__.update(fields)
        self.lock = threading.Lock()

    def add(self, line: str) -> None:
        text = redact(line.rstrip("\n")[:MAX_LINE], self.secrets)
        with self.lock:
            self.log.append(text)

    def summary(self) -> dict:
        keys = ("id", "kind", "state", "scenario", "target", "seed", "repeat", "env_names", "started_at", "finished_at", "exit_code", "verdict", "run_id")
        return {key: getattr(self, key) for key in keys}

    def detail(self) -> dict:
        with self.lock:
            return {**self.summary(), "log": list(self.log), "result": self.result}


def _check_env(env: object) -> dict[str, str]:
    if env is None:
        return {}
    if not isinstance(env, dict):
        raise ConsoleError(422, "env must be an object of environment variable name to value")
    for name, value in env.items():
        if not isinstance(name, str) or not ENV.fullmatch(name) or FORBIDDEN_ENV.fullmatch(name):
            raise ConsoleError(422, f"{name!r} cannot be set as an environment variable for a run: use an upper-case name such as MYAPP_TOKEN (not a system or LiteTraffic name)")
        if not isinstance(value, str) or not value or "\x00" in value or len(value) > 4096:
            raise ConsoleError(422, f"the value of environment variable {name} must be non-empty text")
    return dict(env)


def _int(value: object, name: str, low: int, high: int | None, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or value < low or (high is not None and value > high):
        raise ConsoleError(422, f"{name} must be a whole number" + (f" from {low} to {high}" if high is not None else f" of at least {low}"))
    return value


def _child_env(extra: dict[str, str]) -> dict[str, str]:
    return {**{name: os.environ[name] for name in INHERITED_ENV if name in os.environ}, **extra}


class Console:
    """The library plus the single active job; one instance per dashboard server."""

    def __init__(self, runs_dir: Path, library_dir: Path) -> None:
        self.runs_dir = Path(runs_dir)
        self.library = Library(library_dir)
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    # -- bookkeeping
    def _add(self, job: Job) -> None:
        with self._lock:
            active = self._active()
            if active:
                raise ConsoleError(409, "A test is already running. Wait for it to finish or cancel it.", job_id=active.id)
            self._jobs[job.id] = job
            for stale in [item for item in self._jobs.values() if item.state != "running"][: max(0, len(self._jobs) - MAX_JOBS)]:
                del self._jobs[stale.id]

    def _active(self) -> Job | None:
        return next((job for job in self._jobs.values() if job.state == "running"), None)

    def busy_job(self) -> str | None:
        with self._lock:
            active = self._active()
            return active.id if active else None

    def jobs(self) -> list[dict]:
        with self._lock:
            return [job.summary() for job in sorted(self._jobs.values(), key=lambda item: item.started_at, reverse=True)]

    def job(self, job_id: str) -> Job:
        with self._lock:
            if job_id not in self._jobs:
                raise ConsoleError(404, "no such job")
            return self._jobs[job_id]

    # -- test runs
    def start_run(self, body: dict) -> dict:
        scenario = self.library.runnable(body.get("scenario"))
        if body.get("confirm_target") is not True:
            raise ConsoleError(422, "confirm that you may send test traffic to this target")
        try:
            target = validate_target(body.get("target") if isinstance(body.get("target"), str) else "")
        except ValueError as exc:
            raise ConsoleError(422, str(exc)) from None
        seed = _int(body.get("seed"), "seed", 0, None, 0)
        repeat = _int(body.get("repeat"), "repeat", 1, 20, 1)
        env = _check_env(body.get("env"))
        args = ["--target", target, "--seed", str(seed), "--repeat", str(repeat), "--output-dir", str(self.runs_dir), "--json"]
        if body.get("capture"):
            try:
                parse_spec(body["capture"] if isinstance(body["capture"], str) else "")
            except ValueError as exc:
                raise ConsoleError(422, str(exc)) from None
            args += ["--capture", body["capture"]]
        job = Job("run", scenario=body["scenario"], target=target, seed=seed, repeat=repeat, env_names=sorted(env), secrets=[v for v in env.values() if len(v) >= MIN_REDACTED])
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        before = {path.name for path in self.runs_dir.iterdir()}
        self._add(job)
        try:
            job.process = subprocess.Popen(
                verify_argv(scenario, args), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                env=_child_env(env), start_new_session=True,
            )  # fmt: skip
        except OSError as exc:
            self._finish(job, "failed", None, error=f"could not start the runner: {exc}")
            return {"id": job.id}
        threading.Thread(target=self._watch_run, args=(job, before), daemon=True).start()
        return {"id": job.id}

    def _watch_run(self, job: Job, before: set[str]) -> None:
        process = job.process
        out: list[str] = []

        def pump(stream, keep: list[str] | None) -> None:
            for line in stream:
                if keep is not None:
                    keep.append(line)
                if not line.lstrip().startswith("{"):  # the JSON verdict is parsed below, not dumped into the log
                    job.add(line)

        threads = [threading.Thread(target=pump, args=(process.stdout, out), daemon=True), threading.Thread(target=pump, args=(process.stderr, None), daemon=True)]
        for thread in threads:
            thread.start()
        while process.poll() is None:
            self._find_run(job, before)
            self._narrate(job)
            time.sleep(0.2)
        for thread in threads:
            thread.join(timeout=5)
        self._find_run(job, before)
        self._narrate(job)
        payload = _last_json("".join(out))
        code = process.returncode
        if isinstance(payload, dict):
            job.run_id = payload.get("run_id") if isinstance(payload.get("run_id"), str) else job.run_id
            verdict = payload.get("verdict")
        else:
            verdict = None
        if code == 130 or (job.cancel_requested and code not in (0, 1, 2, 3)):
            state = "cancelled"
        elif code in (0, 1, 2) or (code == 3 and verdict == "error"):
            state = "finished"  # including a cancel that arrived after the run had already ended
        else:
            state = "failed"
        if verdict not in ("pass", "fail", "inconclusive", "error"):
            verdict = {0: "pass", 1: "fail", 2: "inconclusive"}.get(code) if state == "finished" else None
        self._finish(job, state, code, verdict=verdict)

    def _narrate(self, job: Job) -> None:
        """The runner is silent until it ends, so report each lifecycle stage its run.json records as it happens."""
        if not job.run_id:
            return
        try:
            stage = json.loads((self.runs_dir / job.run_id / "run.json").read_text(encoding="utf-8")).get("lifecycle")
        except (OSError, ValueError, AttributeError):
            return
        if isinstance(stage, str) and stage != getattr(job, "stage", None):
            job.stage = stage
            job.add(f"run {job.run_id}: {stage}")

    def _find_run(self, job: Job, before: set[str]) -> None:
        if job.run_id:
            return
        try:
            new = sorted(path for path in self.runs_dir.iterdir() if path.name not in before and path.is_dir() and (path / "run.json").is_file())
        except OSError:
            return
        if new:
            job.run_id = new[0].name

    def _finish(self, job: Job, state: str, code: int | None, verdict: str | None = None, error: str | None = None) -> None:
        if error:
            job.add(error)
        with self._lock:
            job.exit_code, job.verdict, job.finished_at = code, verdict, _now()
            job.state = state

    def cancel(self, job_id: str) -> dict:
        job = self.job(job_id)
        if job.state != "running":
            return {"state": job.state}
        job.cancel_requested = True
        job.add("cancel requested")
        if job.process is not None:
            threading.Thread(target=_stop, args=(job,), daemon=True).start()
        return {"state": "cancelling"}

    def shutdown(self) -> None:
        with self._lock:
            running = [job for job in self._jobs.values() if job.state == "running" and job.process is not None]
        for job in running:
            job.cancel_requested = True
            _signal(job.process, signal.SIGINT)
        deadline = time.time() + 5
        for job in running:
            while job.process.poll() is None and time.time() < deadline:
                time.sleep(0.05)
            if job.process.poll() is None:
                _signal(job.process, signal.SIGKILL)

    # -- AI drafting
    def start_draft(self, body: dict) -> dict:
        name = _check_name(body.get("name"))
        description, rules = (body.get(key) for key in ("app_description", "rules"))
        if not isinstance(rules, str) or not rules.strip():
            raise ConsoleError(422, "say what must never go wrong, in plain words (one rule per line)")
        if not isinstance(description, str):
            description = ""
        key = body.get("api_key") if isinstance(body.get("api_key"), str) and body.get("api_key").strip() else os.environ.get("ANTHROPIC_API_KEY", "")
        if not key:
            raise ConsoleError(422, "No Anthropic API key: set ANTHROPIC_API_KEY where the console runs, or paste a key into the form")
        spec = body.get("spec_text")
        if spec is not None and not isinstance(spec, str):
            raise ConsoleError(422, "spec_text must be text")
        if not spec and body.get("spec_url"):
            spec = _fetch_spec(body["spec_url"])
        if spec and len(spec.encode()) > MAX_SPEC_BYTES:
            raise ConsoleError(422, f"the API spec is too large ({len(spec.encode())} bytes, limit {MAX_SPEC_BYTES}): paste only the part the test needs")
        job = Job("draft", scenario=name, secrets=[key])
        client = anthropic_client(key)
        self._add(job)
        threading.Thread(target=self._run_draft, args=(job, client, name, description, rules, spec or ""), daemon=True).start()
        return {"id": job.id}

    def _run_draft(self, job: Job, client, name: str, description: str, rules: str, spec: str) -> None:
        tokens = {"input": 0, "output": 0}
        files: dict[str, str] = {}
        error: str | None = "no attempt completed"
        attempts = 0
        try:
            messages = [{"role": "user", "content": _draft_task(name, description, rules, spec)}]
            for attempt in range(1, MAX_DRAFT_ATTEMPTS + 1):
                attempts = attempt
                job.add(f"attempt {attempt}: asking Claude to write the scenario")
                reply = client.messages.create(model=draft_model(), max_tokens=DRAFT_TOKENS, system=_draft_system(), messages=messages)
                text = "".join(block.text for block in reply.content if block.type == "text")
                tokens["input"] += reply.usage.input_tokens
                tokens["output"] += reply.usage.output_tokens
                files, error = _check_draft(text)
                if error is None:
                    job.add(f"attempt {attempt}: valid. LiteTraffic accepted the scenario ({tokens['input']} tokens in, {tokens['output']} out)")
                    break
                job.add(f"attempt {attempt}: rejected: {error.splitlines()[0][:300]}")
                messages += [{"role": "assistant", "content": text}, {"role": "user", "content": f"LiteTraffic rejected that bundle:\n{error}\nFix it and reply with the same two fenced blocks."}]
        except Exception as exc:  # noqa: BLE001 - a draft job reports any failure, it never crashes the server
            error = f"drafting failed: {type(exc).__name__}: {exc}"
            job.add(error)
        job.result = {"valid": error is None, "files": files, "attempts": attempts, "tokens": tokens, "error": error, "name": name}
        self._finish(job, "finished", None)


def _last_json(text: str) -> object:
    for line in reversed(text.strip().splitlines()):
        try:
            return json.loads(line)
        except ValueError:
            continue
    return None


def _signal(process: subprocess.Popen | None, sig: int) -> None:
    if process is None or process.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(process.pid), sig)
    except (ProcessLookupError, PermissionError):
        pass


def _stop(job: Job) -> None:
    """Ctrl-C the run's whole process group so LiteTraffic cancels cleanly (fixture teardown, scrubbed artifacts); escalate if it hangs."""
    process = job.process
    _signal(process, signal.SIGINT)
    for sig in (signal.SIGTERM, signal.SIGKILL):
        deadline = time.time() + CANCEL_GRACE_SECONDS
        while process.poll() is None and time.time() < deadline:
            time.sleep(0.1)
        _signal(process, sig)


def _fetch_spec(url: object) -> str:
    try:
        checked = validate_target(url if isinstance(url, str) else "", "spec_url")
    except ValueError as exc:
        raise ConsoleError(422, str(exc)) from None
    try:
        with httpx.stream("GET", checked, timeout=15, follow_redirects=False) as response:
            if response.status_code != 200:
                raise ConsoleError(422, f"could not fetch the API spec: HTTP {response.status_code}")
            data = b""
            for chunk in response.iter_bytes():
                data += chunk
                if len(data) > MAX_SPEC_BYTES:
                    raise ConsoleError(422, f"the API spec is too large (limit {MAX_SPEC_BYTES} bytes): paste only the part the test needs")
    except httpx.HTTPError as exc:
        raise ConsoleError(422, f"could not fetch the API spec: {exc}") from None
    return data.decode("utf-8", errors="replace")


def _draft_system() -> str:
    return (
        "You write LiteTraffic scenario bundles: a manifest.json and a k6 journeys.js. Reply with exactly two fenced blocks, "
        "```json (the manifest) and ```javascript (journeys.js), and nothing else.\n\n"
        "HARD RULES\n"
        "- Only these two files exist. Do NOT use fixtures.command (no setup scripts). Let journeys create their own data through the app's public API, "
        "or use fixtures.owned_http when the app has create/delete endpoints.\n"
        "- Judge each rule by the business effect a real customer would experience (what was recorded, charged, sold, exposed), not by an internal counter "
        "that merely looks consistent. When the app offers a read endpoint for the authoritative effect, add a final observation (GET only) that checks it, "
        "in addition to checking the responses.\n"
        "- Before writing any code, plan each rule. Open journeys.js with a comment block that numbers every rule the owner gave and states (1) the real "
        "business effect that would prove it holds or is broken, and (2) how that effect is observed: a journey assertion on a response, a final observation "
        "reading the authoritative state, or a stage_assertions count of journeys that reached an outcome. A counter the app keeps about itself (a stock "
        "ledger, an allocation field) is not the effect customers experienced: observe the outcome instead (orders paid, rows stored, data returned), and if the "
        "given API cannot show it, say so in that block rather than checking a proxy.\n"
        "- Next to every check in journeys.js, name what it proves with a short comment of the form "
        "`// rule N, effect checked: <the business effect>`.\n"
        "- Secrets are never written into the files: refer to environment variables by name (secret_env, headers_env, __ENV.NAME).\n"
        "- Keep the load small and the budgets honest; this runs against a real app.\n\n"
        "LITETRAFFIC SCENARIO DOCUMENTATION\n" + GUIDE.read_text(encoding="utf-8")
    )


def _draft_task(name: str, description: str, rules: str, spec: str) -> str:
    return (
        f"Write a scenario named {name!r}.\n\nTHE APP (from its owner):\n{description or '(not described)'}\n\n"
        f"RULES THAT MUST HOLD (plain language, from its owner):\n{rules}\n\n"
        f"The target base URL is available to journeys as __ENV.LT_TARGET.\n\nAPI SPEC OR NOTES:\n{spec or '(none given: use only what the owner described)'}\n\n"
        "Start by listing, for each rule, the real business effect that would prove it and how it is observed (as the opening comment of journeys.js), "
        "then write the code. Reply with only the two fenced blocks.\n"
    )


def _check_draft(text: str) -> tuple[dict[str, str], str | None]:
    manifest = re.search(r"```json\n(.*?)```", text, re.S)
    script = re.search(r"```(?:javascript|js)\n(.*?)```", text, re.S)
    if not (manifest and script):
        return {}, "reply did not contain one ```json block and one ```javascript block"
    files = {"manifest.json": manifest.group(1), "journeys.js": script.group(1)}
    if not re.search(r"//.*effect checked:", files["journeys.js"], re.I):
        return files, "journeys.js must name, in a comment per rule, the business effect each check proves: // rule N, effect checked: <effect>"
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp, "bundle")
        _write_bundle(root, files)
        try:
            bundle = load_scenario(root)
        except (ScenarioError, ValidationError, ValueError, OSError) as exc:
            return files, _message(exc)
    if bundle.manifest.fixtures.command:
        return files, "fixtures.command is not allowed in a drafted scenario (setup commands run on the user's machine): remove it and let journeys create their own data, or use fixtures.owned_http"
    return files, None
