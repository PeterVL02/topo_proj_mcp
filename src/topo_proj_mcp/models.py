"""Module for defining data structures and models."""

from dataclasses import dataclass, field
from enum import StrEnum


class Status(StrEnum):
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class PriorityLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


PRIORITY_MAPPING: dict[PriorityLevel, float] = {
    PriorityLevel.LOW: 1.0,
    PriorityLevel.MEDIUM: 2.0,
    PriorityLevel.HIGH: 3.0,
}


@dataclass
class Task:
    name: str
    description: str
    priority: PriorityLevel
    estimated_time: float
    status: Status = Status.NOT_STARTED
    depends_on: set[str] = field(default_factory=set)

    @property
    def base_value(self) -> float:
        """Return the undiscounted reward for finishing this task."""
        return PRIORITY_MAPPING[self.priority]
