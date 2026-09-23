"""Plan-aware `${...}` values in observation expectations: ASCII integers, + - *, parentheses, two names.

Bounded: at most MAX_LENGTH characters and MAX_DEPTH nested parentheses/unary minuses, so hostile
manifests fail validation with ValueError instead of RecursionError.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

NAMES = ("planned_journeys", "seed")
MAX_LENGTH = 200
MAX_DEPTH = 32
_TOKEN = re.compile(r"\s*(?:([0-9]+)|([A-Za-z_][A-Za-z0-9_]*)|(\S))")


def is_expression(value: object) -> bool:
    return isinstance(value, str) and value.startswith("${") and value.endswith("}")


def evaluate(text: str, variables: Mapping[str, int]) -> int:
    """Recursive-descent evaluation; never uses eval. Raises ValueError naming the problem."""
    if len(text) > MAX_LENGTH:
        raise ValueError(f"expression {text[:40]!r}... is longer than {MAX_LENGTH} characters")
    tokens = [match.groups() for match in _TOKEN.finditer(text[2:-1]) if any(match.groups())]
    position = 0

    def peek() -> str | None:
        return next((part for part in tokens[position] if part), None) if position < len(tokens) else None

    def take() -> tuple:
        nonlocal position
        if position >= len(tokens):
            raise ValueError(f"expression {text!r} ends early")
        position += 1
        return tokens[position - 1]

    def factor(depth: int = 0) -> int:
        if depth > MAX_DEPTH:
            raise ValueError(f"expression {text!r} nests deeper than {MAX_DEPTH}")
        number, name, symbol = take()
        if number:
            return int(number)
        if name:
            if name not in NAMES:
                raise ValueError(f"expression {text!r} uses unknown name {name!r}; allowed: {', '.join(NAMES)}")
            if name not in variables:
                raise ValueError(f"expression {text!r} has no value for {name!r}; pass the plan variables")
            return variables[name]
        if symbol == "-":
            return -factor(depth + 1)
        if symbol == "(":
            value = expr(depth + 1)
            if take()[2] != ")":
                raise ValueError(f"expression {text!r} has an unclosed parenthesis")
            return value
        raise ValueError(f"expression {text!r} has unexpected {symbol!r}")

    def term(depth: int) -> int:
        value = factor(depth)
        while peek() == "*":
            take()
            value *= factor(depth)
        return value

    def expr(depth: int = 0) -> int:
        value = term(depth)
        while peek() in {"+", "-"}:
            value = value + term(depth) if take()[2] == "+" else value - term(depth)
        return value

    value = expr()
    if position != len(tokens):
        raise ValueError(f"expression {text!r} has unexpected {peek()!r}")
    return value

