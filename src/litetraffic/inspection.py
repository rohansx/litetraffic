"""The `inspect` payload for a scenario bundle: what it would do, without running anything. Shared by the CLI and the console."""

from __future__ import annotations

from litetraffic.auth import secret_env_names
from litetraffic.models import resolve_expected
from litetraffic.scenario import ScenarioBundle


def inspect_payload(bundle: ScenarioBundle, seed: int = 0) -> dict:
    manifest = bundle.manifest
    resolved_schedule = manifest.schedule.resolve(seed)
    owned, observations = manifest.fixtures.owned_http, manifest.observations
    plan = {"planned_journeys": manifest.planned_journeys, "seed": seed}
    observation = observations[0] if observations else None
    payload = {
        "ok": True,
        "name": manifest.name,
        "schema_version": manifest.schema_version,
        "script": str(bundle.script_path),
        "scenario_sha256": bundle.digest,
        "planned_journeys": manifest.planned_journeys,
        "maximum_journey_requests": manifest.maximum_journey_requests,
        "maximum_observation_requests": sum(o.max_requests for o in observations),
        "maximum_journey_writes": manifest.maximum_journey_writes,
        "resolved_schedule": [phase.model_dump(exclude={"admitted_journeys"}) for phase in resolved_schedule],
        "assertions": manifest.assertions,
        "stages": {journey.name: journey.stages for journey in manifest.journeys if journey.stages},
        "actors": [actor.model_dump(by_alias=True, exclude_none=True) for actor in manifest.actors],
        "budgets": manifest.budgets.model_dump(),
        "fixture": {
            "recipe": manifest.fixtures.recipe,
            "owned_http": owned and owned.model_dump(include={"create_path", "delete_path"}),
            "command": manifest.fixtures.command
            and manifest.fixtures.command.model_dump() | {"hashed_files": list(bundle.command_files)},
        },
        # Names only: the environment is never read here.
        "secret_env": sorted(secret_env_names(manifest)),
        "observer": manifest.observer,
        "allowed_origins_env": manifest.allowed_origins_env,  # names only, like secret_env
        # observation_path/observation_expected describe the first observation; `observations` lists every one.
        "observation_path": observation and observation.path,
        "observation_expected": observation and resolve_expected(observation.expected, plan),
        "observations": [
            {"assertion": item.assertion, "path": item.path, "origin_env": item.origin_env, "expected": resolve_expected(item.expected, plan)}
            for item in observations
        ],
    }
    return payload
