from __future__ import annotations

import math
import random
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, computed_field, model_validator

from litetraffic.expression import NAMES, evaluate, is_expression
from litetraffic.target import validate_target


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Actor(StrictModel):
    actor_class: str = Field(alias="class", min_length=1)
    count: int = Field(gt=0)
    auth_recipe: str = Field(min_length=1)


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


class Fixtures(StrictModel):
    recipe: str = Field(min_length=1)
    parameters: dict[str, JsonValue] = Field(default_factory=dict)
    owned_http: OwnedHttpFixture | None = None
    command: CommandFixture | None = None

    @model_validator(mode="after")
    def require_one_lifecycle(self) -> "Fixtures":
        if self.owned_http and self.command:
            raise ValueError("fixtures.owned_http and fixtures.command are mutually exclusive")
        return self

    @property
    def reserved_seconds(self) -> int:
        """Seconds of max_seconds held back from k6 for fixture setup and cleanup."""
        if self.command:
            return 2 * self.command.timeout_seconds
        return 10 if self.owned_http else 0


MATCHER_KEYS = {"eq", "gte", "lte", "len", "exists"}
ENV_NAME = r"[A-Z_][A-Z0-9_]*"


def as_matcher(expected: JsonValue) -> dict[str, JsonValue]:
    """A dict whose keys are all matcher keys is a matcher; any other value means equality."""
    if isinstance(expected, dict) and expected and set(expected) <= MATCHER_KEYS:
        return expected
    return {"eq": expected}


def resolve_expected(expected: dict[str, JsonValue], variables: dict[str, int]) -> dict[str, JsonValue]:
    """Evaluate `${...}` literals and matcher operands; every other value is kept as written."""
    resolved = {}
    for pointer, value in expected.items():
        matcher = as_matcher(value)
        (op, operand), = matcher.items()  # validation already rejected multi-key matchers
        if is_expression(operand):
            operand = evaluate(operand, variables)
            value = operand if matcher is not value else {op: operand}
        resolved[pointer] = value
    return resolved


def _check_matcher(pointer: str, matcher: dict[str, JsonValue]) -> None:
    if len(matcher) != 1:
        raise ValueError(f"{pointer}: matcher must have exactly one of {sorted(MATCHER_KEYS)}")
    (op, operand), number = next(iter(matcher.items())), (int, float)
    if op != "exists" and is_expression(operand):
        return
    if op in {"gte", "lte"} and (isinstance(operand, bool) or not isinstance(operand, number)):
        raise ValueError(f"{pointer}: {op} matcher needs a number")
    if op == "len" and (isinstance(operand, bool) or not isinstance(operand, int) or operand < 0):
        raise ValueError(f"{pointer}: len matcher needs a non-negative integer")
    if op == "exists" and not isinstance(operand, bool):
        raise ValueError(f"{pointer}: exists matcher needs true or false")


class FinalObservation(StrictModel):
    path: str = Field(min_length=1)
    assertion: str = Field(min_length=1)
    expected: dict[str, JsonValue] = Field(min_length=1)
    bearer_token_env: str | None = None
    origin: str | None = None
    headers_env: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_safe_read(self) -> "FinalObservation":
        if not self.path.startswith("/") or self.path.startswith("//") or "#" in self.path:
            raise ValueError("observation path must be a same-origin relative path")
        if any(pointer and not pointer.startswith("/") for pointer in self.expected):
            raise ValueError("expected keys must be JSON Pointers")
        for pointer, value in self.expected.items():
            if as_matcher(value) is value:
                _check_matcher(pointer, value)
        try:
            resolve_expected(self.expected, dict.fromkeys(NAMES, 0))
        except ValueError as exc:
            raise ValueError(f"expected {exc}") from None
        if self.bearer_token_env and not re.fullmatch(ENV_NAME, self.bearer_token_env):
            raise ValueError("bearer_token_env must name an uppercase environment variable")
        for header, env in self.headers_env.items():
            if not re.fullmatch(r"[A-Za-z0-9!#$%&'*+.^_`|~-]+", header):
                raise ValueError(f"headers_env key {header!r} is not a valid header name")
            if not re.fullmatch(ENV_NAME, env):
                raise ValueError(f"headers_env {header} must name an uppercase environment variable")
        if self.origin is not None:
            self.origin = validate_target(self.origin, "observation origin")
        return self


class Journey(StrictModel):
    name: str = Field(min_length=1)
    max_requests: int = Field(gt=0)
    max_writes: int = Field(ge=0)


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
    allowed_origins: list[str] = Field(default_factory=list)
    budgets: Budgets

    @model_validator(mode="after")
    def require_supported_version_and_duration(self) -> "ScenarioManifest":
        if self.schema_version != 1:
            raise ValueError("schema_version must be 1")
        scheduled_seconds = sum(phase.seconds for phase in self.schedule.resolve(seed=0))
        if scheduled_seconds > self.budgets.max_seconds:
            raise ValueError("scheduled duration exceeds max_seconds budget")
        fixture_seconds = self.fixtures.reserved_seconds
        if fixture_seconds and scheduled_seconds + fixture_seconds > self.budgets.max_seconds:
            raise ValueError(f"scheduled duration plus {fixture_seconds}-second fixture deadline exceeds max_seconds budget")
        if self.observation and scheduled_seconds + fixture_seconds + 5 > self.budgets.max_seconds:
            raise ValueError("scheduled duration plus fixture and 5-second observation deadline exceeds max_seconds budget")
        if self.observation and self.observation.assertion not in self.assertions:
            raise ValueError("observation assertion must be declared in assertions")
        self.allowed_origins = [validate_target(origin, "allowed_origins entry") for origin in self.allowed_origins]
        if self.observation and self.observation.origin and self.observation.origin not in self.allowed_origins:
            raise ValueError(f"observation origin {self.observation.origin} must be listed in allowed_origins")
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
