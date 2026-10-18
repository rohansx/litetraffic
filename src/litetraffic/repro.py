"""Standalone bash repro for a failing run (placeholder: the full synthesis lands in the next stage)."""

from __future__ import annotations

from pathlib import Path

from litetraffic.auth import secret_env_names


def synthesize(evidence, scenario_dir: Path | None, scrubber) -> str:
    names = sorted(secret_env_names(evidence.manifest)) if evidence.manifest else []
    guards = [f': "${{{name}:?set {name}}}"' for name in names]
    lines = ["#!/usr/bin/env bash", "set -euo pipefail", ': "${TARGET:?set TARGET to the app base URL}"', *guards,
             f"# run {evidence.run_id}: the cause and effect checks are not generated yet", "exit 2", ""]
    return scrubber.text("\n".join(lines))
