from __future__ import annotations

import math
import random
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, computed_field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Actor(StrictModel):
    actor_class: str = Field(alias="class", min_length=1)
    count: int = Field(gt=0)
    auth_recipe: str = Field(min_length=1)


class Fixtures(StrictModel):
    recipe: str = Field(min_length=1)
    parameters: dict[str, JsonValue] = Field(default_factory=dict)


class Journey(StrictModel):
    name: str = Field(min_length=1)
    max_requests: int = Field(gt=0)
    max_writes: int = Field(ge=0)


class Phase(StrictModel):
    name: str = Field(min_length=1)
    seconds: int = Field(gt=0)
    rate: int = Field(gt=0)

    @computed_field
    @property
    def admitted_journeys(self) -> int:
        return math.ceil(self.seconds * self.rate)


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
        rates = [self.baseline_rate] * self.duration_seconds
        generator = random.Random(seed)
        for index in range(self.spikes):
            window_start = index * self.duration_seconds // self.spikes
            window_end = (index + 1) * self.duration_seconds // self.spikes
            start = generator.randint(window_start, window_end - self.spike_seconds)
            rates[start : start + self.spike_seconds] = [self.spike_rate] * self.spike_seconds

        phases: list[Phase] = []
        start = 0
        for second in range(1, len(rates) + 1):
            if second == len(rates) or rates[second] != rates[start]:
                kind = "spike" if rates[start] == self.spike_rate else "baseline"
                phases.append(Phase(name=f"{kind}-{len(phases) + 1}", seconds=second - start, rate=rates[start]))
                start = second
        return phases


class Schedule(StrictModel):
    unit: str
    phases: list[Phase] | None = Field(default=None, min_length=1)
    profile: SpikyProfile | None = None

    @model_validator(mode="after")
    def require_journey_rate_unit(self) -> "Schedule":
        if self.unit != "journeys_per_second":
            raise ValueError("schedule unit must be journeys_per_second")
        if (self.phases is None) == (self.profile is None):
            raise ValueError("schedule must define exactly one of phases or profile")
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
    budgets: Budgets

    @model_validator(mode="after")
    def require_supported_version_and_duration(self) -> "ScenarioManifest":
        if self.schema_version != 1:
            raise ValueError("schema_version must be 1")
        scheduled_seconds = sum(phase.seconds for phase in self.schedule.resolve(seed=0))
        if scheduled_seconds > self.budgets.max_seconds:
            raise ValueError("scheduled duration exceeds max_seconds budget")
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
