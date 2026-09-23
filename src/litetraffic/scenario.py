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
    pool: list | None = None  # the fixtures.pool file's items
    command_files: tuple[str, ...] = ()  # fixtures.command inputs plus argv elements naming bundle files, all hashed


def check_pool(pool: object, planned_journeys: int) -> None:
    if not isinstance(pool, list):
        raise ScenarioError("fixture pool must be a JSON array")
    if len(pool) < planned_journeys:
        raise ScenarioError(f"fixture pool has {len(pool)} items but {planned_journeys} journeys are planned")


def _bundle_file(root: Path, name: str, what: str) -> Path:
    path = (root / name).resolve()
    if not path.is_relative_to(root):
        raise ScenarioError(f"{what} must stay inside the scenario directory")
    if not path.is_file():
        raise ScenarioError(f"{what} does not exist: {path}")
    return path


def _load_pool(root: Path, name: str, planned_journeys: int) -> tuple[str, bytes, list]:
    path = _bundle_file(root, name, "fixture pool")
    data = path.read_bytes()
    try:
        pool = json.loads(data)
    except ValueError as exc:
        raise ScenarioError(f"fixture pool is not valid JSON: {exc}") from exc
    check_pool(pool, planned_journeys)
    return path.relative_to(root).as_posix(), data, pool


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
    pool = None
    if manifest.fixtures.pool:
        name, data, pool = _load_pool(root, manifest.fixtures.pool, manifest.planned_journeys)
        files[name] = hashlib.sha256(data).hexdigest()
    command_files = _command_files(root, manifest.fixtures.command) if manifest.fixtures.command else ()
    for name in command_files:
        files[name] = hashlib.sha256((root / name).read_bytes()).hexdigest()
    digest = hashlib.sha256(json.dumps(raw, sort_keys=True, separators=(",", ":")).encode())
    for name, file_sha in sorted(files.items()):
        digest.update(f"\n{name}\0{file_sha}".encode())

    extra_requests = sum(observation.max_requests for observation in manifest.observations) + 2 * int(manifest.fixtures.owned_http is not None)
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

    return ScenarioBundle(root, manifest_path, script_path, manifest, raw, files, digest.hexdigest(), pool, command_files)


def _command_files(root: Path, command) -> tuple[str, ...]:
    """Declared inputs plus every setup/teardown argv element that names an existing file in the bundle."""
    names = {_bundle_file(root, name, "fixture input").relative_to(root).as_posix() for name in command.inputs}
    # ponytail: whole-element match only; `--file=seed.sql` style args must be listed in `inputs`.
    for arg in (*command.setup, *command.teardown):
        path = (root / arg).resolve()
        if path.is_relative_to(root) and path.is_file():
            names.add(path.relative_to(root).as_posix())
    return tuple(sorted(names))


def _import_closure(root: Path, script_path: Path) -> dict[str, str]:
    """Map each file reachable from the script via relative imports to its sha256; reject remote/extension imports."""
    files: dict[str, str] = {}
    pending = [script_path]
    while pending:
        path = pending.pop()
        name = path.relative_to(root).as_posix()
        if name in files:
            continue
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise ScenarioError(f"cannot read import {name}: {exc}") from exc
        files[name] = hashlib.sha256(data).hexdigest()
        for specifier in _IMPORT.findall(data.decode("utf-8", errors="replace")):
            if specifier.startswith("/") or specifier.lower().startswith("file:"):
                raise ScenarioError(
                    f"import {specifier!r} in {name} is not allowed: absolute paths, file: URLs and "
                    "protocol-relative specifiers are rejected; use a ./ or ../ path inside the scenario directory"
                )
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
