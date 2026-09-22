from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from litetraffic.models import ScenarioManifest

# ponytail: regex scan, not a JS parser; a specifier inside a comment or string is also
# checked, and computed import paths are not. Swap for a real parser if that bites.
RUNTIME_PATH = "litetraffic/runtime.js"  # where scripts import the bundled helper from
RUNTIME_SOURCE = Path(__file__).with_name("k6") / "runtime.js"
_IMPORT = re.compile(r"""(?:\bfrom|\bimport\s*\(?|\brequire\s*\()\s*["']([^"'\n]+)["']""")


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
    """Map each file reachable from the script via relative imports to its sha256; reject remote/extension imports."""
    files: dict[str, str] = {}
    pending = [script_path]
    while pending:
        path = pending.pop()
        name = path.relative_to(root).as_posix()
        if name in files:
            continue
        data = path.read_bytes()
        files[name] = hashlib.sha256(data).hexdigest()
        for specifier in _IMPORT.findall(data.decode("utf-8", errors="replace")):
            if "://" in specifier or specifier.startswith("k6/x/"):
                raise ScenarioError(
                    f"import {specifier!r} in {name} is not allowed: remote modules and k6/x extensions are rejected"
                )
            if not specifier.startswith(("./", "../")):
                continue  # k6 built-ins such as "k6/http"
            imported = (path.parent / specifier).resolve()
            if not imported.is_relative_to(root):
                raise ScenarioError(f"import {specifier!r} in {name} must stay inside the scenario directory")
            if imported == root / RUNTIME_PATH:
                if imported.exists():
                    raise ScenarioError(f"{RUNTIME_PATH} is reserved for the bundled LiteTraffic runtime helper")
                files[RUNTIME_PATH] = hashlib.sha256(RUNTIME_SOURCE.read_bytes()).hexdigest()
                continue
            if not imported.is_file():
                raise ScenarioError(f"import {specifier!r} in {name} does not exist: {imported}")
            pending.append(imported)
    return files


@contextmanager
def staged(bundle: ScenarioBundle) -> Iterator[Path]:
    """Yield a temporary copy of the scenario directory with the bundled runtime helper added."""
    with tempfile.TemporaryDirectory(prefix="litetraffic-") as tmp:
        root = Path(tmp) / "scenario"
        shutil.copytree(bundle.root, root)
        if RUNTIME_PATH in bundle.files:
            (root / RUNTIME_PATH).parent.mkdir(exist_ok=True)
            shutil.copyfile(RUNTIME_SOURCE, root / RUNTIME_PATH)
        yield root
