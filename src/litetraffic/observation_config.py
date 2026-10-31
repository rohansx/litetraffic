"""The final observation's manifest model and its expected-value matchers."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from urllib.parse import quote

from pydantic import Field, JsonValue, model_validator

from litetraffic.expression import NAMES, evaluate, fixture_keys, is_expression
from litetraffic.modelbase import ENV_NAME, StrictModel
from litetraffic.target import normalize_origin


MATCHER_KEYS = {"eq", "gte", "lte", "len", "exists"}


def as_matcher(expected: JsonValue) -> dict[str, JsonValue]:
    """A dict whose keys are all matcher keys is a matcher; any other value means equality."""
    if isinstance(expected, dict) and expected and set(expected) <= MATCHER_KEYS:
        return expected
    return {"eq": expected}


class Placeholders(dict):
    """Stand-in plan variables for load-time checks: every plan name and every fixture.KEY is 0."""

    def __init__(self):
        super().__init__(dict.fromkeys(NAMES, 0))

    def __contains__(self, name: object) -> bool:
        return super().__contains__(name) or str(name).startswith("fixture.")

    def __missing__(self, name: str) -> int:
        return 0


WHOLE_FIXTURE_REF = re.compile(r"\$\{\s*fixture\.([A-Za-z_][A-Za-z0-9_]*)\s*\}")
PATH_FIXTURE_REF = re.compile(r"\$\{fixture\.([A-Za-z_][A-Za-z0-9_]*)\}")


def resolve_expected(expected: dict[str, JsonValue], variables: Mapping[str, object], defer_fixture: bool = False) -> dict[str, JsonValue]:
    """Evaluate `${...}` literals and matcher operands; every other value is kept as written.

    A whole `${fixture.KEY}` operand takes the fixture value as it is (a string or a number); inside a larger
    expression it must be an integer. `defer_fixture` keeps operands that need a fixture value as written."""
    resolved = {}
    for pointer, value in expected.items():
        matcher = as_matcher(value)
        (op, operand), = matcher.items()  # validation already rejected multi-key matchers
        if is_expression(operand) and defer_fixture and any(f"fixture.{key}" not in variables for key in fixture_keys(operand)):
            resolved[pointer] = value
            continue
        if is_expression(operand):
            whole = WHOLE_FIXTURE_REF.fullmatch(operand)
            raw = variables.get(f"fixture.{whole[1]}") if whole else None
            operand = raw if isinstance(raw, str | int | float) and not isinstance(raw, bool) else evaluate(operand, variables)
            value = operand if matcher is not value else {op: operand}
        resolved[pointer] = value
    return resolved


def resolve_path(path: str, variables: Mapping[str, object]) -> str:
    """Substitute each `${fixture.KEY}` with its URL-encoded value; a missing or unusable value is a ValueError."""

    def value(match: re.Match) -> str:
        found = variables.get(f"fixture.{match[1]}")
        if isinstance(found, bool) or not isinstance(found, str | int | float) or str(found) in {"", ".", ".."}:
            raise ValueError(f"fixture.{match[1]} has no usable value")
        return quote(str(found), safe="")

    return PATH_FIXTURE_REF.sub(value, path)


def fixture_refs(path: str, expected: Mapping[str, JsonValue]) -> set[str]:
    """Every fixture KEY a path or an expected map refers to."""
    keys = set(PATH_FIXTURE_REF.findall(path))
    for value in expected.values():
        operand = next(iter(as_matcher(value).values()))
        if is_expression(operand):
            keys |= fixture_keys(operand)
    return keys


def _check_matcher(pointer: str, matcher: dict[str, JsonValue]) -> None:
    if len(matcher) != 1:
        raise ValueError(f"{pointer}: matcher must have exactly one of {sorted(MATCHER_KEYS)}")
    (op, operand), number = next(iter(matcher.items())), (int, float)
    if op != "exists" and is_expression(operand):
        return
    if op in {"gte", "lte"} and (isinstance(operand, bool) or not isinstance(operand, number)):
        raise ValueError(f"{pointer}: {op} matcher needs a number")
    if op == "len" and (isinstance(operand, bool) or not isinstance(operand, int) or operand < 0):
        raise ValueError(f"{pointer}: len matcher needs a non-negative integer")
    if op == "exists" and not isinstance(operand, bool):
        raise ValueError(f"{pointer}: exists matcher needs true or false")


OBSERVER_REQUEST_SECONDS = 5  # hard wall-clock deadline of one observer request


class Until(StrictModel):
    """Re-read the observer every `interval_seconds` until the expectations pass or `deadline_seconds` pass."""

    deadline_seconds: float = Field(gt=0, le=60)
    interval_seconds: float = Field(ge=0.5)

    @model_validator(mode="after")
    def require_interval_within_deadline(self) -> "Until":
        if self.interval_seconds > self.deadline_seconds:
            raise ValueError("until interval_seconds must not exceed deadline_seconds")
        return self

    @property
    def max_attempts(self) -> int:
        # attempts at 0, interval, 2 * interval, ... and a last one at the deadline
        return math.ceil(self.deadline_seconds / self.interval_seconds) + 1


class FinalObservation(StrictModel):
    path: str = Field(min_length=1)
    assertion: str = Field(min_length=1)
    expected: dict[str, JsonValue] = Field(min_length=1)
    bearer_token_env: str | None = None
    origin: str | None = None
    origin_env: str | None = None  # names a variable holding the origin, checked against the allowed origins at run time
    headers_env: dict[str, str] = Field(default_factory=dict)
    until: Until | None = None

    @property
    def reserved_seconds(self) -> int:
        """Wall clock reserved from max_seconds: the polling deadline plus one last request."""
        return math.ceil(self.until.deadline_seconds if self.until else 0) + OBSERVER_REQUEST_SECONDS

    @property
    def max_requests(self) -> int:
        return self.until.max_attempts if self.until else 1

    @property
    def fixture_keys(self) -> set[str]:
        return fixture_refs(self.path, self.expected)

    @model_validator(mode="after")
    def require_safe_read(self) -> "FinalObservation":
        if not self.path.startswith("/") or self.path.startswith("//") or "#" in self.path:
            raise ValueError("observation path must be a same-origin relative path")
        if any(pointer and not pointer.startswith("/") for pointer in self.expected):
            raise ValueError("expected keys must be JSON Pointers")
        for pointer, value in self.expected.items():
            if as_matcher(value) is value:
                _check_matcher(pointer, value)
        try:
            resolve_expected(self.expected, Placeholders())
        except ValueError as exc:
            raise ValueError(f"expected {exc}") from None
        if "${fixture." in PATH_FIXTURE_REF.sub("", self.path):
            raise ValueError("observation path has a malformed ${fixture.KEY} placeholder")
        if self.bearer_token_env and not re.fullmatch(ENV_NAME, self.bearer_token_env):
            raise ValueError("bearer_token_env must name an uppercase environment variable")
        for header, env in self.headers_env.items():
            if not re.fullmatch(r"[A-Za-z0-9!#$%&'*+.^_`|~-]+", header):
                raise ValueError(f"headers_env key {header!r} is not a valid header name")
            if not re.fullmatch(ENV_NAME, env):
                raise ValueError(f"headers_env {header} must name an uppercase environment variable")
        if self.origin is not None and self.origin_env is not None:
            raise ValueError("observation takes origin or origin_env, not both")
        if self.origin_env is not None and not re.fullmatch(ENV_NAME, self.origin_env):
            raise ValueError("origin_env must name an uppercase environment variable")
        if self.origin is not None:
            self.origin = normalize_origin(self.origin, "observation origin")
        return self
