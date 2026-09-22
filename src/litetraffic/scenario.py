from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from litetraffic.models import ScenarioManifest


class ScenarioError(ValueError):
    """A scenario bundle is structurally unsafe or exceeds its budgets."""


@dataclass(frozen=True)
class ScenarioBundle:
    root: Path
    manifest_path: Path
    script_path: Path
    manifest: ScenarioManifest


def load_scenario(path: Path) -> ScenarioBundle:
    root = path.resolve()
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        raise ScenarioError(f"manifest does not exist: {manifest_path}")

    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ScenarioError(f"cannot read manifest: {exc}") from exc

    manifest = ScenarioManifest.model_validate(raw)
    script_path = (root / manifest.script).resolve()
    if not script_path.is_relative_to(root):
        raise ScenarioError("scenario script must stay inside the scenario directory")
    if not script_path.is_file():
        raise ScenarioError(f"scenario script does not exist: {script_path}")

    if manifest.maximum_journey_requests + int(manifest.observation is not None) > manifest.budgets.max_requests:
        raise ScenarioError(
            f"request budget {manifest.budgets.max_requests} is below the "
            f"journey and observation maximum {manifest.maximum_journey_requests + int(manifest.observation is not None)}"
        )
    if manifest.maximum_journey_writes > manifest.budgets.max_write_attempts:
        raise ScenarioError(
            f"write budget {manifest.budgets.max_write_attempts} is below the "
            f"journey maximum {manifest.maximum_journey_writes}"
        )

    return ScenarioBundle(root, manifest_path, script_path, manifest)
