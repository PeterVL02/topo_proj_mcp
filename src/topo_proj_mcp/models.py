"""Module for defining data structures and models."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import TypedDict


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
    started_at: datetime | None = None
    completed_at: datetime | None = None
    actual_time: float | None = None

    @property
    def base_value(self) -> float:
        """Return the undiscounted reward for finishing this task."""
        return PRIORITY_MAPPING[self.priority]

    def to_json(self) -> TaskJSON:
        """Return a JSON-serialisable dict of this task."""
        return {
            "name": self.name,
            "description": self.description,
            "priority": self.priority,
            "estimated_time": self.estimated_time,
            "status": self.status,
            "depends_on": sorted(self.depends_on),
            "started_at": format_time(self.started_at),
            "completed_at": format_time(self.completed_at),
            "actual_time": self.actual_time,
        }

    @classmethod
    def from_json(cls, data: TaskJSON) -> Task:
        """Build a task from the dict produced by `to_json`."""
        return cls(
            name=data["name"],
            description=data["description"],
            priority=PriorityLevel(data["priority"]),
            estimated_time=data["estimated_time"],
            status=Status(data["status"]),
            depends_on=set(data["depends_on"]),
            started_at=parse_time(data["started_at"]),
            completed_at=parse_time(data["completed_at"]),
            actual_time=data["actual_time"],
        )


class TaskJSON(TypedDict):
    name: str
    description: str
    priority: str
    estimated_time: float
    status: str
    depends_on: list[str]
    started_at: str | None
    completed_at: str | None
    actual_time: float | None


def format_time(moment: datetime | None) -> str | None:
    """Return an ISO 8601 string, or None."""
    return moment.isoformat() if moment else None


def parse_time(text: str | None) -> datetime | None:
    """Parse an ISO 8601 string, or return None."""
    return datetime.fromisoformat(text) if text else None
