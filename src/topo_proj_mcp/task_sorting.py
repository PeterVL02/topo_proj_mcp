"""Module for the task dependency graph: validation, topological stages and rendering."""

from collections.abc import Iterable
from graphlib import CycleError, TopologicalSorter

from topo_proj_mcp.models import Status, Task

type Edges = dict[str, set[str]]
"""Maps each task name to the names it depends on."""

STATUS_MARKERS: dict[Status, str] = {
    Status.NOT_STARTED: "[ ]",
    Status.IN_PROGRESS: "[~]",
    Status.COMPLETED: "[x]",
}


class PlanError(ValueError):
    pass


def check_estimate(estimated_time: float) -> None:
    """Raise PlanError unless the estimate is a positive number of hours."""
    if estimated_time <= 0:
        raise PlanError(f"estimated_time must be > 0 hours, got {estimated_time}.")


class Project:
    def __init__(self) -> None:
        self.tasks: dict[str, Task] = {}

    def edges(self) -> Edges:
        """Return a copy of the graph that can be edited without touching the tasks."""
        return {name: set(task.depends_on) for name, task in self.tasks.items()}

    def dependents_of(self, name: str) -> set[str]:
        """Return the names of tasks that depend directly on `name`."""
        return {other for other, task in self.tasks.items() if name in task.depends_on}

    def add_task(self, task: Task, dependents: Iterable[str] = ()) -> None:
        """Add a task; `dependents` are existing tasks that should depend on it.

        Raises:
            PlanError: If the name is blank or taken, the estimate is not positive,
                a referenced task does not exist, or the result would contain a cycle.
        """
        if not task.name.strip():
            raise PlanError("Task name must not be blank.")
        if task.name in self.tasks:
            raise PlanError(
                f"Task '{task.name}' already exists. Use topo_update_task to change it."
            )
        check_estimate(task.estimated_time)
        edges = self.edges()
        edges[task.name] = set(task.depends_on)
        link_dependents(edges, task.name, dependents)
        validate(edges)
        self.tasks[task.name] = task
        self.apply_edges(edges)

    def apply_edges(self, edges: Edges) -> None:
        """Write a validated edge map back onto the tasks."""
        for name, depends_on in edges.items():
            self.tasks[name].depends_on = depends_on

    def stages(self) -> list[list[str]]:
        """Group tasks into stages; every task comes after all of its dependencies."""
        sorter = TopologicalSorter(self.edges())
        sorter.prepare()
        stages: list[list[str]] = []
        while sorter.is_active():
            ready = sorted(sorter.get_ready())
            stages.append(ready)
            sorter.done(*ready)
        return stages

    def render(self) -> str:
        """Return the plan as staged text that a model can read and edit against."""
        if not self.tasks:
            return "No tasks yet. Add one with topo_add_task."
        lines: list[str] = []
        for number, stage in enumerate(self.stages(), start=1):
            lines.append(f"Stage {number}")
            lines.extend(f"  {self.describe(name)}" for name in stage)
        return "\n".join(lines)

    def describe(self, name: str) -> str:
        """Return one line describing a task and its edges."""
        task = self.tasks[name]
        line = (
            f"{STATUS_MARKERS[task.status]} {name}  {task.priority.upper()}"
            f"  est {task.estimated_time:g}h"
        )
        if task.depends_on:
            line += "  ← " + ", ".join(sorted(task.depends_on))
        if children := self.dependents_of(name):
            line += "  → " + ", ".join(sorted(children))
        return line


def link_dependents(edges: Edges, name: str, dependents: Iterable[str]) -> None:
    """Make every task in `dependents` depend on `name`, in place."""
    for child in dependents:
        if child not in edges:
            raise PlanError(f"Unknown task '{child}'. Add it first with topo_add_task.")
        edges[child].add(name)


def validate(edges: Edges) -> None:
    """Raise PlanError if an edge points to an unknown task or the graph has a cycle."""
    unknown = sorted({dep for deps in edges.values() for dep in deps} - edges.keys())
    if unknown:
        raise PlanError(
            f"Unknown task(s): {', '.join(unknown)}. Add them first with topo_add_task."
        )
    try:
        TopologicalSorter(edges).prepare()
    except CycleError as error:
        cycle = " -> ".join(error.args[1])
        raise PlanError(f"That change would create a cycle: {cycle}.") from error
