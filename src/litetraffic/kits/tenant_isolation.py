"""`litetraffic init tenant-isolation`: a complete scenario bundle from a small JSON config."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, JsonValue, model_validator

from litetraffic.auth import token_env_name
from litetraffic.modelbase import ENV_NAME, StrictModel
from litetraffic.models import ENGINE_SLACK_SECONDS, Fixtures, JwtAuth, Schedule
from litetraffic.observation_config import FinalObservation
from litetraffic.scenario import ScenarioBundle, load_scenario

REJECTED_STATUSES = [401, 403, 404]
DEFAULT_STATUSES = {"read": [200], "write": [200, 201, 204]}
DEFAULT_FIXTURES = {"recipe": "static-resources"}
DEFAULT_SCHEDULE = {
    "unit": "journeys_per_second",
    "phases": [
        {"name": "warmup", "seconds": 1, "rate": 1},
        {"name": "measure", "seconds": 4, "rate": 2},
        {"name": "recovery", "seconds": 1, "rate": 1},
    ],
}
MAX_ARTIFACT_BYTES = 64 * 1024 * 1024
HEADER_NAME = r"[A-Za-z0-9!#$%&'*+.^_`|~-]+"
Status = Annotated[int, Field(strict=True, ge=100, le=599)]
KEY = r"[A-Za-z_][A-Za-z0-9_]*"
JOURNEY = "journey"  # `{journey}` is filled with lt.journeyKey()
ATTACK_SUFFIX = "-lt-attack-{journey}"
PLACEHOLDER = re.compile(r"\{(" + KEY + r")\}")
Ref = Annotated[str, Field(min_length=1)] | Annotated[dict[Annotated[str, Field(pattern=f"^{KEY}$")], Annotated[str, Field(min_length=1)]], Field(min_length=1)]


def _strings(value: JsonValue):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)


def _attack_body(value: JsonValue, keys: set[str]) -> JsonValue:
    """`value` with every string suffixed, so the attacker never writes what a check_own owner just wrote.

    Strings naming a resource key are left alone: they address the victim's resource, and changing them would aim elsewhere.
    """
    if isinstance(value, str):
        return value if set(PLACEHOLDER.findall(value)) & keys else value + ATTACK_SUFFIX
    if isinstance(value, list):
        return [_attack_body(item, keys) for item in value]
    if isinstance(value, dict):
        return {key: _attack_body(item, keys) for key, item in value.items()}
    return value


class Identity(StrictModel):
    name: str = Field(min_length=1)
    auth: JwtAuth | None = None  # `kind` defaults to jwt_hs256
    token_env: str | None = None  # names a variable holding a ready bearer token
    headers: dict[str, str] = Field(default_factory=dict)  # plain, non-secret headers such as a tenant id
    # Each resource is an id (the `{id}` placeholder) or named placeholders, e.g. {"id": org, "position": pos}.
    resources: list[Ref] = Field(min_length=1)
    # Strings that only this identity's private data contains (a seeded secret, a row id); no other caller's response may contain one.
    markers: list[Annotated[str, Field(min_length=1)]] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def default_auth_kind(cls, data):
        if isinstance(data, dict) and isinstance(data.get("auth"), dict):
            return data | {"auth": {"kind": "jwt_hs256"} | data["auth"]}
        return data

    @model_validator(mode="after")
    def require_one_credential(self) -> "Identity":
        if self.auth and self.token_env:
            raise ValueError("identity takes auth or token_env, not both")
        if not (self.auth or self.token_env or self.headers):
            raise ValueError("identity needs auth, token_env or headers to tell it apart")
        if self.token_env and not re.fullmatch(ENV_NAME, self.token_env):
            raise ValueError("token_env must name an uppercase environment variable")
        if bad := [name for name in self.headers if not re.fullmatch(HEADER_NAME, name)]:
            raise ValueError(f"invalid header name {bad[0]!r}")
        return self

    @property
    def refs(self) -> list[dict[str, str]]:
        return [ref if isinstance(ref, dict) else {"id": ref} for ref in self.resources]


class Endpoint(StrictModel):
    name: str | None = Field(default=None, min_length=1)  # lets a write's read_back refer to this read
    method: Literal["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"]
    path: str = Field(min_length=1)
    kind: Literal["read", "write"]
    body: JsonValue = None
    # What the attacker writes; default: `body` with every string suffixed by ATTACK_SUFFIX (see _attack_body).
    attack_body: JsonValue = None
    expected_statuses: list[Status] | None = Field(default=None, min_length=1)  # own-access success statuses
    # Also run the write as the owner. Only for idempotent writes: concurrent journeys otherwise race the read-back compare.
    check_own: bool = False
    # The read endpoint (index into `endpoints` or its `name`) that reads this write's resource back; default: the first read.
    read_back: Annotated[int, Field(strict=True)] | str | None = None
    journey_in_path: bool = False  # allows `{journey}` (the journey key) in the path; body strings always get it

    @model_validator(mode="after")
    def require_safe_template(self) -> "Endpoint":
        if not self.path.startswith("/") or self.path.startswith("//") or any(char in self.path for char in "#"):
            raise ValueError("path must be a same-origin path")
        if self.kind == "read" and self.method not in {"GET", "HEAD"}:
            raise ValueError("read endpoints must use GET or HEAD")
        if self.kind == "write" and self.method in {"GET", "HEAD"}:
            raise ValueError("write endpoints must use POST, PUT, PATCH or DELETE")
        if self.kind == "read" and (self.body is not None or self.check_own or self.read_back is not None or "attack_body" in self.model_fields_set):
            raise ValueError("read endpoints take no body, attack_body, check_own or read_back")
        return self

    @property
    def statuses(self) -> list[int]:
        return self.expected_statuses or DEFAULT_STATUSES[self.kind]


class KitConfig(StrictModel):
    name: str = Field(min_length=1)
    identities: list[Identity] = Field(min_length=2, max_length=2)
    endpoints: list[Endpoint] = Field(min_length=1)
    # Absent means the default; an explicit null is rejected.
    fixtures: Fixtures = Field(default_factory=lambda: Fixtures.model_validate(DEFAULT_FIXTURES))
    observations: list[FinalObservation] = Field(default_factory=list)
    schedule: Schedule = Field(default_factory=lambda: Schedule.model_validate(DEFAULT_SCHEDULE))
    allowed_origins: list[str] = Field(default_factory=list)  # copied into the manifest for observation origins
    allowed_origins_env: str | None = None
    max_in_flight: int = Field(default=6, gt=0)
    unauthenticated_probe: bool = False  # also read every resource with no credentials; must be rejected

    @model_validator(mode="after")
    def require_distinct_identities_and_a_read(self) -> "KitConfig":
        names = [identity.name for identity in self.identities]
        if len(set(names)) != 2 or len({token_env_name(name) for name in names}) != 2:
            raise ValueError(f"identity names must be distinct: {', '.join(names)}")
        if not any(endpoint.kind == "read" for endpoint in self.endpoints):
            raise ValueError("endpoints need at least one read endpoint (the first one reads back victim state)")
        named = [endpoint.name for endpoint in self.endpoints if endpoint.name]
        if len(set(named)) != len(named):
            raise ValueError(f"endpoint names must be distinct: {', '.join(named)}")
        for endpoint in self.endpoints:
            if endpoint.kind == "write":
                self.read_back_index(endpoint)  # raises on a bad reference
        generated = set(_journey_assertions(self))
        if clash := [o.assertion for o in self.observations if o.assertion in generated]:
            raise ValueError(f"observation assertion {clash[0]!r} collides with a generated assertion")
        refs = [ref for identity in self.identities for ref in identity.refs]
        keys = set(refs[0])
        if any(set(ref) != keys for ref in refs):
            raise ValueError("resources must all have the same keys")
        if JOURNEY in keys:
            raise ValueError(f"resource key {JOURNEY!r} is reserved for the journey key placeholder")
        for endpoint in self.endpoints:
            for name in re.findall(r"\{([^{}]*)\}", endpoint.path):
                if name == JOURNEY and not endpoint.journey_in_path:
                    raise ValueError(f"{endpoint.method} {endpoint.path}: {{journey}} in a path needs journey_in_path: true")
                if name not in keys | {JOURNEY}:
                    raise ValueError(f"path placeholder {{{name}}} is not a resource key ({', '.join(sorted(keys))})")
            used = {name for text in [endpoint.path, *_strings(endpoint.body)] for name in PLACEHOLDER.findall(text)}
            if not used & keys:
                raise ValueError(f"{endpoint.method} {endpoint.path} uses no resource placeholder in its path or body")
            # A bodyless write with no owner run (a plain DELETE) has no owner payload to pre-match.
            bodyless = endpoint.body is None and not endpoint.check_own and "attack_body" not in endpoint.model_fields_set
            if endpoint.kind == "write" and not bodyless and self.attack_body(endpoint) == endpoint.body:
                raise ValueError(f"{endpoint.method} {endpoint.path}: the attacker body equals the owner body (no string to vary); "
                                 "set attack_body to a payload the owner never writes")
        return self

    def attack_body(self, endpoint: Endpoint) -> JsonValue:
        if "attack_body" in endpoint.model_fields_set:
            return endpoint.attack_body
        return _attack_body(endpoint.body, set(self.identities[0].refs[0]))

    def read_back_index(self, endpoint: Endpoint) -> int:
        reference = endpoint.read_back
        if reference is None:
            index = next((i for i, candidate in enumerate(self.endpoints) if candidate.kind == "read" and candidate.method == "GET"), None)
            if index is None:
                raise ValueError(f"{endpoint.method} {endpoint.path}: read_back needs a GET read endpoint (HEAD has no body to compare)")
            return index
        names = {candidate.name: i for i, candidate in enumerate(self.endpoints) if candidate.name}
        index = names.get(reference) if isinstance(reference, str) else reference
        if index is None or not 0 <= index < len(self.endpoints) or self.endpoints[index].kind != "read":
            raise ValueError(f"{endpoint.method} {endpoint.path}: read_back {reference!r} is not a read endpoint")
        if self.endpoints[index].method != "GET":
            raise ValueError(f"{endpoint.method} {endpoint.path}: read_back {reference!r} must be a GET read (HEAD has no body to compare)")
        return index


def _journey_assertions(config: KitConfig) -> list[str]:
    names = ["own_access", "cross_tenant_read_blocked"]
    if any(endpoint.kind == "write" for endpoint in config.endpoints):
        names += ["cross_tenant_write_blocked", "victim_unchanged"]
    if config.unauthenticated_probe:
        names.append("unauthenticated_rejected")
    if any(identity.markers for identity in config.identities):
        names.append("no_foreign_data_in_own_responses")
    return names


def warnings(config: KitConfig) -> list[str]:
    return [f"identity {identity.name!r} declares no markers: status-only checks cannot detect data returned in denial bodies"
            for identity in config.identities if not identity.markers]


def _journey(config: KitConfig) -> dict:
    resources = sum(len(identity.resources) for identity in config.identities)
    reads = sum(endpoint.kind == "read" for endpoint in config.endpoints)
    writes = sum(endpoint.kind == "write" for endpoint in config.endpoints)
    own_writes = sum(endpoint.check_own for endpoint in config.endpoints)
    own_statuses = {status for endpoint in config.endpoints for status in endpoint.statuses}
    anonymous_reads = reads if config.unauthenticated_probe else 0
    expected = {"own": sorted(own_statuses), "cross_tenant": REJECTED_STATUSES}
    if config.unauthenticated_probe:
        expected["unauthenticated"] = REJECTED_STATUSES
    return {
        "name": "tenant-isolation",
        # own reads (+ own writes), cross-tenant reads, per cross-tenant write: read before, write, read after,
        # and the optional anonymous reads
        "max_requests": resources * (reads + own_writes + reads + 3 * writes + anonymous_reads),
        "max_writes": resources * (own_writes + writes),
        "expected_statuses": expected,
    }


def build_manifest(config: KitConfig, raw: dict) -> dict:
    """The manifest dict; passthrough sections (fixtures, schedule, observations) are copied as written."""
    fixtures = raw.get("fixtures", DEFAULT_FIXTURES)
    schedule = raw.get("schedule", DEFAULT_SCHEDULE)
    observations = raw.get("observations", [])
    origins = {key: raw[key] for key in ("allowed_origins", "allowed_origins_env") if key in raw}
    journey = _journey(config)
    phases = config.schedule.resolve(seed=0)
    planned, seconds = sum(phase.admitted_journeys for phase in phases), sum(phase.seconds for phase in phases)
    reserved = config.fixtures
    lifecycle = 2 * int(reserved.owned_http is not None)
    actors = []
    for identity, written in zip(config.identities, raw["identities"]):
        actor = {"class": identity.name, "count": 1, "auth_recipe": "kit-headers"}
        if identity.auth:
            actor |= {"auth_recipe": "kit-jwt", "auth": {"kind": "jwt_hs256"} | written["auth"]}
        elif identity.token_env:
            actor["auth_recipe"] = "kit-bearer-env"
        actors.append(actor)
    manifest = {
        "schema_version": 1,
        "name": config.name,
        "script": "journeys.js",
        "actors": actors,
        "fixtures": fixtures,
        "journeys": [journey],
        "schedule": schedule,
        "assertions": _journey_assertions(config) + [observation.assertion for observation in config.observations],
        "observer": "tenant-isolation-kit",
        "budgets": {
            "max_seconds": seconds + ENGINE_SLACK_SECONDS + reserved.reserved_seconds + sum(o.reserved_seconds for o in config.observations),
            "max_requests": planned * journey["max_requests"] + sum(o.max_requests for o in config.observations) + lifecycle,
            "max_write_attempts": planned * journey["max_writes"] + lifecycle,
            "max_in_flight": config.max_in_flight,
            "max_artifact_bytes": MAX_ARTIFACT_BYTES,
        },
    }
    if observations:
        manifest["observations"] = observations
    token_envs = [identity.token_env for identity in config.identities if identity.token_env]
    if token_envs:
        manifest["secret_env"] = token_envs
    manifest |= origins
    return manifest


def build_script(config: KitConfig) -> str:
    kit = {
        "assertions": _journey_assertions(config),
        "rejected": REJECTED_STATUSES,
        "identities": [
            {
                "name": identity.name,
                "headers": identity.headers,
                "token_env": token_env_name(identity.name) if identity.auth else identity.token_env,
                "resources": identity.refs,
                "markers": identity.markers,
            }
            for identity in config.identities
        ],
        "endpoints": [
            {"method": e.method, "path": e.path, "kind": e.kind, "body": e.body, "statuses": e.statuses, "check_own": e.check_own,
             "journey_in_path": e.journey_in_path}
            | ({"read_back": config.read_back_index(e), "attack_body": config.attack_body(e)} if e.kind == "write" else {})
            for e in config.endpoints
        ],
    }
    return SCRIPT.replace("__KIT__", json.dumps(kit, indent=2))


def generate(config_path: Path, out: Path) -> tuple[ScenarioBundle, list[str]]:
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"cannot read kit config: {exc}") from exc
    config = KitConfig.model_validate(raw)  # ValidationError: the CLI reports it as an invalid kit config
    manifest = build_manifest(config, raw)
    out.mkdir(parents=True, exist_ok=True)
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    (out / "journeys.js").write_text(build_script(config), encoding="utf-8")
    return load_scenario(out), warnings(config)


SCRIPT = """\
// Generated by `litetraffic init tenant-isolation`. Edit the kit config and regenerate instead of editing this file.
import http from "k6/http";
import * as lt from "./litetraffic/runtime.js";

