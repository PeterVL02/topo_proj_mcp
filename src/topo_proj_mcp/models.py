"""Module for defining data structures and models."""

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


class Task:
    def __init__(
        self,
        name: str,
        description: str,
        priority: PriorityLevel,
        estimated_time: float,
        status: Status = Status.NOT_STARTED,
        depends_on: list[Task] | None = None,
        dependents: list[Task] | None = None,
    ) -> None:
        self.name = name
        self.description = description
        self.priority = priority
        self.estimated_time = estimated_time
        self.status = status
        self.depends_on = depends_on
        self.dependents = dependents
        self._priority_value: float = PRIORITY_MAPPING[priority]
        self.discounted_count: int = 0

    def update_status(self, new_status: Status) -> None:
        """Update the status of the task."""
        self.status = new_status

    def update_priority(self, new_priority: PriorityLevel) -> None:
        """Update the priority of the task and reapply previous discounts."""
        self.priority = new_priority
        self._priority_value = PRIORITY_MAPPING[new_priority]
        for _ in range(self.discounted_count):
            self._priority_value *= 0.9

    def discount_priority_value(self, gamma: float) -> None:
        """Apply a discount factor to the priority value of the task."""
        self._priority_value = round(self._priority_value * gamma, 3)
        self.discounted_count += 1

    @property
    def priority_value(self) -> float:
        """Return the current (possibly discounted) priority value."""
        return self._priority_value

    def __repr__(self) -> str:
        """Return a readable representation including dependency names."""
        depends_on = (
            [task.name for task in self.depends_on] if self.depends_on else None
        )
        dependents = (
            [task.name for task in self.dependents] if self.dependents else None
        )
        return (
            f"Task(name={self.name}, description={self.description}, "
            f"priority={self.priority}, priority_value={self.priority_value}, "
            f"estimated_time={self.estimated_time}, status={self.status}, "
            f"depends_on={depends_on}, dependents={dependents})"
        )


if __name__ == "__main__":
    task1 = Task(
        name="Design Database Schema",
        description="Create the initial database schema for the project.",
        priority=PriorityLevel.HIGH,
        estimated_time=5.0,
    )
    task2 = Task(
        name="Implement Authentication",
        description="Develop the user authentication module.",
        priority=PriorityLevel.MEDIUM,
        estimated_time=8.0,
    )
    for _ in range(3):
        task1.discount_priority_value(0.9)
    print(task1)
    print(task2)
