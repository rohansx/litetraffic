"""`litetraffic report issue`: a scrubbed, maintainer-facing issue draft built from a failing run's artifacts."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shlex
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlsplit

from pydantic import ValidationError

from litetraffic import __version__
from litetraffic.artifacts import MANIFEST
from litetraffic.auth import MIN_SECRET_LENGTH, REDACTED, redact, secret_env_names, secret_values
from litetraffic.models import ScenarioManifest
from litetraffic.runs import read_json, resolve

BUNDLE = "issue"
# The files whose hashes tie a report to what the run recorded; any of them present must match artifacts.json.
SOURCE_FILES = ("run.json", "result.json", "observation.json", "scenario.lock.json", "events/000001.jsonl", "server.json")
CHANGED = "run artifacts changed after the run; refusing to report"
SECURITY_ASSERTIONS = frozenset(
    {"cross_tenant_read_blocked", "cross_tenant_write_blocked", "victim_unchanged", "unauthenticated_rejected", "no_foreign_data_in_own_responses"}
)
# Matched against the normalised id (camelCase split, lower-cased, '-', '_' and spaces removed), so variants fail closed.
SECURITY_ID_RE = re.compile(
    r"(tenant|isolat|authz|authori[sz]|authn|authenticat|permission|privilege|idor|bola|bfla|cross|foreign|leak|secret|marker|unauth"
    r"|token|session|victim|bypass|access|acl|rbac|abac|owner|csrf|xsrf|ssrf|xss|inject|credential|password|otheruser|otheraccount"
    r"|otherorg|hidden|forbid|denied|blocked|escalat|impersonat|scope)"
)
NOT_SECURITY_IDS = frozenset({"ownaccess"})  # the kit's own-resource control check: failing alone, it is a correctness bug
KIT_OBSERVER = "tenant-isolation-kit"
OVERLOADED = (429, 503)
DEFAULT_PORTS = {"http": 80, "https": 443}


class IssueError(ValueError):
    """The report cannot be drafted or filed; the CLI prints the message and exits 3."""


@dataclass
class RunEvidence:
    run_dir: Path
    runs_dir: Path
    run: dict
    result: dict
    observation: dict | None
    lock: dict
    manifest: ScenarioManifest | None
    source_sha256: dict[str, str]
    requests: list[dict] = field(default_factory=list)  # tags of each http_reqs point in metrics.jsonl
    events: list[dict] = field(default_factory=list)  # every stage and assertion event in the checked events/*.jsonl, in order
    server: dict | None = None  # server.json from --capture docker, when the run had it
    runs_dir_given: bool = True  # --runs-dir was passed (so the ledger lives there even for a run outside it)

    @property
    def run_id(self) -> str:
        return str(self.run.get("run_id") or self.run_dir.name)

    @property
    def manifest_data(self) -> dict:
        manifest = self.lock.get("manifest")
        return manifest if isinstance(manifest, dict) else {}


@dataclass(frozen=True)
class Classification:
    kind: str  # "security" or "correctness"
    reasons: list[str]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _check_integrity(run_dir: Path) -> tuple[dict[str, str], set[str]]:
    """sha256 of every source file, and every listed name, after checking each listed file against artifacts.json."""
    manifest = read_json(run_dir / MANIFEST)
    files = manifest.get("files") if manifest else None
    if not isinstance(files, list):
        raise IssueError(CHANGED)
    listed = {entry.get("path"): entry.get("sha256") for entry in files if isinstance(entry, dict)}
    for name, digest in listed.items():
        if not isinstance(name, str) or name.split("/", 1)[0] == BUNDLE:
            continue
        path = run_dir / name
        if not path.is_file() or path.is_symlink() or _sha256(path) != digest:
            raise IssueError(CHANGED)
    present = [name for name in SOURCE_FILES if (run_dir / name).is_file()]
    if any(name not in listed for name in present):
        raise IssueError(CHANGED)
    return {name: listed[name] for name in present}, {name for name in listed if isinstance(name, str)}


def _http_requests(path: Path) -> list[dict]:
    requests = []
    if not path.is_file() or path.is_symlink():
        return requests
    with path.open(encoding="utf-8", errors="replace") as lines:
        for line in lines:
            try:
                point = json.loads(line)
            except ValueError:
                continue
            if isinstance(point, dict) and point.get("metric") == "http_reqs" and point.get("type") == "Point":
                tags = (point.get("data") or {}).get("tags")
                if isinstance(tags, dict):
                    requests.append(tags)
    return requests


def _events(run_dir: Path, listed: set[str]) -> list[dict]:
    """Stage and assertion events from the checked events/*.jsonl files, ordered by sequence."""
    found = []
    for name in sorted(n for n in listed if n.startswith("events/") and n.endswith(".jsonl") and n.count("/") == 1):
        with (run_dir / name).open(encoding="utf-8", errors="replace") as lines:
            for line in lines:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if isinstance(event, dict) and event.get("type") in ("stage", "assertion"):
                    found.append(event)
    return sorted(found, key=lambda e: e.get("sequence") if isinstance(e.get("sequence"), int) else 0)


def load_run(ref: str, runs_dir: Path) -> RunEvidence:
    run_dir = resolve(ref, runs_dir)
    run, result = read_json(run_dir / "run.json"), read_json(run_dir / "result.json")
    if run is None or result is None:
        raise IssueError(f"{run_dir} has no readable run.json and result.json")
    sources, listed = _check_integrity(run_dir)
    lock = read_json(run_dir / "scenario.lock.json") or {}
    try:
        manifest = ScenarioManifest.model_validate(lock["manifest"]) if isinstance(lock.get("manifest"), dict) else None
    except ValidationError:
        manifest = None
    return RunEvidence(
        run_dir=run_dir,
        runs_dir=Path(runs_dir),
        run=run,
        result=result,
        observation=read_json(run_dir / "observation.json"),
        lock=lock,
        manifest=manifest,
        source_sha256=sources,
        requests=_http_requests(run_dir / "metrics.jsonl"),
        events=_events(run_dir, listed),
        server=read_json(run_dir / "server.json") if "server.json" in sources else None,
    )


def gate(evidence: RunEvidence) -> str:
    """"pass" for a passing run, "file" for a failing one; any other verdict is refused."""
    verdict = evidence.result.get("verdict")
    if verdict == "pass":
        return "pass"
    if verdict == "fail":
        return "file"
    shown = verdict if isinstance(verdict, str) else "unreadable"
    raise IssueError(f"verdict is {shown}: missing evidence is not a bug report; rerun until it passes or fails")


def failing(evidence: RunEvidence) -> list[dict]:
    rows = evidence.result.get("assertions") or []
    return [row for row in rows if isinstance(row, dict) and row.get("status") == "fail" and isinstance(row.get("id"), str)]


def _samples(assertion: dict) -> list[dict]:
    """Each recorded failure, plus the per-request samples a kit failure nests under actual.samples."""
    found = []
    for failure in assertion.get("failures") or []:
        if not isinstance(failure, dict):
            continue
        found.append(failure)
        actual = failure.get("actual")
        if isinstance(actual, dict):
            found.append(actual)
            found.extend(sample for sample in actual.get("samples") or [] if isinstance(sample, dict))
    return found


def submit_blockers(evidence: RunEvidence) -> list[str]:
    result, blockers = evidence.result, []
    if result.get("completeness") != "complete":
        blockers.append(f"the run's evidence is {result.get('completeness') or 'not marked'}, not complete")
    blockers.extend(f"limitation: {item}" for item in result.get("limitations") or [])
    if any(sample.get("status") in OVERLOADED for row in failing(evidence) for sample in _samples(row)):
        blockers.append("the target was rate-limiting or overloaded (HTTP 429/503); this FAIL may come from the traffic, not a bug")
    return blockers


def normalise_id(name: object) -> str:
    """`victimUnchanged`, `victim-unchanged` and `Victim Unchanged` all become `victimunchanged`."""
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(name))
    return re.sub(r"[-_\s]+", "", text.lower())


SECURITY_IDS = frozenset(normalise_id(name) for name in SECURITY_ASSERTIONS)


def _kit_run(manifest: dict) -> list[str]:
    reasons = []
    observer = normalise_id(manifest.get("observer") or "")
    if "isolation" in observer or "kit" in observer:
        reasons.append(f"the scenario's observer is {manifest.get('observer')}")
    if any(isinstance(j, dict) and "isolation" in normalise_id(j.get("name") or "") for j in manifest.get("journeys") or []):
        reasons.append("the scenario has a tenant-isolation journey")
    if any(isinstance(a, dict) and "kit" in normalise_id(a.get("auth_recipe") or "") for a in manifest.get("actors") or []):
        reasons.append("the scenario's actors use kit auth recipes")
    return reasons


def _kit_shaped(sample: dict) -> bool:
    """A per-request sample as the isolation kit records it: who sent it and what status came back."""
    return "identity" in sample and "status" in sample


def classify(evidence: RunEvidence, force_security: bool) -> Classification:
    """Fail closed: any security signal makes the failure security-class, and nothing downgrades it."""
    rows, reasons = failing(evidence), []
    for row in rows:
        name = normalise_id(row["id"])
        if name in NOT_SECURITY_IDS:
            continue
        if name in SECURITY_IDS:
            reasons.append(f"failing assertion {_short(row['id'], 100)} is an isolation check")
        elif SECURITY_ID_RE.search(name):
            reasons.append(f"failing assertion {_short(row['id'], 100)} is named like a security check")
        if any(_kit_shaped(sample) for sample in _samples(row)):
            reasons.append(f"failing assertion {_short(row['id'], 100)} recorded per-identity request samples")
    ids = {normalise_id(row["id"]) for row in rows}
    if ids and not ids <= NOT_SECURITY_IDS:
        reasons.extend(_kit_run(evidence.manifest_data))
    if any("markers" in sample for row in rows for sample in _samples(row)):
        reasons.append("a failing sample carries another identity's markers")
    declared = evidence.manifest_data.get("report")
    if isinstance(declared, dict) and declared.get("security") is True:
        reasons.append("the scenario declares report.security")
    if force_security:
        reasons.append("--security was passed")
    return Classification("security" if reasons else "correctness", reasons)


def normalize_origin(url: str) -> str:
    parts = urlsplit(str(url).strip())
    scheme, host = parts.scheme.lower(), (parts.hostname or "").lower()
    try:
        port = parts.port
    except ValueError:
        port = None
    if port is not None and port != DEFAULT_PORTS.get(scheme):
        return f"{scheme}://{host}:{port}"
    return f"{scheme}://{host}"


def origin_hash(url: str) -> str:
    return hashlib.sha256(normalize_origin(url).encode()).hexdigest()


def fingerprint(evidence: RunEvidence) -> str:
    ids = ",".join(sorted(row["id"] for row in failing(evidence)))
    key = f"{evidence.run.get('scenario')}\0{ids}\0{origin_hash(evidence.run.get('target') or '')}"
    return "lt" + hashlib.sha256(key.encode()).hexdigest()[:12]


JWT_RE = re.compile(r"eyJ[\w-]{8,}\.[\w-]{8,}\.[\w-]*")
GH_TOKEN_RE = re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}|github_pat_\w{20,}")
TOKEN_LIKE = re.compile(r"\S{12,}")
IDENTIFIER_LIKE = re.compile(r"^[A-Za-z_]+$|^[A-Za-z]+(?:[-_][A-Za-z]+)*$")


def _marker_like(text: str) -> bool:
    """A string that looks like a planted marker value: long, unbroken, with a digit, and not an identifier."""
    return bool(TOKEN_LIKE.fullmatch(text)) and any(char.isdigit() for char in text) and not IDENTIFIER_LIKE.match(text)
# An opaque credential after an auth scheme; a ${ENV} reference or an already-redacted value is not one.
BEARER_RE = re.compile(r"\b(Bearer|Basic)(\s+)(?![$\[])([A-Za-z0-9._~+/=-]{16,})", re.IGNORECASE)
MIN_HOST_LENGTH = 4


def parse_kit(script: str) -> dict | None:
    """The KIT object `build_script` embeds as JSON in a generated journeys.js, or None."""
    start = script.find("const KIT = ")
    if start < 0:
        return None
    try:
        kit, _ = json.JSONDecoder().raw_decode(script, start + len("const KIT = "))
    except ValueError:
        return None
    return kit if isinstance(kit, dict) else None


def _strings(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for item in value.values() for text in _strings(item)]
    if isinstance(value, list):
        return [text for item in value for text in _strings(item)]
    return []


def _spellings(text: str) -> set[str]:
    """`text` raw, JSON-escaped and URL-encoded (either hex case)."""
    encoded = quote(text, safe="")
    return {text, json.dumps(text)[1:-1], encoded, re.sub(r"%[0-9A-F]{2}", lambda m: m.group(0).lower(), encoded)}


def _secret_spellings(secret: str) -> list[str]:
    """A secret as `_spellings` gives it, plus base64 and base64url (padded or not); longest first."""
    data = secret.encode()
    forms = _spellings(secret)
    for encoded in (base64.b64encode(data).decode(), base64.urlsafe_b64encode(data).decode()):
        forms |= {encoded, encoded.rstrip("=")}
    return sorted(forms, key=len, reverse=True)


class Scrubber:
    """Every byte the bundle (and the CLI output) carries goes through `text`; `verify` re-reads what was written."""

    def __init__(self, evidence: RunEvidence, kit: dict | None = None, security: bool = False) -> None:
        self.secrets = secret_values(evidence.manifest, os.environ, {}) if evidence.manifest else []
        self.secret_forms = sorted({form for secret in self.secrets if len(secret) >= MIN_SECRET_LENGTH
                                    for form in _secret_spellings(secret)}, key=len, reverse=True)
        self.security = security
        markers: dict[str, str] = {}
        for identity in (kit or {}).get("identities") or []:
            for index, marker in enumerate(identity.get("markers") or []):
                if isinstance(marker, str) and marker:
                    markers[marker] = f"[marker:{identity.get('name')}#{index}]"
        if security and not markers:
            # Without the kit's declared markers, a failing assertion whose samples carry a `markers` key (a marker leak
            # was seen) may name the marker values in `expected`; only its high-entropy strings are treated as markers,
            # never plain words, ids or status values such as COMPLETED (scrubbing those would break the repro's check).
            marked = [row for row in failing(evidence) if any("markers" in sample for sample in _samples(row))]
            expected = [text for row in marked for text in _strings(row.get("expected"))]
            for index, text in enumerate(dict.fromkeys(t for t in expected if _marker_like(t))):
                markers.setdefault(text, f"[marker:expected#{index}]")
        self.markers = dict(sorted(markers.items(), key=lambda item: len(item[0]), reverse=True))
        target = str(evidence.run.get("target") or "")
        origins = {target.rstrip("/"), normalize_origin(target)} if target else set()
        self.origins = sorted({form for origin in origins if "://" in origin for form in _spellings(origin)}, key=len, reverse=True)
        parts = urlsplit(target)
        hosts = {parts.netloc, parts.hostname or "", normalize_origin(target).split("://", 1)[-1]} if target else set()
        self.hosts = sorted({form for host in hosts if len(host) >= MIN_HOST_LENGTH for form in _spellings(host)}, key=len, reverse=True)
        home = os.environ.get("HOME")
        paths = [(evidence.run_dir, "<run>"), (evidence.runs_dir, "<runs>")] + ([(Path(home), "~")] if home else [])
        forms = {}
        for path, label in paths:
            for form in (str(path), str(Path(path).resolve()), str(Path(path).absolute())):
                if len(form) > 1:
                    forms.setdefault(form, label)
        self.paths = sorted(forms.items(), key=lambda item: len(item[0]), reverse=True)

    def text(self, value: str) -> str:
        value = redact(value, self.secrets)
        for form in self.secret_forms:
            value = value.replace(form, REDACTED)
        value = GH_TOKEN_RE.sub(REDACTED, JWT_RE.sub(REDACTED, value))
        value = BEARER_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", value)
        for form in self.origins:
            value = re.sub(re.escape(form), "$TARGET", value, flags=re.IGNORECASE)
        for form in self.hosts:
            value = re.sub(re.escape(form), "$TARGET_HOST", value, flags=re.IGNORECASE)
        for marker, label in self.markers.items():
            for form in _spellings(marker):
                value = value.replace(form, label)
        for path, label in self.paths:
            value = value.replace(path, label)
        return value

    def value(self, data):
        """A copy of decoded JSON with every string, keys included, scrubbed."""
        if isinstance(data, str):
            return self.text(data)
        if isinstance(data, dict):
            return {self.text(key): self.value(item) for key, item in data.items()}
        if isinstance(data, list):
            return [self.value(item) for item in data]
        return data

    def leak(self, text: str) -> bool:
        if any(form in text for form in self.secret_forms):
            return True
        if JWT_RE.search(text) or GH_TOKEN_RE.search(text) or BEARER_RE.search(text):
            return True
        return self.security and any(marker in text for marker in self.markers)

    def verify(self, bundle: Path) -> None:
        """Re-read every bundle file; on any leak delete the whole bundle and refuse."""
        for path in sorted(bundle.rglob("*")):
            if path.is_file() and self.leak(path.read_bytes().decode("utf-8", errors="replace")):
                name = path.relative_to(bundle).as_posix()
                shutil.rmtree(bundle, ignore_errors=True)
                raise IssueError(f"redaction check failed: {name}")


MATCHER_SYMBOLS = {"eq": "=", "gte": "≥", "lte": "≤"}
ID_SEGMENT = re.compile(r"^(\d+|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$")
MAX_SAMPLES = 3
TITLE_LIMIT = 100
ACTIONS_NOTE = "requires GitHub sign-in; expires with artifact retention"


CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]")
SAFE_URL = re.compile(r"^https://[A-Za-z0-9.-]+(?::\d+)?(?:/[A-Za-z0-9._~/?#=&%+:-]*)?$")


def _short(value: object, limit: int = 80) -> str:
    """One line of plain text: control characters and newlines collapse to single spaces, then the text is clipped."""
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True, ensure_ascii=False)
    text = " ".join(CONTROL.sub(" ", text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _code(value: object, limit: int = 80) -> str:
    """A run-derived value as an inline code span, inert on GitHub: no headings, mentions, links, HTML or comments.

    The delimiter is one backtick longer than the longest run inside, so the value cannot close the span.
    """
    text = _short(value, limit)
    if not text:
        return "(empty)"
    fence = "`" * (max((len(run) for run in re.findall(r"`+", text)), default=0) + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{fence}{pad}{text}{pad}{fence}"


def _num(value: object) -> str:
    """A count or rate as plain digits; anything else is shown as code."""
    return str(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else _code(value, 40)


def _link(url: object) -> str:
    """A URL as an autolink when it is a plain https URL, else as inert code."""
    return f"<{url}>" if isinstance(url, str) and SAFE_URL.match(url) else _code(url, 200)


def _fence(text: str, lang: str = "") -> list[str]:
    """`text` in a fenced block whose fence is longer than any backtick run inside, so the text cannot close it."""
    fence = "`" * max(3, max((len(run) for run in re.findall(r"`+", text)), default=0) + 1)
    return [fence + lang, text, fence]


def describe_matcher(matcher: object) -> str:
    if isinstance(matcher, dict) and len(matcher) == 1:
        (key, value), = matcher.items()
        if key in MATCHER_SYMBOLS:
            return f"{MATCHER_SYMBOLS[key]} {_short(value)}"
        if key == "len":
            return f"length = {_short(value)}"
        if key == "exists":
            return "present" if value else "absent"
    return f"= {_short(matcher)}"


def _holds(matcher: object, actual: object) -> bool:
    if isinstance(matcher, dict) and len(matcher) == 1:
        (key, value), = matcher.items()
        try:
            if key == "eq":
                return actual == value
            if key == "gte":
                return actual >= value
            if key == "lte":
                return actual <= value
            if key == "len":
                return len(actual) == value
            if key == "exists":
                return (actual is not None) == bool(value)
        except TypeError:
            return False
    return actual == matcher


def checks(row: dict) -> list[dict]:
    """The failing pointer checks of a final-state assertion: [{pointer, expected, actual}]; empty for event assertions."""
    expected, actual = row.get("expected"), row.get("actual")
    if not isinstance(expected, dict) or not isinstance(actual, dict) or row.get("failures"):
        return []
    found = [
        {"pointer": pointer, "expected": matcher, "actual": actual.get(pointer)}
        for pointer, matcher in expected.items()
        if not _holds(matcher, actual.get(pointer))
    ]
    return found or [{"pointer": pointer, "expected": matcher, "actual": actual.get(pointer)} for pointer, matcher in expected.items()]


def _path_template(url: str) -> str:
    path = urlsplit(url).path or "/"
    return "/".join("{id}" if ID_SEGMENT.match(segment) else segment for segment in path.split("/"))


def _merge_paths(paths: list[str]) -> str:
    """One template for an operation's paths: segments that differ between requests become {id}."""
    split = [path.split("/") for path in paths]
    if len({len(parts) for parts in split}) != 1:
        return paths[0]
    return "/".join(column[0] if len(set(column)) == 1 else "{id}" for column in zip(*split))


def traffic(evidence: RunEvidence) -> list[dict]:
    """Per operation: method, path template, request count and status histogram, from metrics.jsonl."""
    operations: dict[str, dict] = {}
    for tags in evidence.requests:
        name = str(tags.get("operation") or tags.get("name") or "request")
        entry = operations.setdefault(name, {"operation": name, "method": tags.get("method"), "paths": [], "requests": 0, "statuses": {}})
        entry["requests"] += 1
        entry["paths"].append(_path_template(str(tags.get("url") or "")))
        status = str(tags.get("status") or "?")
        entry["statuses"][status] = entry["statuses"].get(status, 0) + 1
    return [
        {"operation": e["operation"], "method": e["method"], "path": _merge_paths(e["paths"]), "requests": e["requests"],
         "statuses": dict(sorted(e["statuses"].items()))}
        for e in operations.values()
    ]


MAX_JOURNEY_ROWS, MAX_SAMPLE_COLUMNS = 20, 8


def _scalar(value: object) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def journey_samples(evidence: RunEvidence) -> dict:
    """One row per journey from its assertion events, passing ones included: their actuals are often the clearest evidence.

    Columns are the scalar fields of each assertion's `actual` (a key two assertions share is prefixed by the assertion).
    """
    prefix = f"{evidence.run_id}-"
    journeys: dict[str, dict] = {}
    columns: dict[tuple[str, str], None] = {}
    for event in evidence.events:
        key = event.get("logical_key")
        if not isinstance(key, str):
            continue
        row = journeys.setdefault(key, {"journey": key.removeprefix(prefix), "stages": [], "values": {}, "failed": []})
        if event.get("type") == "stage" and isinstance(event.get("stage"), str):
            row["stages"].append(event["stage"])
        elif event.get("type") == "assertion" and isinstance(event.get("assertion"), str):
            name, actual = event["assertion"], event.get("actual")
            if event.get("passed") is False:
                row["failed"].append(name)
            for field_name, value in (actual.items() if isinstance(actual, dict) else []):
                if _scalar(value):
                    columns.setdefault((name, str(field_name)), None)
                    row["values"][f"{name}.{field_name}"] = value
    chosen = list(columns)[:MAX_SAMPLE_COLUMNS]
    names = [field_name for _, field_name in chosen]
    labels = [field_name if names.count(field_name) == 1 else f"{name}.{field_name}" for name, field_name in chosen]
    rows = [{"journey": row["journey"], "stages": row["stages"], "failed": row["failed"],
             "values": [row["values"].get(f"{name}.{field_name}") for name, field_name in chosen]}
            for row in journeys.values() if row["values"] or row["failed"]]
    return {"columns": labels, "rows": rows[:MAX_JOURNEY_ROWS], "total": len(rows)}


def mechanism(evidence: RunEvidence) -> list[str]:
    """What the evidence shows about how the journeys ran: the stage funnel, event order and peak in-flight requests.

    Nothing here says "concurrent": overlap is stated only as the measured peak.
    """
    lines = []
    rates = [phase.get("rate") for phase in evidence.run.get("resolved_schedule") or [] if isinstance(phase, dict)]
    rate = f" at {'/'.join(_num(r) for r in rates)} per second" if rates else ""
    for name, journey in sorted((evidence.result.get("journeys") or {}).items()):
        if not isinstance(journey, dict):
            continue
        started = journey.get("started")
        stages = [stage for stage in journey.get("stages") or [] if isinstance(stage, dict)]
        funnel = ", ".join(f"{_code(stage.get('name'), 40)} {_num(stage.get('reached'))}/{_num(started)}" for stage in stages)
        line = f"The run started {_num(started)} {_code(name, 40)} journeys{rate}."
        lines.append(line + (f" Stages reached: {funnel}." if funnel else ""))
        lines += _stage_order([stage.get("name") for stage in stages], evidence.events)
    overlap = {name: count for name, count in ((evidence.result.get("metrics") or {}).get("overlap") or {}).items()
               if isinstance(count, (int, float)) and not isinstance(count, bool)}
    if not overlap:
        lines.append("Request overlap was not measured.")
    elif max(overlap.values()) > 1:
        detail = ", ".join(f"{_code(name, 40)} {_num(count)}" for name, count in sorted(overlap.items()))
        lines.append(f"Requests overlapped: at most {_num(max(overlap.values()))} were in flight at once ({detail}).")
    else:
        lines.append("Requests did not overlap: at most 1 was in flight at once.")
    return lines


def _stage_order(stages: list, events: list[dict]) -> list[str]:
    """For consecutive stages A, B: whether every journey reached A before any reached B, in the recorded event order."""
    lines = []
    for first, second in zip(stages, stages[1:]):
        before = [e["sequence"] for e in events if e.get("type") == "stage" and e.get("stage") == first and isinstance(e.get("sequence"), int)]
        after = [e["sequence"] for e in events if e.get("type") == "stage" and e.get("stage") == second and isinstance(e.get("sequence"), int)]
        if len(before) > 1 and after and max(before) < min(after):
            lines.append(f"In the recorded event order, every journey reached {_code(first, 40)} before any reached {_code(second, 40)}.")
    return lines


def summary_line(evidence: RunEvidence, row: dict) -> str:
    found = checks(row)
    if found:
        first = found[0]
        return (f"{_code(row['id'])}: {_code(first['pointer'])} expected "
                f"{_code(describe_matcher(first['expected']))}, observed {_code(first['actual'])}.")
    failures = [f for f in row.get("failures") or [] if isinstance(f, dict)]
    line = f"{_num(row.get('failed', len(failures)))}/{_num(row.get('samples', '?'))} {_code(row['id'])} checks failed"
    if failures:
        line += f"; expected {_code(failures[0].get('expected'))}, got {_code(failures[0].get('actual'))}"
    return line + "."


def make_title(evidence: RunEvidence, override: str | None = None) -> str:
    if override:
        return _short(override, TITLE_LIMIT)
    rows = failing(evidence)
    row = rows[0] if rows else {"id": "unknown"}
    found = checks(row)
    if found:
        pointer = found[0]["pointer"].rsplit("/", 1)[-1] or found[0]["pointer"]
        detail = f"{pointer} {describe_matcher(found[0]['expected'])}, got {_short(found[0]['actual'], 20)}"
    else:
        detail = f"{row.get('failed', '?')}/{row.get('samples', '?')} checks"
    more = f" (+{len(rows) - 1} more)" if len(rows) > 1 else ""
    return _short(f"{evidence.run.get('scenario')}: {row['id']} failed ({detail}){more}", TITLE_LIMIT)


def attach_url(explicit: str | None) -> tuple[str | None, str | None]:
    """The evidence link and its caveat: the flag, else the GitHub Actions run when all three variables are set."""
    if explicit:
        if not SAFE_URL.match(explicit):
            raise IssueError(f"--attach-url must be a plain https:// URL, got {_short(explicit, 80)!r}")
        return explicit, None
    parts = [os.environ.get(name) for name in ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID")]
    if all(parts):
        url = f"{parts[0].rstrip('/')}/{parts[1]}/actions/runs/{parts[2]}"
        return (url, ACTIONS_NOTE) if SAFE_URL.match(url) else (None, None)
    return None, None


KNOWN_ID_RE = re.compile(r"\b(CVE-\d{4}-\d{4,}|GHSA(?:-[a-z0-9]{4}){3})\b", re.IGNORECASE)
NOT_RECORDED = "target version not recorded; pass --target-version (or run with --capture docker:NAME) before filing"


def captured_images(evidence: RunEvidence) -> dict[str, str]:
    """{container: image reference} from server.json; the tag is the closest thing to a version the run recorded."""
    containers = (evidence.server or {}).get("containers")
    if not isinstance(containers, dict):
        return {}
    return {str(name): _short(entry["image"], 200) for name, entry in sorted(containers.items())
            if isinstance(entry, dict) and isinstance(entry.get("image"), str) and entry["image"].strip()}


def _known_ids(text: str) -> list[str]:
    """CVE ids upper-cased, GHSA ids in GitHub's own spelling (GHSA- then lower case)."""
    ids = [match.group(0) for match in KNOWN_ID_RE.finditer(text)]
    return [i.upper() if i.upper().startswith("CVE-") else "GHSA-" + i[5:].lower() for i in ids]


def declared_known_issues(evidence: RunEvidence, scenario_dir: Path | None) -> list[str]:
    """What the scenario says it reproduces: manifest `report.known_issue`, then CVE/GHSA ids in the scenario's README."""
    found = []
    declared = evidence.manifest_data.get("report")
    known = declared.get("known_issue") if isinstance(declared, dict) else None
    if isinstance(known, str) and known.strip():
        found += _known_ids(known) or [_short(known, 120)]
    if scenario_dir is not None and Path(scenario_dir).is_dir():
        for path in sorted(Path(scenario_dir).iterdir()):
            if path.is_file() and not path.is_symlink() and path.name.lower().startswith("readme"):
                found += _known_ids(path.read_text(encoding="utf-8", errors="replace")[:200_000])
    return list(dict.fromkeys(found))


def known_issue_warning(ids: list[str]) -> str:
    return (f"the scenario says it reproduces {', '.join(ids)}; check the tracker and changelog first, since this may already "
            "be reported or fixed in a later version than the target")


def draft_warnings(target: dict, known: dict) -> list[str]:
    warnings = [] if target["version"] else [NOT_RECORDED]
    if known["declared"]:
        warnings.append(known_issue_warning(known["declared"]))
    return warnings


def evidence_document(evidence: RunEvidence, classification: Classification, fp: str, caveats: list[str], options: dict) -> dict:
    """evidence.json before scrubbing; the markdown bodies are rendered from this, so they cannot say more than it does."""
    run, result, metrics = evidence.run, evidence.result, evidence.result.get("metrics") or {}
    url, note = attach_url(options.get("attach_url"))
    images = captured_images(evidence)
    version = options.get("target_version") or ("; ".join(f"{name}: {image}" for name, image in images.items()) or None)
    target = {"origin": "$TARGET", "origin_sha256": origin_hash(run.get("target") or ""), "version": version, "images": images,
              "version_source": "reporter" if options.get("target_version") else ("container image" if images else None)}
    scenario_dir = options.get("scenario")
    known = {"declared": declared_known_issues(evidence, Path(scenario_dir) if scenario_dir else None),
             "reporter": options.get("known_issue")}
    return {
        "schema_version": 1,
        "litetraffic_version": __version__,
        "run": {
            "run_id": evidence.run_id, "scenario": run.get("scenario"), "scenario_sha256": run.get("scenario_sha256"), "seed": run.get("seed"),
            "engine": run.get("engine") or evidence.lock.get("engine"), "started_at": run.get("started_at"),
            "finished_at": run.get("finished_at"), "schedule": run.get("resolved_schedule") or [],
            "planned_journeys": result.get("planned_journeys"),
        },
        "target": target,
        "known_issue": known,
        "warnings": draft_warnings(target, known),
        "verdict": result.get("verdict"),
        "completeness": result.get("completeness"),
        "limitations": result.get("limitations") or [],
        "classification": {"kind": classification.kind, "reasons": classification.reasons},
        "fingerprint": fp,
        "title": make_title(evidence, options.get("title")),
        "summary": [summary_line(evidence, row) for row in failing(evidence)],
        "mechanism": mechanism(evidence),
        "journey_samples": journey_samples(evidence),
        "failing": [
            {"id": row["id"], "expected": row.get("expected"), "actual": row.get("actual"), "samples": row.get("samples"),
             "failed": row.get("failed"), "checks": checks(row), "failures": (row.get("failures") or [])[:MAX_SAMPLES]}
            for row in failing(evidence)
        ],
        "traffic": {"operations": traffic(evidence), "by_operation": metrics.get("by_operation") or {}, "overlap": metrics.get("overlap") or {}},
        "source_sha256": evidence.source_sha256,
        "caveats": caveats,
        "video": options.get("video") or {"status": "skipped"},
        "attach_url": url,
        "attach_note": note,
    }


def _cell(value: object) -> str:
    """A table cell: inert code, with '|' escaped because GitHub splits table rows on it even inside code spans."""
    return _code(value, 120).replace("|", "\\|")


def _check_rows(doc: dict) -> list[str]:
    rows = ["| Check | Expected | Actual |", "|---|---|---|"]
    for item in doc["failing"]:
        if item["checks"]:
            rows += [f"| {_cell(item['id'])} {_cell(check['pointer'])} | {_cell(describe_matcher(check['expected']))} "
                     f"| {_cell(check['actual'])} |" for check in item["checks"]]
        else:
            first = (item["failures"] or [{}])[0]
            first = first if isinstance(first, dict) else {}
            rows.append(f"| {_cell(item['id'])} ({_num(item.get('failed', '?'))}/{_num(item.get('samples', '?'))} failed) "
                        f"| {_cell(first.get('expected'))} | {_cell(first.get('actual'))} |")
    return rows


def _sample_rows(doc: dict) -> list[str]:
    """Recorded failures first, then one row per journey from its assertion events (passing ones included)."""
    rows = []
    samples = [(item["id"], failure) for item in doc["failing"] for failure in item["failures"]]
    if samples:
        rows += ["| Check | Sequence | Key | Expected | Actual | Detail |", "|---|---|---|---|---|---|"]
        for name, failure in samples:
            failure = failure if isinstance(failure, dict) else {}
            rows.append(f"| {_cell(name)} | {_cell(failure.get('sequence'))} | {_cell(failure.get('logical_key'))} "
                        f"| {_cell(failure.get('expected'))} | {_cell(failure.get('actual'))} "
                        f"| {_cell(failure['detail']) if failure.get('detail') else ''} |")
    journeys = doc.get("journey_samples") or {}
    if journeys.get("rows"):
        if rows:
            rows.append("")
        rows.append("What each journey's checks recorded (passing checks included):")
        rows.append("")
        columns = ["Journey", "Stages", *(_cell(label) for label in journeys["columns"]), "Failed checks"]
        rows += ["| " + " | ".join(columns) + " |", "|" + "---|" * len(columns)]
        for row in journeys["rows"]:
            values = [_cell(value) for value in row["values"]]
            stages = _cell(" > ".join(row["stages"])) if row["stages"] else ""
            failed = _cell(", ".join(row["failed"])) if row["failed"] else ""
            rows.append(f"| {_cell(row['journey'])} | {stages} | {' | '.join(values)}{' | ' if values else ''}{failed} |")
        if journeys.get("total", 0) > len(journeys["rows"]):
            rows.append(f"\nFirst {_num(len(journeys['rows']))} of {_num(journeys['total'])} journeys; the rest are in events/*.jsonl.")
    return rows or ["The run recorded no per-request or per-journey samples."]


def _ms(value: object) -> str:
    """A latency in whole milliseconds: three decimals of a p95 tell a maintainer nothing."""
    return str(round(value)) if isinstance(value, (int, float)) and not isinstance(value, bool) else ""


def _traffic_rows(doc: dict) -> list[str]:
    traffic_doc = doc["traffic"]
    if not traffic_doc["operations"]:
        return ["metrics.jsonl recorded no requests."]
    rows = ["| Operation | Request | Count | Statuses | p95 ms |", "|---|---|---|---|---|"]
    for op in traffic_doc["operations"]:
        statuses = ", ".join(f"{status}×{count}" for status, count in op["statuses"].items())
        p95 = _ms((traffic_doc["by_operation"].get(op["operation"]) or {}).get("p95"))
        request = f"{op['method']} $TARGET{op['path']}"
        rows.append(f"| {_cell(op['operation'])} | {_cell(request)} | {_num(op['requests'])} | {_cell(statuses)} | {p95} |")
    return rows


INLINE_REPRO_BYTES = 12 * 1024
ESTABLISHES = (
    "LiteTraffic drove the app's public HTTP API from outside and read the resulting state back through the same API. "
    "It did not inspect code, logs or the database, and it does not know the root cause. "
    "The result comes from one run unless stated otherwise."
)
CWE = {"cross_tenant": "CWE-639", "unauthenticated": "CWE-306", "leak": "CWE-200", "other": "CWE-285"}


def cwe_ids(doc: dict) -> list[str]:
    found = []
    for item in doc["failing"]:
        name = item["id"]
        if name.startswith("cross_tenant") or name == "victim_unchanged":
            found.append(CWE["cross_tenant"])
        elif name.startswith("unauth"):
            found.append(CWE["unauthenticated"])
        elif any(word in name for word in ("foreign", "leak", "marker", "secret")):
            found.append(CWE["leak"])
        elif item["id"] != "own_access":
            found.append(CWE["other"])
    return list(dict.fromkeys(found)) or [CWE["other"]]


def _environment(doc: dict) -> list[str]:
    run, target = doc["run"], doc["target"]
    version = _code(target["version"], 200) if target["version"] else "not recorded"
    return [
        f"- Target version: {version}",
        f"- Scenario: {_code(run['scenario'])}, seed {_num(run['seed'])}",
        f"- LiteTraffic: {_code(doc['litetraffic_version'])}",
        "", "<details><summary>Run metadata</summary>", "",
        f"- Target origin: `$TARGET` (sha256 of the normalized origin {_code(target['origin_sha256'])})",
        f"- Load engine: {_code(run['engine'], 120)}",
        f"- Run: {_code(run['run_id'])}, started {_code(run['started_at'])} (UTC)",
        f"- Scenario sha256: {_code(run['scenario_sha256'])}",
        "", f"litetraffic-fingerprint: {doc['fingerprint']}",
        "", "</details>",
    ]


def _steps(doc: dict, repro: str, env_names: list[str]) -> list[str]:
    names = " ".join(f"{name}=…" for name in env_names)
    exports = " and export " + ", ".join(_code(name) for name in env_names) if env_names else ""
    lines = [
        f"1. Set `TARGET` to the app's base URL{exports}.",
        f"2. Run {_code(f'TARGET=… {names} bash repro.sh', 400)}. Exit 1 means the failure reproduced; exit 0 means it did not; "
        "exit 2 means the script could not check (a missing variable, or a step that failed before the check).",
        "3. A failure can depend on timing, so one attempt may not reproduce it; rerun a few times. "
        f"The recorded run planned {_num(doc['run']['planned_journeys'])} journeys.",
    ]
    if len(repro.encode()) <= INLINE_REPRO_BYTES:
        lines += ["", "<details><summary>repro.sh</summary>", "", *_fence(repro.rstrip("\n"), "bash"), "", "</details>"]
    else:
        lines += ["", "repro.sh is too long to inline; it is in the reporter's bundle."]
    return lines


def _proof(doc: dict) -> list[str]:
    lines, video = [], doc["video"]
    if doc.get("embed_video"):
        lines.append(f"![LiteTraffic replay](./{doc['embed_video']})")
    elif video.get("status") == "rendered":
        lines.append("A replay video of the recorded evidence is in the reporter's bundle (issue/proof.mp4).")
    if doc["attach_url"]:
        note = f" ({doc['attach_note']})" if doc["attach_note"] else ""
        lines.append(f"- Evidence bundle: {_link(doc['attach_url'])}{note}")
    lines += ["", "<details><summary>Source artifacts (sha256)</summary>", ""]
    lines += [f"- {_code(name)}: {_code(digest)}" for name, digest in sorted(doc["source_sha256"].items())]
    return [*lines, "", "</details>"]


def _header(doc: dict) -> list[str]:
    """What a maintainer needs before the summary: which version was tested and whether this is already known."""
    target, known = doc["target"], doc["known_issue"]
    if target["version"]:
        lines = [f"Target version: {_code(target['version'], 200)}"]
    else:
        lines = ["**Target version: not recorded.** The reporter should add it (`--target-version`) before this is filed."]
    lines.append("")
    if known["reporter"]:
        lines.append(f"**Known/fixed?** {_link(known['reporter'])}")
    else:
        lines.append("**Known/fixed?** not checked by the reporter (`--known-issue URL` records an existing issue or advisory).")
    if known["declared"]:
        lines += ["", f"The scenario says it reproduces {', '.join(_code(item, 60) for item in known['declared'])}; "
                      "it may already be reported, or fixed in a later version than the one tested."]
    return [*lines, ""]


def _sections(doc: dict, repro: str, env_names: list[str]) -> list[str]:
    caveats = ["", "## Caveats", "", *(f"- {_code(item, 300)}" for item in doc["caveats"])] if doc["caveats"] else []
    return [
        "## Summary", "", *doc["summary"], "", *doc["mechanism"], "",
        *([f"This failure was reported before in {_link(doc['supersedes'])}, which is now closed; it is happening again.", ""]
          if doc.get("supersedes") else []),
        "## Expected vs actual", "", *_check_rows(doc), "",
        "## Samples", "", *_sample_rows(doc), "",
        "## Steps to reproduce", "", *_steps(doc, repro, env_names), "",
        "## Requests sent", "", *_traffic_rows(doc), "",
        "## Environment", "", *_environment(doc), "",
        "## Proof", "", *_proof(doc), "",
        "## What this does and does not establish", "", ESTABLISHES, *caveats, "",
    ]


def render_issue_md(doc: dict, repro: str, env_names: list[str]) -> str:
    return "\n".join([*_header(doc), *_sections(doc, repro, env_names)]) + "\n"


def render_security_md(doc: dict, repro: str, env_names: list[str], repo: str | None) -> str:
    fp, where = doc["fingerprint"], repo or "OWNER/NAME"
    impact = [f"- {_code(item['id'])} failed ({_num(item.get('failed') or '?')}/{_num(item.get('samples') or '?')} samples)"
              for item in doc["failing"]]
    lines = [
        "**Private vulnerability report — do not post publicly.**", "", *_header(doc),
        "## Observed impact", "", *impact, "", "Why this is treated as security-class:",
        *(f"- {_code(reason, 200)}" for reason in doc["classification"]["reasons"]), "",
        f"Suggested CWE: {', '.join(cwe_ids(doc))}", "",
        *_sections(doc, repro, env_names),
        "## If this report cannot be sent privately", "",
        f"Open https://github.com/{where}/security/advisories/new, or use the contact in the project's SECURITY.md. "
        "Do not open a public issue.", "",
    ]
    return "\n".join(lines) + "\n"


VIDEO_ENABLE = "pip install 'litetraffic[video]' (pillow, imageio-ffmpeg), or a system ffmpeg plus pillow"
FILE_MODE, SCRIPT_MODE, DIR_MODE = 0o600, 0o700, 0o700


def verify_scenario(scenario_dir: Path, evidence: RunEvidence) -> dict[str, Path]:
    """The scenario files to copy, after checking each against the run's scenario.lock.json; never inferred."""
    root = Path(scenario_dir).resolve()
    mismatch = f"--scenario {scenario_dir} does not match the run's scenario.lock.json"
    manifest = read_json(root / "manifest.json")
    if manifest is None or manifest != evidence.manifest_data:
        raise IssueError(f"{mismatch} (manifest.json)")
    files = evidence.lock.get("files") if isinstance(evidence.lock.get("files"), dict) else {}
    script = evidence.manifest_data.get("script")
    chosen = {"manifest.json": root / "manifest.json"}
    for name, digest in sorted(files.items()):
        path = (root / name).resolve()
        if not path.is_relative_to(root):
            raise IssueError(f"{mismatch} ({name})")
        if not path.is_file():
            if name == script:
                raise IssueError(f"{mismatch} ({name} is missing)")
            continue  # e.g. litetraffic/runtime.js, which is staged at run time
        if _sha256(path) != digest:
            raise IssueError(f"{mismatch} ({name})")
        chosen[name] = path
    return chosen


def scenario_kit(files: dict[str, Path], evidence: RunEvidence) -> dict | None:
    script = files.get(str(evidence.manifest_data.get("script") or ""))
    return parse_kit(script.read_text(encoding="utf-8", errors="replace")) if script else None


def render_video(doc: dict, out_dir: Path, skip: bool, scrubber: Scrubber | None = None) -> dict:
    """Render the replay from the scrubbed evidence document; any failure leaves the bundle complete without it.

    Pixels cannot be re-read like the text files, so every line the video would draw is checked first instead.
    """
    from litetraffic import proof

    if skip:
        return {"status": "skipped"}
    if proof.available() is None:
        return {"status": "unavailable", "enable": VIDEO_ENABLE, "missing": proof.missing()}
    try:
        drawn = [text for text, _ in proof.lines(doc)]
        if scrubber and any(scrubber.leak(text) or scrubber.text(text) != text for text in drawn):
            return {"status": "failed", "reason": "redaction check failed: a line the video would draw is not scrubbed"}
        files = proof.render(doc, out_dir)
    except Exception as exc:  # noqa: BLE001 - video is optional proof; the draft stands without it
        return {"status": "failed", "reason": f"{type(exc).__name__}: {exc}"}
    return {"status": "rendered", "files": [Path(path).name for path in files]}


def _write(path: Path, text: str, mode: int = FILE_MODE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=DIR_MODE)
    path.write_text(text, encoding="utf-8")
    path.chmod(mode)


def _restrict(root: Path) -> None:
    for path in [root, *root.rglob("*")]:
        if not path.is_symlink():
            path.chmod(DIR_MODE if path.is_dir() else (SCRIPT_MODE if path.name == "repro.sh" and path.parent == root else FILE_MODE))


@dataclass
class Bundle:
    dir: Path
    files: list[str]
    classification: Classification
    fingerprint: str
    title: str
    caveats: list[str]
    video: dict
    document: dict  # the scrubbed evidence.json content
    body: Path  # issue.md, or security.md for a security-class failure
    scrubber: Scrubber
    repro: str  # the scrubbed repro.sh text
    env_names: list[str]


def build_bundle(evidence: RunEvidence, options: dict) -> Bundle:
    """Write <run>/issue/ atomically (a temp dir renamed over it), scrubbed and re-checked; artifacts.json is never touched."""
    from litetraffic import repro

    classification = classify(evidence, bool(options.get("security")))
    fp, caveats = fingerprint(evidence), submit_blockers(evidence)
    scenario_dir = options.get("scenario")
    scenario_files = verify_scenario(scenario_dir, evidence) if scenario_dir else {}
    security = classification.kind == "security"
    scrubber = Scrubber(evidence, scenario_kit(scenario_files, evidence), security=security)
    document = scrubber.value(evidence_document(evidence, classification, fp, caveats, options))
    staging = Path(tempfile.mkdtemp(prefix=".issue-", dir=evidence.run_dir))
    try:
        document["video"] = render_video(document, staging, bool(options.get("no_video")), scrubber)
        script = scrubber.text(repro.synthesize(evidence, Path(scenario_dir) if scenario_dir else None, scrubber))
        env_names = sorted(secret_env_names(evidence.manifest)) if evidence.manifest else []
        _write(staging / "repro.sh", script, SCRIPT_MODE)
        if security:
            body_name = "security.md"
            body = scrubber.text(render_security_md(document, script, env_names, options.get("repo")))
            payload = {"summary": document["title"], "description": body, "cwe_ids": cwe_ids(document)}
            _write(staging / "report.json", json.dumps(scrubber.value(payload), indent=2, sort_keys=True, ensure_ascii=False) + "\n")
        else:
            body_name = "issue.md"
            body = scrubber.text(render_issue_md(document, script, env_names))
        _write(staging / body_name, body)
        _write(staging / "evidence.json", json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
        for name, path in scenario_files.items():
            _write(staging / "scenario" / name, scrubber.text(path.read_text(encoding="utf-8", errors="replace")))
        target = evidence.run_dir / BUNDLE
        filed = target / "filed.json"
        if filed.is_file() and not filed.is_symlink() and not target.is_symlink():
            shutil.copyfile(filed, staging / "filed.json")  # a re-draft keeps the record of an earlier filing
        _restrict(staging)
        if target.is_symlink() or target.is_file():
            target.unlink()
        elif target.exists():
            shutil.rmtree(target)
        staging.rename(target)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    scrubber.verify(target)
    files = sorted(path.relative_to(target).as_posix() for path in target.rglob("*") if path.is_file())
    return Bundle(target, files, classification, fp, document["title"], caveats, document["video"], document, target / body_name,
                  scrubber, script, env_names)


def embed_video(bundle: Bundle, name: str | None) -> None:
    """Rewrite issue.md so it references the attached video inline (gh rewrites `./name` to the uploaded URL); None drops it."""
    bundle.document["embed_video"] = name
    rerender(bundle)


def rerender(bundle: Bundle) -> None:
    """Rewrite issue.md and evidence.json from the (changed) document, then re-check the bundle."""
    _write(bundle.body, bundle.scrubber.text(render_issue_md(bundle.document, bundle.repro, bundle.env_names)))
    _write(bundle.dir / "evidence.json", json.dumps(bundle.document, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    bundle.scrubber.verify(bundle.dir)


def would_file(bundle: Bundle, repo: str | None) -> list[str]:
    where = repo or "OWNER/NAME"
    if bundle.classification.kind == "security":
        return ["gh", "api", "-X", "POST", f"repos/{where}/security-advisories/reports", "--input", str(bundle.dir / "report.json")]
    return ["gh", "issue", "create", "-R", where, "-t", bundle.title, "-F", str(bundle.body)]


def _passed(evidence: RunEvidence) -> dict:
    return {"ok": True, "run_id": evidence.run_id, "verdict": "pass", "kind": None, "fingerprint": None, "bundle": None, "files": [],
            "would_file": None, "url": None, "duplicate": None, "video": None, "attach": None, "removed": False,
            "message": f"run {evidence.run_id} passed; nothing to report"}


def _drafted(evidence: RunEvidence, bundle: Bundle, repo: str | None) -> dict:
    return {
        "ok": True,
        "run_id": evidence.run_id,
        "verdict": "fail",
        "kind": bundle.classification.kind,
        "reasons": bundle.classification.reasons,
        "fingerprint": bundle.fingerprint,
        "title": bundle.title,
        "bundle": str(bundle.dir),
        "files": bundle.files,
        "caveats": bundle.caveats,
        "warnings": bundle.document.get("warnings", []),
        "would_file": {"command": would_file(bundle, repo)},  # the title is already scrubbed
        "url": None,
        "duplicate": None,
        "video": bundle.video,
        "attach": bundle.document.get("attach_url"),
        "removed": False,
    }


def draft(ref: str, runs_dir: Path, options: dict) -> dict:
    """Draft the bundle for a failing run and describe what --submit would send; nothing leaves the machine."""
    evidence = load_run(ref, runs_dir)
    if gate(evidence) == "pass":
        return _passed(evidence)
    return _drafted(evidence, build_bundle(evidence, options), options.get("repo"))


def format_draft(result: dict) -> list[str]:
    if result["verdict"] == "pass":
        return [result["message"]]
    body = "security.md" if result["kind"] == "security" else "issue.md"
    detail = f"security: {', '.join(result['reasons'])}" if result["kind"] == "security" else "correctness"
    lines = [f"drafted {result['bundle']}/{body} ({detail})", f"files: {', '.join(result['files'])}", f"title: {result['title']}"]
    lines += [f"caveat: {item}" for item in result["caveats"]]
    lines += [f"warning: {item}" for item in result.get("warnings", [])]
    command = result["would_file"]["command"]
    lines.append(f"would POST {command[4]}" if result["kind"] == "security" else f"would run: {shlex.join(command)}")
    video = result["video"] or {}
    if video.get("status") == "rendered":
        lines.append("video: " + ", ".join(f"{result['bundle']}/{name}" for name in video.get("files", [])))
    elif video.get("status") == "unavailable":
        gap = f" (missing: {', '.join(video['missing'])})" if video.get("missing") else ""
        lines.append(f"video: unavailable{gap} — {video['enable']}")
    else:
        lines.append(f"video: {video.get('status')}" + (f" ({video['reason']})" if video.get("reason") else ""))
    lines.append("nothing was sent; add --submit --repo OWNER/NAME to file it")
    return lines


LEDGER = "issues.jsonl"  # in --runs-dir, next to the run directories, so it survives --remove-local
PRUNE_HINT = "run 'litetraffic prune' after adding the video"


def ledger_path(evidence: RunEvidence) -> Path:
    """<runs_dir>/issues.jsonl; a run given by path outside the default runs dir keeps its ledger beside it."""
    runs_dir = evidence.runs_dir.resolve()
    if evidence.runs_dir_given or evidence.run_dir.is_relative_to(runs_dir):
        return runs_dir / LEDGER
    return evidence.run_dir.parent / LEDGER


def ledger_paths(evidence: RunEvidence) -> list[Path]:
    """Every ledger that may hold an earlier filing of this run: dedupe reads them all."""
    return list(dict.fromkeys([ledger_path(evidence), evidence.runs_dir.resolve() / LEDGER, evidence.run_dir.parent / LEDGER]))


def ledger_match(evidence: RunEvidence, fp: str, repo: str) -> dict | None:
    """The most recent local filing of this fingerprint in this repo, if any."""
    match = None
    lines = [line for path in ledger_paths(evidence) if path.is_file() and not path.is_symlink()
             for line in path.read_text(encoding="utf-8", errors="replace").splitlines()]
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if (isinstance(entry, dict) and entry.get("fingerprint") == fp and str(entry.get("repo") or "").lower() == repo.lower()
                and entry.get("url")):
            match = entry
    return match


def record_filing(evidence: RunEvidence, bundle: Bundle, entry: dict) -> dict:
    """filed.json in the bundle, plus one line in the runs-dir ledger."""
    filed = {"run_id": evidence.run_id, "kind": bundle.classification.kind, "fingerprint": bundle.fingerprint,
             "filed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **entry}
    _write(bundle.dir / "filed.json", json.dumps(filed, indent=2, sort_keys=True) + "\n")
    ledger = ledger_path(evidence)
    filed = {**filed, "repo": str(filed.get("repo") or "").lower()}  # GitHub repo names are case-insensitive
    if ledger.is_symlink():
        raise IssueError(f"refusing to append to a symlinked ledger: {ledger}")
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("a", encoding="utf-8") as out:
        out.write(json.dumps(filed, sort_keys=True) + "\n")
    return filed


def render_comment_md(evidence: RunEvidence, bundle: Bundle) -> str:
    """A duplicate's follow-up: the new run, its summary and samples, nothing the issue already has."""
    doc = bundle.document
    lines = [f"Reproduced again by LiteTraffic run {_code(evidence.run_id)}.", "", *doc["summary"], "",
             "## Expected vs actual", "", *_check_rows(doc), "", "## Failing samples", "", *_sample_rows(doc), "",
             "---", f"Reported with LiteTraffic {doc['litetraffic_version']} · litetraffic-fingerprint: {bundle.fingerprint}"]
    return bundle.scrubber.text("\n".join(lines) + "\n")


def manual_route(repo: str, bundle: Bundle) -> str:
    return (f"open https://github.com/{repo}/security/advisories/new, or contact the maintainers via SECURITY.md or their "
            f"security contact, sending {bundle.body}. Do not open a public issue.")


def video_file(bundle: Bundle) -> str | None:
    names = (bundle.video.get("files") or []) if bundle.video.get("status") == "rendered" else []
    for name in ("proof.mp4", "proof.gif"):
        if name in names and (bundle.dir / name).is_file():
            return name
    return None


def remove_run(run_dir: Path) -> None:
    """The `prune` rules: a real directory (not a symlink) that holds run.json."""
    if run_dir.is_symlink() or not run_dir.is_dir() or run_dir != run_dir.resolve():
        raise IssueError(f"refusing to delete {run_dir}: not a real run directory")
    if not (run_dir / "run.json").is_file() or (run_dir / "run.json").is_symlink():
        raise IssueError(f"refusing to delete {run_dir}: it has no run.json")
    shutil.rmtree(run_dir)


def submit_security(evidence: RunEvidence, bundle: Bundle, gh, repo: str) -> dict:
    """A private vulnerability report, never an issue and never an attachment (uploads in public repos are world-readable)."""
    from litetraffic.github import GhError

    previous = ledger_match(evidence, bundle.fingerprint, repo)
    if previous:
        raise IssueError(f"already reported: {previous['url']}")
    try:
        enabled = gh.pvr_enabled(repo)
    except GhError as exc:
        raise IssueError(f"could not check private vulnerability reporting on {repo}: {exc}; {manual_route(repo, bundle)}") from exc
    if not enabled:
        raise IssueError(f"private vulnerability reporting is disabled on {repo}; {manual_route(repo, bundle)}")
    try:
        url = gh.report_vulnerability(repo, bundle.dir / "report.json")
    except GhError as exc:
        raise IssueError(f"{exc}; {manual_route(repo, bundle)}") from exc
    filed = record_filing(evidence, bundle, {"url": url, "repo": repo, "action": "private-report", "attached": []})
    return {"url": url, "duplicate": None, "filed": filed, "attached": [],
            "notes": [f"reported privately: {url}"], "remote_complete": False, "keep_reason": "security reports keep their evidence locally"}


def submit_public(evidence: RunEvidence, bundle: Bundle, gh, repo: str, comment: bool, labels: list[str]) -> dict:
    """Dedupe (ledger, search, the user's own open issues), then comment on the duplicate or create the issue."""
    previous = ledger_match(evidence, bundle.fingerprint, repo)
    if previous and gh.issue_state(previous["url"]) == "CLOSED":
        bundle.document["supersedes"] = previous["url"]  # a regression of a fixed issue: file anew, linking the old one
        rerender(bundle)
        previous = None
    duplicates = [{"url": previous["url"]}] if previous else gh.find_duplicates(repo, bundle.fingerprint)
    video, notes = video_file(bundle), []
    if duplicates:
        duplicate = duplicates[0]["url"]
        if not comment:
            raise IssueError(f"already reported: {duplicate}; add --comment to attach this run's evidence")
        body = bundle.dir / "comment.md"
        _write(body, render_comment_md(evidence, bundle))
        bundle.scrubber.verify(bundle.dir)
        url = gh.comment_issue(duplicate, body)
        notes.append(f"commented on {duplicate}: {url}")
        attached: list[str] = []
        attach_error = None
        entry = {"url": duplicate, "comment_url": url, "repo": repo, "action": "comment", "attached": attached}
    else:
        duplicate = None
        allowed, reason = gh.can_attach(repo) if video else (False, "no video")
        if allowed:
            embed_video(bundle, video)
        url, attach_error = gh.create_issue(repo, bundle.title, bundle.body, labels, [f"./{video}"] if allowed else [])
        attached = [video] if allowed and attach_error is None else []
        notes.append(f"filed {url}")
        if attach_error:
            notes.append(f"the issue exists but the video upload failed: {attach_error}")
            notes.append(drop_embed(bundle, gh, url))
        elif video and not allowed:
            notes.append(f"not attaching the video: {reason}")
        entry = {"url": url, "repo": repo, "action": "create", "attached": attached}
    if video and not attached:
        notes.append(f"drag {bundle.dir / video} into {url} to add the video")
    filed = record_filing(evidence, bundle, entry)
    keep = keep_reason(bundle, video, attached, upload_failed=bool(attach_error))
    return {"url": url, "duplicate": duplicate, "filed": filed, "attached": attached, "notes": notes,
            "remote_complete": keep is None, "keep_reason": keep}


def drop_embed(bundle: Bundle, gh, url: str) -> str:
    """After a failed upload gh leaves `./proof.mp4` in the filed body as a broken image: re-render without it and edit."""
    from litetraffic.github import GhError

    embed_video(bundle, None)
    try:
        gh.edit_issue(url, bundle.body)
    except GhError as exc:
        return f"could not remove the broken video reference from {url} ({exc}); edit the issue to drop it"
    return f"removed the broken video reference from {url}"


def keep_reason(bundle: Bundle, video: str | None, attached: list[str], upload_failed: bool) -> str | None:
    """Why --remove-local must keep the run, or None when everything the issue points to is reachable remotely.

    A video that failed to render is kept for the record; an explicit --attach-url stands in for a video that was never
    uploaded, but not for one whose upload failed, and the auto-detected Actions URL never counts.
    """
    if bundle.video.get("status") == "failed":
        return "the video failed to render"
    if video is None or attached:
        return None
    if upload_failed:
        return "the video upload failed"
    if bundle.document.get("attach_url") and bundle.document.get("attach_note") is None:
        return None  # an explicit --attach-url (the Actions URL carries its note)
    return "the video was not uploaded"


def submit(evidence: RunEvidence, bundle: Bundle, args) -> dict:
    from litetraffic.github import Gh

    blockers = submit_blockers(evidence)
    if blockers:
        raise IssueError(f"not submitting while the evidence is in doubt ({'; '.join(blockers)}); the draft is in {bundle.dir}")
    gh = Gh(bundle.scrubber.text)
    gh.ensure_auth()
    if bundle.classification.kind == "security":
        return submit_security(evidence, bundle, gh, args.repo)
    return submit_public(evidence, bundle, gh, args.repo, args.comment, list(args.label or []))


def _run_is_symlink(ref: str, runs_dir: Path) -> bool:
    return Path(ref).is_symlink() or (Path(ref).name == ref and (Path(runs_dir) / ref).is_symlink())


DEFAULT_RUNS_DIR = Path(".litetraffic/runs")
REPO_RE = re.compile(r"^(?!-)[A-Za-z0-9-]{1,39}/(?!\.)[A-Za-z0-9._-]{1,100}$")  # no '.', '..' or dot-led names


def report(args) -> dict:
    """`litetraffic report issue`: check the flags, draft the bundle, and (with --submit) file it."""
    if args.remove_local and not args.submit:
        raise IssueError("--remove-local needs --submit: the run is only deleted after a successful filing")
    if args.submit and not args.repo:
        raise IssueError("--submit needs --repo OWNER/NAME")
    if args.repo is not None and (not REPO_RE.match(args.repo) or args.repo.startswith("-")):
        raise IssueError(f"--repo must be OWNER/NAME, got {args.repo!r}")
    if args.known_issue is not None and not SAFE_URL.match(args.known_issue):
        raise IssueError(f"--known-issue must be a plain https:// URL, got {_short(args.known_issue, 80)!r}")
    runs_dir = Path(args.runs_dir) if args.runs_dir is not None else DEFAULT_RUNS_DIR
    if args.remove_local and _run_is_symlink(args.run, runs_dir):
        raise IssueError(f"--remove-local refuses a symlinked run directory: {args.run}")
    options = {"scenario": args.scenario, "repo": args.repo, "security": args.security, "title": args.title,
               "target_version": args.target_version, "attach_url": args.attach_url, "no_video": args.no_video,
               "known_issue": args.known_issue}
    evidence = load_run(args.run, runs_dir)
    evidence.runs_dir_given = args.runs_dir is not None
    if gate(evidence) == "pass":
        return _passed(evidence)
    bundle = build_bundle(evidence, options)
    result = _drafted(evidence, bundle, args.repo)
    if bundle.classification.kind == "security":
        if args.public:
            raise IssueError("security-class failures are never filed publicly; omit --public to send a private vulnerability report")
        if args.comment:
            raise IssueError("--comment is for public issues; a security-class failure goes only to a private vulnerability report")
    if not args.submit:
        return result
    filed = submit(evidence, bundle, args)
    result.update(submitted=True, url=filed["url"], duplicate=filed["duplicate"], filed=filed["filed"], notes=filed["notes"])
    if args.remove_local:
        reason = filed["keep_reason"] if not filed["remote_complete"] else None
        if reason is None:
            remove_run(evidence.run_dir)
            result["removed"] = True
            result["notes"].append(f"removed {evidence.run_dir}")
        elif bundle.classification.kind == "security":
            result["notes"].append(f"kept {evidence.run_dir}: {reason}; maintainers may ask for the evidence")
        else:
            result["notes"].append(f"kept {evidence.run_dir}: {reason}; {PRUNE_HINT}")
    return result


def format_result(result: dict) -> list[str]:
    if result.get("submitted"):
        return [f"drafted {result['bundle']} ({result['kind']})", *result["notes"]]
    return format_draft(result)