const KIT = __KIT__;
const MAX_SAMPLES = 3;
const ANONYMOUS = { name: "unauthenticated", headers: {}, token_env: null };
const PLACEHOLDER = /\\{([A-Za-z_][A-Za-z0-9_]*)\\}/g;

export const options = lt.options();

// Replace {name} with the resource's value (or the journey key for {journey}) in every string; names the resource lacks stay literal.
function fill(value, ref, encode) {
  if (typeof value === "string") return value.replace(PLACEHOLDER, (match, name) => (name in ref ? encode(ref[name]) : match));
  if (Array.isArray(value)) return value.map((item) => fill(item, ref, encode));
  if (value && typeof value === "object") return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, fill(item, ref, encode)]));
  return value;
}

function send(identity, endpoint, ref, operation, template = endpoint.body) {
  const headers = { "X-LiteTraffic-Run": __ENV.LT_RUN_ID, ...identity.headers };
  if (__ENV.LT_FIXTURE_ID) headers["X-LiteTraffic-Fixture"] = __ENV.LT_FIXTURE_ID;
  const token = identity.token_env && __ENV[identity.token_env];
  if (token) headers.Authorization = `Bearer ${token}`;
  let body = null;
  if (template !== null) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(fillBody(template, ref));
  }
  const pathValues = endpoint.journey_in_path ? { ...ref, journey: lt.journeyKey() } : ref;
  const url = __ENV.LT_TARGET + fill(endpoint.path, pathValues, encodeURIComponent);
  return http.request(endpoint.method, url, body, { headers, tags: { operation }, jar: identity.jar });
}

