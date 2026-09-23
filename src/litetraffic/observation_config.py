"""The final observation's manifest model and its expected-value matchers."""

from __future__ import annotations

import re

from pydantic import Field, JsonValue, model_validator

from litetraffic.expression import NAMES, evaluate, is_expression
from litetraffic.modelbase import ENV_NAME, StrictModel
from litetraffic.target import normalize_origin


MATCHER_KEYS = {"eq", "gte", "lte", "len", "exists"}


def as_matcher(expected: JsonValue) -> dict[str, JsonValue]:
    """A dict whose keys are all matcher keys is a matcher; any other value means equality."""
    if isinstance(expected, dict) and expected and set(expected) <= MATCHER_KEYS:
        return expected
    return {"eq": expected}


def resolve_expected(expected: dict[str, JsonValue], variables: dict[str, int]) -> dict[str, JsonValue]:
    """Evaluate `${...}` literals and matcher operands; every other value is kept as written."""
    resolved = {}
    for pointer, value in expected.items():
        matcher = as_matcher(value)
        (op, operand), = matcher.items()  # validation already rejected multi-key matchers
        if is_expression(operand):
            operand = evaluate(operand, variables)
            value = operand if matcher is not value else {op: operand}
        resolved[pointer] = value
    return resolved


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


class FinalObservation(StrictModel):
    path: str = Field(min_length=1)
    assertion: str = Field(min_length=1)
    expected: dict[str, JsonValue] = Field(min_length=1)
    bearer_token_env: str | None = None
    origin: str | None = None
    origin_env: str | None = None  # names a variable holding the origin, checked against the allowed origins at run time
    headers_env: dict[str, str] = Field(default_factory=dict)

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
            resolve_expected(self.expected, dict.fromkeys(NAMES, 0))
        except ValueError as exc:
            raise ValueError(f"expected {exc}") from None
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
