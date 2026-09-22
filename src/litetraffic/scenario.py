from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from litetraffic.models import ScenarioManifest

# ponytail: regex scan, not a JS parser; a relative specifier inside a comment or string is
# also followed, and computed import paths are not. Swap for a real parser if that bites.
_RELATIVE_IMPORT = re.compile(r"""(?:\bfrom|\bimport\s*\(?|\brequire\s*\()\s*["'](\.\.?/[^"'\n]+)["']""")


class ScenarioError(ValueError):
    """A scenario bundle is structurally unsafe or exceeds its budgets."""


@dataclass(frozen=True)
class ScenarioBundle:
    root: Path
    manifest_path: Path
    script_path: Path
    manifest: ScenarioManifest
    manifest_data: dict
    files: dict[str, str]  # relative path -> sha256, for the script and its relative import closure
    digest: str  # sha256 over the canonical manifest JSON plus every file hash


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
    files = _import_closure(root, script_path)
    digest = hashlib.sha256(json.dumps(raw, sort_keys=True, separators=(",", ":")).encode())
    for name, file_sha in sorted(files.items()):
        digest.update(f"\n{name}\0{file_sha}".encode())

    extra_requests = int(manifest.observation is not None) + 2 * int(manifest.fixtures.owned_http is not None)
    if manifest.maximum_journey_requests + extra_requests > manifest.budgets.max_requests:
        raise ScenarioError(
            f"request budget {manifest.budgets.max_requests} is below the "
            f"journey and lifecycle maximum {manifest.maximum_journey_requests + extra_requests}"
        )
    extra_writes = 2 * int(manifest.fixtures.owned_http is not None)
    if manifest.maximum_journey_writes + extra_writes > manifest.budgets.max_write_attempts:
        raise ScenarioError(
            f"write budget {manifest.budgets.max_write_attempts} is below the "
            f"journey and lifecycle maximum {manifest.maximum_journey_writes + extra_writes}"
        )

    return ScenarioBundle(root, manifest_path, script_path, manifest, raw, files, digest.hexdigest())


def _import_closure(root: Path, script_path: Path) -> dict[str, str]:
    """Map each file reachable from the script via relative imports to its sha256."""
    files: dict[str, str] = {}
    pending = [script_path]
    while pending:
        path = pending.pop()
        name = path.relative_to(root).as_posix()
        if name in files:
            continue
        data = path.read_bytes()
        files[name] = hashlib.sha256(data).hexdigest()
        for specifier in _RELATIVE_IMPORT.findall(data.decode("utf-8", errors="replace")):
            imported = (path.parent / specifier).resolve()
            if not imported.is_relative_to(root):
                raise ScenarioError(f"import {specifier!r} in {name} must stay inside the scenario directory")
            if not imported.is_file():
                raise ScenarioError(f"import {specifier!r} in {name} does not exist: {imported}")
            pending.append(imported)
    return files