function fillBody(template, ref) {
  return fill(template, { ...ref, journey: lt.journeyKey() }, (text) => text);
}

// True when every field of `part` already holds that value in `state`: writing `part` could not change it.
// True when `state` cannot show `part` being written: every key of `part` that the state also has (at least one)
// already holds `part`'s value. Keys the state lacks (fields the server ignores) cannot reveal the write either.
function holds(state, part) {
  if (part && typeof part === "object" && !Array.isArray(part) && state && typeof state === "object" && !Array.isArray(state)) {
    const shared = Object.keys(part).filter((key) => key in state);
    return shared.length > 0 && shared.every((key) => holds(state[key], part[key]));
  }
  return lt.deepEqual(state, part);
}

function empty(response) {
  const state = content(response);
  return response.body === null || response.body === undefined || response.body === "" || state === null ||
    (typeof state === "object" && Object.keys(state).length === 0);
}

function content(response) {
  try {
    return response.json();
  } catch (_) {
    return response.body;
  }
}

// Evidence never carries response bodies: only who, what and the status.
function where(identity, endpoint, ref, status) {
  return { identity: identity.name, method: endpoint.method, path: endpoint.path, id: ref, status };
}

// Indexes of `victim`'s markers in the raw response body (any status). Evidence gets the indexes, never the values.
function leaked(response, victim) {
  const body = typeof response.body === "string" ? response.body : "";
  return victim.markers.flatMap((marker, index) => (body.includes(marker) ? [index] : []));
}

