"""Plan-aware `${...}` values in observation expectations: integers, + - *, parentheses, two names."""

from __future__ import annotations

import re
from collections.abc import Mapping

NAMES = ("planned_journeys", "seed")
_TOKEN = re.compile(r"\s*(?:(\d+)|([A-Za-z_]\w*)|(\S))")


def is_expression(value: object) -> bool:
    return isinstance(value, str) and value.startswith("${") and value.endswith("}")


def evaluate(text: str, variables: Mapping[str, int]) -> int:
    """Recursive-descent evaluation; never uses eval. Raises ValueError naming the problem."""
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

    def factor() -> int:
        number, name, symbol = take()
        if number:
            return int(number)
        if name:
            if name not in NAMES:
                raise ValueError(f"expression {text!r} uses unknown name {name!r}; allowed: {', '.join(NAMES)}")
            return variables[name]
        if symbol == "-":
            return -factor()
        if symbol == "(":
            value = expr()
            if take()[2] != ")":
                raise ValueError(f"expression {text!r} has an unclosed parenthesis")
            return value
        raise ValueError(f"expression {text!r} has unexpected {symbol!r}")

    def term() -> int:
        value = factor()
        while peek() == "*":
            take()
            value *= factor()
        return value

    def expr() -> int:
        value = term()
        while peek() in {"+", "-"}:
            value = value + term() if take()[2] == "+" else value - term()
        return value

    value = expr()
    if position != len(tokens):
        raise ValueError(f"expression {text!r} has unexpected {peek()!r}")
    return value

