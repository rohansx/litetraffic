"""Declared actor auth recipes: HS256 JWTs minted by the controller and handed to k6 by env var."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from collections.abc import Iterable, Mapping

REDACTED = "[redacted]"
# Shorter values are too likely to collide with ordinary evidence text (`true`, ids) to substring-redact.
MIN_SECRET_LENGTH = 8


class AuthError(Exception):
    pass


def token_env_name(actor_class: str) -> str:
    return "LT_TOKEN_" + re.sub(r"[^A-Z0-9]+", "_", actor_class.upper()).strip("_")


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _substitute(value, names: dict[str, str]):
    if isinstance(value, str):
        return re.sub(r"\$\{(run_id|actor_index)\}", lambda match: names[match.group(1)], value)
    if isinstance(value, dict):
        return {key: _substitute(item, names) for key, item in value.items()}
    if isinstance(value, list):
        return [_substitute(item, names) for item in value]
    return value


def jwt_hs256(claims: dict, secret: str) -> str:
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = _b64(json.dumps(claims, separators=(",", ":")).encode())
    signature = hmac.new(secret.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest()
    return f"{header}.{payload}.{_b64(signature)}"


# Linux MAX_ARG_STRLEN: one NAME=value env string above this makes exec fail with "Argument list too long".
MAX_ENV_STRING_BYTES = 128 * 1024


def mint_tokens(actors: Iterable, run_id: str, environ: Mapping[str, str], now: int | None = None) -> dict[str, str]:
    """One token per actor with `auth`, keyed by LT_TOKEN_<CLASS>; `per_identity` actors also get
    LT_TOKENS_<CLASS>, a JSON array of `count` tokens. `iat`/`exp` override declared claims."""
    issued = int(time.time()) if now is None else now
    tokens = {}
    for index, actor in enumerate(actors):
        if actor.auth is None:
            continue
        secret = environ.get(actor.auth.secret_env)
        if not secret:
            raise AuthError(f"auth secret env {actor.auth.secret_env} missing")
        if len(secret) < MIN_SECRET_LENGTH:
            raise AuthError(f"auth secret env {actor.auth.secret_env} is shorter than {MIN_SECRET_LENGTH} characters")
        indexes = range(actor.count) if actor.auth.per_identity else [index]
        minted = [
            jwt_hs256(
                _substitute(actor.auth.claims, {"run_id": run_id, "actor_index": str(identity)})
                | {"iat": issued, "exp": issued + actor.auth.ttl_seconds},
                secret,
            )
            for identity in indexes
        ]
        name = token_env_name(actor.actor_class)
        tokens[name] = minted[0]
        if actor.auth.per_identity:
            array = json.dumps(minted)
            if len(f"{tokens_env_name(name)}={array}") >= MAX_ENV_STRING_BYTES:
                raise AuthError(
                    f"{tokens_env_name(name)} is {len(array)} bytes, over the {MAX_ENV_STRING_BYTES}-byte environment variable limit; lower the actor count or shrink the claims"
                )
            tokens[tokens_env_name(name)] = array
    return tokens


def tokens_env_name(token_env: str) -> str:
    return "LT_TOKENS_" + token_env.removeprefix("LT_TOKEN_")


def minted_tokens(tokens: Mapping[str, str]) -> set[str]:
    """Every individual token in mint_tokens output, unpacking the LT_TOKENS_ arrays."""
    return {item for name, value in tokens.items() for item in (json.loads(value) if name.startswith("LT_TOKENS_") else [value])}


def secret_env_names(manifest) -> set[str]:
    """Every environment variable a manifest declares as a credential: signing keys, bearer tokens, headers and `secret_env`."""
    owned, observations = manifest.fixtures.owned_http, manifest.observations
    return (
        {ref for ref in (owned and owned.bearer_token_env, *(item.bearer_token_env for item in observations)) if ref}
        | {env for item in observations for env in item.headers_env.values()}
        | {actor.auth.secret_env for actor in manifest.actors if actor.auth}
        | set(manifest.secret_env)
    )


def secret_values(manifest, environ: Mapping[str, str], tokens: Mapping[str, str]) -> list[str]:
    """Every declared credential value and minted token, longest first, for scrubbing anything that is kept."""
    secrets = {environ.get(name, "") for name in secret_env_names(manifest)} | minted_tokens(tokens)
    return sorted(filter(None, secrets), key=len, reverse=True)


# Go's encoding/json (k6's metrics output) also escapes these; other \\uXXXX spellings are left to redact_value on decoded JSON.
GO_JSON_ESCAPES = str.maketrans({char: f"\\u{ord(char):04x}" for char in "<>&\u2028\u2029"})


def redact(text: str, secrets: Iterable[str]) -> str:
    """Scrub raw text: each secret literally and as it appears inside a JSON string (Python ASCII-escaped or not, or Go's encoding/json)."""
    for secret in secrets:
        if len(secret) >= MIN_SECRET_LENGTH:
            raw = json.dumps(secret, ensure_ascii=False)[1:-1]
            for form in (secret, json.dumps(secret)[1:-1], raw, raw.translate(GO_JSON_ESCAPES)):
                text = text.replace(form, REDACTED)
    return text


def redact_value(value, secrets: Iterable[str]):
    """A copy of decoded JSON with every string (keys included) scrubbed; escaping cannot hide a secret here."""
    if isinstance(value, str):
        return redact(value, secrets)
    if isinstance(value, dict):
        return {redact_value(key, secrets): redact_value(item, secrets) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_value(item, secrets) for item in value]
    return value
