from __future__ import annotations

import math

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
    rate: float = Field(gt=0)

    @computed_field
    @property
    def admitted_journeys(self) -> int:
        return math.ceil(self.seconds * self.rate)


class Schedule(StrictModel):
    unit: str
    phases: list[Phase] = Field(min_length=1)

    @model_validator(mode="after")
    def require_journey_rate_unit(self) -> "Schedule":
        if self.unit != "journeys_per_second":
            raise ValueError("schedule unit must be journeys_per_second")
        return self


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
        scheduled_seconds = sum(phase.seconds for phase in self.schedule.phases)
        if scheduled_seconds > self.budgets.max_seconds:
            raise ValueError("scheduled duration exceeds max_seconds budget")
        return self

    @computed_field
    @property
    def planned_journeys(self) -> int:
        return sum(phase.admitted_journeys for phase in self.schedule.phases)

    @computed_field
    @property
    def maximum_journey_requests(self) -> int:
        return self.planned_journeys * max(journey.max_requests for journey in self.journeys)

    @computed_field
    @property
    def maximum_journey_writes(self) -> int:
        return self.planned_journeys * max(journey.max_writes for journey in self.journeys)
