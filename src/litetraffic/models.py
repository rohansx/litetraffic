from __future__ import annotations

import math
import random
import re
from typing import Annotated, Literal

from pydantic import Field, JsonValue, computed_field, model_validator

from litetraffic.auth import token_env_name
from litetraffic.modelbase import ENV_NAME, StrictModel
from litetraffic.observation_config import FinalObservation, as_matcher, resolve_expected  # noqa: F401 (re-exported)
from litetraffic.process import STOP_GRACE_SECONDS
from litetraffic.target import normalize_origin

AUTH_PLACEHOLDER = re.compile(r"\$\{([^}]*)\}")
AUTH_NAMES = {"run_id", "actor_index"}


def _strings(value: JsonValue):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict | list):
        for item in value.values() if isinstance(value, dict) else value:
            yield from _strings(item)


# ponytail: fixed headroom for k6 start-up and the last in-flight journeys; a share equal to the
# schedule always times out. Make it per-scenario if slow journeys need more.
ENGINE_SLACK_SECONDS = 2

class JwtAuth(StrictModel):
    kind: Literal["jwt_hs256"]
    secret_env: str
    claims: dict[str, JsonValue] = Field(default_factory=dict)
    ttl_seconds: int = Field(gt=0, le=86400)

    @model_validator(mode="after")
    def require_env_and_known_placeholders(self) -> "JwtAuth":
        if not re.fullmatch(ENV_NAME, self.secret_env):
            raise ValueError("secret_env must name an uppercase environment variable")
        for name in (name for text in _strings(self.claims) for name in AUTH_PLACEHOLDER.findall(text)):
            if name not in AUTH_NAMES:
                raise ValueError(f"claims use unknown placeholder ${{{name}}}; allowed: ${{run_id}}, ${{actor_index}}")
        return self


class Actor(StrictModel):
    actor_class: str = Field(alias="class", min_length=1)
    count: int = Field(gt=0)
    auth_recipe: str = Field(min_length=1)
    auth: JwtAuth | None = None


class OwnedHttpFixture(StrictModel):
    create_path: str = Field(min_length=1)
    delete_path: str = Field(min_length=1)
    id_pointer: str = Field(min_length=1)
    create_body: dict[str, JsonValue] = Field(default_factory=dict)
    bearer_token_env: str | None = None

    @model_validator(mode="after")
    def require_scoped_paths(self) -> "OwnedHttpFixture":
        if not self.create_path.startswith("/") or self.create_path.startswith("//") or any(char in self.create_path for char in "?#"):
            raise ValueError("create_path must be a same-origin path")
        if (
            not self.delete_path.startswith("/")
            or self.delete_path.startswith("//")
            or any(char in self.delete_path for char in "?#%")
            or self.delete_path.count("{fixture_id}") != 1
            or not self.delete_path.endswith("/{fixture_id}")
            or any(part in {".", ".."} for part in self.delete_path.split("/"))
        ):
            raise ValueError("delete_path must be a same-origin path ending in /{fixture_id}")
        if not self.id_pointer.startswith("/"):
            raise ValueError("id_pointer must be a JSON Pointer")
        if self.bearer_token_env and not re.fullmatch(r"[A-Z_][A-Z0-9_]*", self.bearer_token_env):
            raise ValueError("bearer_token_env must name an uppercase environment variable")
        return self


Argv = list[Annotated[str, Field(min_length=1)]]


class CommandFixture(StrictModel):
    setup: Argv = Field(min_length=1)
    teardown: Argv = Field(min_length=1)
    timeout_seconds: int = Field(gt=0, le=60)
    cwd: Literal["bundle"] = "bundle"
    inputs: list[Annotated[str, Field(min_length=1)]] = Field(default_factory=list)  # bundle-relative files hashed into the digest


class Fixtures(StrictModel):
    recipe: str = Field(min_length=1)
    parameters: dict[str, JsonValue] = Field(default_factory=dict)
    owned_http: OwnedHttpFixture | None = None
    command: CommandFixture | None = None
    pool: str | None = Field(default=None, min_length=1)  # bundle-relative JSON array file, one item per journey

    @model_validator(mode="after")
    def require_one_lifecycle(self) -> "Fixtures":
        if self.owned_http and self.command:
            raise ValueError("fixtures.owned_http and fixtures.command are mutually exclusive")
        return self

    @property
    def reserved_seconds(self) -> int:
        """Seconds of max_seconds held back from k6 for fixture setup and cleanup."""
        if self.command:  # setup and teardown may each run to their timeout and then be stopped
            return 2 * (self.command.timeout_seconds + STOP_GRACE_SECONDS)
        return 10 if self.owned_http else 0