// Fails `assertion` when the status is wrong or the body carries one of `victim`'s markers.
function check(failures, assertion, statusOk, response, victim, entry) {
  const markers = leaked(response, victim);
  if (markers.length) failures[assertion].push({ ...entry, markers: { identity: victim.name, indexes: markers } });
  else if (!statusOk) failures[assertion].push(entry);
}

export default function tenantIsolation() {
  const failures = Object.fromEntries(KIT.assertions.map((name) => [name, []]));
  // victim_unchanged checks whose read-back could not show the attack: an empty state, or one the attacker body already holds.
  let inconclusive = 0;
  // One cookie jar per identity per journey, never k6's shared VU jar: a session cookie set for one identity must not ride along on another's requests.
  const identities = KIT.identities.map((identity) => ({ ...identity, jar: new http.CookieJar() }));
  identities.forEach((owner, index) => {
    const attacker = identities[1 - index];
    // No owner response may carry the other identity's markers (the assertion exists whenever markers do).
    const ownBody = (response, endpoint, ref) =>
      check(failures, "no_foreign_data_in_own_responses", true, response, attacker, where(owner, endpoint, ref, response.status));
    for (const ref of owner.resources) {
      for (const endpoint of KIT.endpoints) {
        if (endpoint.kind === "write" && !endpoint.check_own) continue;
        const own = send(owner, endpoint, ref, "own");
        if (!endpoint.statuses.includes(own.status)) failures.own_access.push(where(owner, endpoint, ref, own.status));
        ownBody(own, endpoint, ref);
      }
      for (const endpoint of KIT.endpoints) {
        if (endpoint.kind === "read") {
          const cross = send(attacker, endpoint, ref, "cross_tenant");
          check(failures, "cross_tenant_read_blocked", KIT.rejected.includes(cross.status), cross, owner, where(attacker, endpoint, ref, cross.status));
          if (failures.unauthenticated_rejected) {
            const anonymous = send({ ...ANONYMOUS, jar: new http.CookieJar() }, endpoint, ref, "unauthenticated");  // an empty jar: no cookies at all
            check(failures, "unauthenticated_rejected", KIT.rejected.includes(anonymous.status), anonymous, owner,
              where(ANONYMOUS, endpoint, ref, anonymous.status));
          }
          continue;
        }
        const readBack = KIT.endpoints[endpoint.read_back];
        const before = send(owner, readBack, ref, "own");
        const cross = send(attacker, endpoint, ref, "cross_tenant", endpoint.attack_body);
        const after = send(owner, readBack, ref, "own");
        ownBody(before, readBack, ref);
        ownBody(after, readBack, ref);
        check(failures, "cross_tenant_write_blocked", KIT.rejected.includes(cross.status), cross, owner, where(attacker, endpoint, ref, cross.status));
        const unchanged = readBack.statuses.includes(before.status) && after.status === before.status && lt.deepEqual(content(before), content(after));
        if (!unchanged) {
          failures.victim_unchanged.push({ ...where(attacker, endpoint, ref, cross.status), before: before.status, after: after.status });
        } else if (empty(before) || holds(content(before), fillBody(endpoint.attack_body, ref))) {
          inconclusive += 1;
        }
      }
    }
  });
  const expected = {
    own_access: "every owner request returns one of its endpoint's statuses",
    cross_tenant_read_blocked: { statuses: KIT.rejected, body: "none of the owner's markers" },
    cross_tenant_write_blocked: { statuses: KIT.rejected, body: "none of the owner's markers" },
    unauthenticated_rejected: { statuses: KIT.rejected, body: "none of the owner's markers" },
    victim_unchanged: "the owner's read-back is identical before and after each cross-tenant write",
    no_foreign_data_in_own_responses: "no owner response contains the other identity's markers",
  };
  for (const [assertion, failed] of Object.entries(failures)) {
    // No event makes this journey's evidence missing, so the assertion reports unknown rather than a pass it cannot back.
    if (assertion === "victim_unchanged" && failed.length === 0 && inconclusive > 0) continue;
    lt.evidence(assertion, failed.length === 0, {
      expected: expected[assertion],
      actual: { failures: failed.length, samples: failed.slice(0, MAX_SAMPLES) },
    });
  }
}
"""
