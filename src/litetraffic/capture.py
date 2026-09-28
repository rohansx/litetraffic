"""Server-side capture for `verify --capture docker:NAMES`: container logs and resource samples for the run window.

Capture is evidence about the server, never about the verdict: every failure here becomes a problem string.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from litetraffic.auth import redact

NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
LOG_CAP_BYTES = 5 * 1024 * 1024
STATS_INTERVAL_SECONDS = 2.0
DOCKER_TIMEOUT_SECONDS = 10
STOP_WAIT_SECONDS = 3
TOP_SIGNATURES = 8
MAX_DISTINCT_SIGNATURES = 10_000
EXAMPLE_LIMIT = 300
ERROR_LINE = re.compile(r"(?i:\b(?:error|critical|fatal|panic|timed out|refused)\b)|Traceback|Exception| 5\d\d(?: |$)")
_NORMALISE = [
    (re.compile(r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\''), "<str>"),
    (re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?"), "<ts>"),
    (re.compile(r"\b\d{2}:\d{2}:\d{2}(?:[.,]\d+)?\b"), "<ts>"),
    (re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"), "<uuid>"),
    (re.compile(r"\b0x[0-9a-fA-F]+\b|\b(?=[0-9a-f]*\d)[0-9a-f]{8,}\b"), "<hex>"),
    (re.compile(r"\b\d{4,}"), "<n>"),  # also 5000ms
    (re.compile(r"\s+"), " "),
]
_MIB = {"b": 1 / 2**20, "kib": 1 / 2**10, "mib": 1, "gib": 2**10, "tib": 2**20, "kb": 1e3 / 2**20, "mb": 1e6 / 2**20, "gb": 1e9 / 2**20, "tb": 1e12 / 2**20}


def parse_spec(value: str) -> list[str]:
    """`docker:api,worker` -> ["api", "worker"]; ValueError for anything else."""
    source, _, names = value.partition(":")
    if source != "docker" or not names:
        raise ValueError(f"--capture must look like docker:NAME[,NAME...], got {value!r}")
    containers = list(dict.fromkeys(name.strip() for name in names.split(",")))
    bad = [name for name in containers if not NAME.match(name)]
    if bad:
        raise ValueError(f"invalid container name(s) for --capture: {', '.join(map(repr, bad))}")
    return containers


def signature(line: str) -> str:
    for pattern, replacement in _NORMALISE:
        line = pattern.sub(replacement, line)
    return line.strip()[:EXAMPLE_LIMIT]


def _split_timestamp(line: str) -> tuple[str | None, str]:
    # `docker logs --timestamps` prefixes each line with an RFC 3339 time and one space.
    head, _, rest = line.partition(" ")
    return (head, rest) if head[:4].isdigit() and "T" in head else (None, line)


def _mib(text: str) -> float | None:
    match = re.fullmatch(r"\s*([\d.]+)\s*([A-Za-z]+)\s*", text)
    if not match or match.group(2).lower() not in _MIB:
        return None
    return round(float(match.group(1)) * _MIB[match.group(2).lower()], 3)


def parse_stats(line: str) -> dict | None:
    """One `docker stats --format '{{json .}}'` line -> {name, cpu_percent, mem_mb}, or None if unreadable."""
    try:
        raw = json.loads(line)
        cpu = float(str(raw["CPUPerc"]).rstrip("%"))
    except (ValueError, KeyError, TypeError):
        return None
    # .Container echoes the NAME/ID the user passed (what samples are keyed by); .Name is always the canonical name.
    return {"name": raw.get("Container") or raw.get("Name"), "cpu_percent": cpu, "mem_mb": _mib(str(raw.get("MemUsage", "")).split("/")[0])}


class _Log:
    """Drains one `docker logs -f` into a capped file and counts lines, error lines and error signatures."""

    def __init__(self, name: str, path: Path, secrets: list[str] = ()) -> None:
        self.name, self.path, self.secrets = name, path, secrets
        self.lines = self.error_lines = self.written = 0
        self.truncated = False
        self.signatures: Counter[str] = Counter()
        self.first: dict[str, tuple[str | None, str]] = {}
        self.process: subprocess.Popen[bytes] | None = None
        self.thread: threading.Thread | None = None

    def drain(self) -> None:
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(fd, "wb") as out, self.process.stdout as stream:
            for chunk in stream:
                if self.written + len(chunk) <= LOG_CAP_BYTES and not self.truncated:
                    out.write(chunk)
                    self.written += len(chunk)
                else:
                    self.truncated = True  # keep reading so docker never blocks on a full pipe
                self._count(chunk.decode("utf-8", "replace").rstrip("\n"))

    def _count(self, line: str) -> None:
        self.lines += 1
        at, message = _split_timestamp(line)
        # Redact before signature()/truncation: either can split a secret so the exact-match redaction later misses it.
        message = redact(message, self.secrets)
        if not ERROR_LINE.search(message):
            return
        self.error_lines += 1
        key = signature(message)
        if key not in self.signatures and len(self.signatures) >= MAX_DISTINCT_SIGNATURES:
            return  # ponytail: bounded memory; later new signatures go uncounted, only error_lines sees them
        self.signatures[key] += 1
        self.first.setdefault(key, (at, message[:EXAMPLE_LIMIT]))

    def summary(self) -> dict:
        return {
            "log_lines": self.lines,
            "error_lines": self.error_lines,
            "truncated": self.truncated,
            "signatures": [
                {"signature": key, "count": count, "first_seen": self.first[key][0], "example": self.first[key][1]}
                for key, count in self.signatures.most_common(TOP_SIGNATURES)
            ],
        }


def _stop(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(STOP_WAIT_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


class Capture:
    """start() right before traffic, stop() once observations are done; stop() is safe to call again."""

    def __init__(self, containers: list[str], server_dir: Path, secrets: list[str] = ()) -> None:
        self.containers, self.server_dir, self.secrets = containers, server_dir, secrets
        self.problems: list[str] = []
        self.logs: list[_Log] = []
        self.samples: dict[str, list[dict]] = {}
        self.stopping = threading.Event()
        self.stats_thread: threading.Thread | None = None
        self.docker = shutil.which("docker")

    def _run(self, *args: str) -> subprocess.CompletedProcess[str] | None:
        try:
            return subprocess.run([self.docker, *args], capture_output=True, text=True, timeout=DOCKER_TIMEOUT_SECONDS, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            self._problem(f"docker {args[0]} failed: {exc}")
            return None

    def _problem(self, message: str) -> None:
        if message not in self.problems:
            self.problems.append(message)

    def start(self) -> None:
        if not self.docker:
            self._problem("docker not found on PATH")
            return
        try:
            self.server_dir.mkdir(mode=0o700)
        except OSError as exc:
            self._problem(f"cannot create {self.server_dir.name}/: {exc}")
            return
        since = datetime.now(UTC).isoformat()
        for name in self.containers:
            found = self._run("inspect", "--type", "container", "--format", "{{.Name}}", name)
            if found is None:
                continue
            if found.returncode:
                self._problem(f"{name}: {(found.stderr.strip().splitlines() or ['docker inspect failed'])[-1]}")
                continue
            log = _Log(name, self.server_dir / f"{name}.log", self.secrets)
            try:
                log.process = subprocess.Popen(
                    [self.docker, "logs", "-f", "--timestamps", "--since", since, name],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    start_new_session=os.name == "posix",  # a terminal Ctrl-C is the runner's to handle, not docker's
                )
            except OSError as exc:
                self._problem(f"{name}: cannot start docker logs: {exc}")
                continue
            log.thread = threading.Thread(target=log.drain, daemon=True)
            log.thread.start()
            self.logs.append(log)
            self.samples[name] = []
        if self.samples:
            self.stats_thread = threading.Thread(target=self._poll_stats, daemon=True)
            self.stats_thread.start()

    def _poll_stats(self) -> None:
        path = self.server_dir / "stats.jsonl"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            while True:
                self._sample(out)
                if self.stopping.wait(STATS_INTERVAL_SECONDS):
                    return

    def _sample(self, out) -> None:
        result = self._run("stats", "--no-stream", "--format", "{{json .}}", *self.samples)
        if result is None:
            return
        if result.returncode:
            self._problem(f"docker stats: {(result.stderr.strip().splitlines() or ['failed'])[-1]}")
            return
        at = datetime.now(UTC).isoformat()
        for line in result.stdout.splitlines():
            sample = parse_stats(line)
            if sample and sample["name"] in self.samples:
                self.samples[sample["name"]].append({"at": at, **sample})
                out.write(json.dumps({"at": at, **json.loads(line)}, sort_keys=True) + "\n")
        out.flush()

    def stop(self) -> dict:
        self.stopping.set()
        for log in self.logs:
            _stop(log.process)
        for log in self.logs:
            log.thread.join()
            if log.process.returncode not in (0, None) and not self._stopped_by_us(log.process.returncode):
                self._problem(f"{log.name}: docker logs exited with status {log.process.returncode}")
        if self.stats_thread:
            self.stats_thread.join()
        for name, samples in self.samples.items():
            if not samples:
                self._problem(f"{name}: no resource samples from docker stats")
        return self.summary()

    @staticmethod
    def _stopped_by_us(returncode: int) -> bool:
        return returncode < 0 or returncode in (137, 143)  # SIGKILL/SIGTERM, raw or as a shell-style status

    def summary(self) -> dict:
        containers = {log.name: {**_resources(self.samples.get(log.name, [])), **log.summary()} for log in self.logs}
        return {"schema_version": 1, "source": "docker", "containers": containers, "problems": list(self.problems)}


def _resources(samples: list[dict]) -> dict:
    if not samples:
        return {key: None for key in ("baseline_cpu_percent", "peak_cpu_percent", "peak_at", "baseline_mem_mb", "peak_mem_mb")}
    peak = max(samples, key=lambda sample: sample["cpu_percent"])
    memory = [sample["mem_mb"] for sample in samples if sample["mem_mb"] is not None]
    return {
        "baseline_cpu_percent": samples[0]["cpu_percent"],
        "peak_cpu_percent": peak["cpu_percent"],
        "peak_at": peak["at"],
        "baseline_mem_mb": samples[0]["mem_mb"],
        "peak_mem_mb": max(memory) if memory else None,
    }