class Journey(StrictModel):
    name: str = Field(min_length=1)
    max_requests: int = Field(gt=0)
    max_writes: int = Field(ge=0)
    # operation tag -> requests that must be observed in flight together for the run to count
    min_overlap: dict[Annotated[str, Field(min_length=1)], Annotated[int, Field(gt=0)]] = Field(default_factory=dict)
    # operation tag -> HTTP statuses that are an intended outcome, not a failure, for unexpected_http_failure_rate
    expected_statuses: dict[
        Annotated[str, Field(min_length=1)], Annotated[list[Annotated[int, Field(strict=True, ge=100, le=599)]], Field(min_length=1)]
    ] = Field(default_factory=dict)


class Phase(StrictModel):
    name: str = Field(min_length=1)
    seconds: int = Field(gt=0)
    rate: int = Field(ge=0)

    @computed_field
    @property
    def admitted_journeys(self) -> int:
        return math.ceil(self.seconds * self.rate)


def _compile_profile(
    duration_seconds: int,
    quiet_rate: int,
    burst_rate: int,
    burst_seconds: int,
    bursts: int,
    seed: int,
    quiet_name: str,
    burst_name: str,
) -> list[Phase]:
    rates = [quiet_rate] * duration_seconds
    generator = random.Random(seed)
    for index in range(bursts):
        window_start = index * duration_seconds // bursts
        window_end = (index + 1) * duration_seconds // bursts
        start = generator.randint(window_start, window_end - burst_seconds)
        rates[start : start + burst_seconds] = [burst_rate] * burst_seconds

    phases: list[Phase] = []
    start = 0
    for second in range(1, len(rates) + 1):
        if second == len(rates) or rates[second] != rates[start]:
            name = burst_name if rates[start] == burst_rate else quiet_name
            phases.append(Phase(name=f"{name}-{len(phases) + 1}", seconds=second - start, rate=rates[start]))
            start = second
    return phases


class SpikyProfile(StrictModel):
    kind: Literal["spiky"]
    duration_seconds: int = Field(gt=0)
    baseline_rate: int = Field(gt=0)
    spike_rate: int = Field(gt=0)
    spike_seconds: int = Field(gt=0)
    spikes: int = Field(gt=0)

    @model_validator(mode="after")
    def require_bounded_spikes(self) -> "SpikyProfile":
        if self.spike_rate <= self.baseline_rate:
            raise ValueError("spike_rate must exceed baseline_rate")
        if self.duration_seconds // self.spikes < self.spike_seconds:
            raise ValueError("spikes do not fit without overlap")
        return self

    def compile(self, seed: int) -> list[Phase]:
        return _compile_profile(
            self.duration_seconds,
            self.baseline_rate,
            self.spike_rate,
            self.spike_seconds,
            self.spikes,
            seed,
            "baseline",
            "spike",
        )


class RandomBurstProfile(StrictModel):
    kind: Literal["random_bursts"]
    duration_seconds: int = Field(gt=0)
    quiet_rate: int = Field(ge=0)
    burst_rate: int = Field(gt=0)
    burst_seconds: int = Field(gt=0)
    bursts: int = Field(gt=0)

    @model_validator(mode="after")
    def require_bounded_bursts(self) -> "RandomBurstProfile":
        if self.burst_rate <= self.quiet_rate:
            raise ValueError("burst_rate must exceed quiet_rate")
        if self.duration_seconds // self.bursts < self.burst_seconds:
            raise ValueError("bursts do not fit without overlap")
        return self

    def compile(self, seed: int) -> list[Phase]:
        return _compile_profile(
            self.duration_seconds,
            self.quiet_rate,
            self.burst_rate,
            self.burst_seconds,
            self.bursts,
            seed,
            "quiet",
            "burst",
        )


class SustainedBurstProfile(StrictModel):
    kind: Literal["sustained_burst"]
    baseline_rate: int = Field(ge=0)
    plateau_rate: int = Field(gt=0)
    ramp_seconds: int = Field(gt=0)
    plateau_seconds: int = Field(gt=0)
    recovery_seconds: int = Field(gt=0)

    @model_validator(mode="after")
    def require_higher_plateau(self) -> "SustainedBurstProfile":
        if self.plateau_rate <= self.baseline_rate:
            raise ValueError("plateau_rate must exceed baseline_rate")
        return self

    def compile(self, seed: int) -> list[Phase]:
        increase = self.plateau_rate - self.baseline_rate
        phases = [
            Phase(
                name=f"ramp-{second}",
                seconds=1,
                rate=self.baseline_rate + math.ceil(increase * second / self.ramp_seconds),
            )
            for second in range(1, self.ramp_seconds + 1)
        ]
        phases.append(Phase(name="plateau", seconds=self.plateau_seconds, rate=self.plateau_rate))
        phases.append(Phase(name="recovery", seconds=self.recovery_seconds, rate=self.baseline_rate))
        return phases


class Schedule(StrictModel):
    unit: str
    phases: list[Phase] | None = Field(default=None, min_length=1)
    profile: SpikyProfile | RandomBurstProfile | SustainedBurstProfile | None = None

    @model_validator(mode="after")
    def require_journey_rate_unit(self) -> "Schedule":
        if self.unit != "journeys_per_second":
            raise ValueError("schedule unit must be journeys_per_second")
        if (self.phases is None) == (self.profile is None):
            raise ValueError("schedule must define exactly one of phases or profile")
        if not any(phase.admitted_journeys for phase in self.resolve(seed=0)):
            raise ValueError("schedule must admit at least one journey")
        return self

    def resolve(self, seed: int) -> list[Phase]:
        if self.phases is not None:
            return self.phases
        if self.profile is None:
            raise ValueError("schedule has no phases or profile")
        return self.profile.compile(seed)


class Budgets(StrictModel):
    max_seconds: int = Field(gt=0)
    max_requests: int = Field(gt=0)
    max_write_attempts: int = Field(ge=0)
    max_in_flight: int = Field(gt=0)
    max_artifact_bytes: int = Field(gt=0)


class ScenarioManifest(StrictModel):
    schema_version: int
    name: str = Field(min_length=1)
    script: str = Field(min_length=1)
    actors: list[Actor] = Field(min_length=1)
    fixtures: Fixtures
    journeys: list[Journey] = Field(min_length=1)
    schedule: Schedule
    assertions: list[str] = Field(min_length=1)
    observer: str = Field(min_length=1)
    observation: FinalObservation | None = None
    observations: list[FinalObservation] = Field(default_factory=list)  # the legacy `observation` becomes its only entry
    allowed_origins: list[str] = Field(default_factory=list)
    allowed_origins_env: str | None = None  # names a variable holding comma-separated extra origins for origin_env
    budgets: Budgets

    @model_validator(mode="after")
    def require_supported_version_and_duration(self) -> "ScenarioManifest":
        if self.schema_version != 1:
            raise ValueError("schema_version must be 1")
        if self.observation and self.observations:
            raise ValueError("use observation or observations, not both")
        if self.observation:
            self.observations = [self.observation]
        scheduled_seconds = sum(phase.seconds for phase in self.schedule.resolve(seed=0))
        fixture_seconds = self.fixtures.reserved_seconds
        observation_seconds = 5 * len(self.observations)
        needed = scheduled_seconds + ENGINE_SLACK_SECONDS + fixture_seconds + observation_seconds
        if needed > self.budgets.max_seconds:
            raise ValueError(
                f"scheduled duration ({scheduled_seconds} s) plus engine start/drain ({ENGINE_SLACK_SECONDS} s), "
                f"fixture ({fixture_seconds} s) and observation ({observation_seconds} s) deadlines need {needed} s, "
                f"max_seconds is {self.budgets.max_seconds}"
            )
        observed = [observation.assertion for observation in self.observations]
        if any(assertion not in self.assertions for assertion in observed):
            raise ValueError("observation assertion must be declared in assertions")
        if len(set(observed)) != len(observed):
            raise ValueError("observation assertions must be distinct")
        if self.allowed_origins_env is not None and not re.fullmatch(ENV_NAME, self.allowed_origins_env):
            raise ValueError("allowed_origins_env must name an uppercase environment variable")
        token_envs = [token_env_name(actor.actor_class) for actor in self.actors if actor.auth]
        if len(set(token_envs)) != len(token_envs):
            raise ValueError(f"actor classes with auth must map to distinct token variables: {', '.join(token_envs)}")
        self.allowed_origins = [normalize_origin(origin, "allowed_origins entry") for origin in self.allowed_origins]
        for observation in self.observations:
            if observation.origin and observation.origin not in self.allowed_origins:
                raise ValueError(f"observation origin {observation.origin} must be listed in allowed_origins")
        return self

    @computed_field
    @property
    def planned_journeys(self) -> int:
        return sum(phase.admitted_journeys for phase in self.schedule.resolve(seed=0))

    @computed_field
    @property
    def maximum_journey_requests(self) -> int:
        return self.planned_journeys * max(journey.max_requests for journey in self.journeys)

    @computed_field
    @property
    def maximum_journey_writes(self) -> int:
        return self.planned_journeys * max(journey.max_writes for journey in self.journeys)
