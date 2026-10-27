"""Module for the task dependency graph: validation, topological stages and rendering."""

import json
import logging
from collections.abc import Iterable
from datetime import UTC, datetime
from graphlib import CycleError, TopologicalSorter
from pathlib import Path
from statistics import mean
from typing import TypedDict

from topo_proj_mcp.models import PriorityLevel, Status, Task, TaskJSON

type Edges = dict[str, set[str]]
"""Maps each task name to the names it depends on."""

DEFAULT_GAMMA = 0.9

logger = logging.getLogger(__name__)

STATUS_MARKERS: dict[Status, str] = {
    Status.NOT_STARTED: "[ ]",
    Status.IN_PROGRESS: "[~]",
    Status.COMPLETED: "[x]",
}


class PlanError(ValueError):
    pass


class ProjectJSON(TypedDict):
    tasks: list[TaskJSON]


def check_estimate(estimated_time: float) -> None:
    """Raise PlanError unless the estimate is a positive number of hours."""
    if estimated_time <= 0:
        raise PlanError(f"estimated_time must be > 0 hours, got {estimated_time}.")


class Project:
    def __init__(self) -> None:
        self.tasks: dict[str, Task] = {}

    @classmethod
    def load(cls, path: Path) -> Project:
        """Load a project from JSON; a missing file gives an empty project.

        Raises:
            PlanError: If the file describes an invalid graph.
        """
        project = cls()
        if not path.exists():
            return project
        data: ProjectJSON = json.loads(path.read_text(encoding="utf-8"))
        project.tasks = {item["name"]: Task.from_json(item) for item in data["tasks"]}
        validate(project.edges())
        return project

    def save(self, path: Path) -> None:
        """Write the project to JSON atomically, so a crash never leaves half a file."""
        data: ProjectJSON = {"tasks": [task.to_json() for task in self.tasks.values()]}
        temp = path.with_name(path.name + ".tmp")
        temp.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        temp.replace(path)

    def edges(self) -> Edges:
        """Return a copy of the graph that can be edited without touching the tasks."""
        return {name: set(task.depends_on) for name, task in self.tasks.items()}

    def children(self) -> Edges:
        """Return the reverse graph: each task name mapped to the tasks that depend on it."""
        children: Edges = {name: set() for name in self.tasks}
        for name, task in self.tasks.items():
            for dep in task.depends_on:
                children[dep].add(name)
        return children

    def dependents_of(self, name: str) -> set[str]:
        """Return the names of tasks that depend directly on `name`."""
        return self.children()[name]

    def values(self, gamma: float = DEFAULT_GAMMA) -> dict[str, float]:
        """Return each task's time-discounted value.

        V(t) = γ^(est(t)/τ) · (base(t) + max V(dependents)), where τ is the mean estimate,
        so γ is the discount per average-sized task. Completed tasks are worth 0.

        Raises:
            PlanError: If gamma is not in (0, 1].
        """
        if not 0 < gamma <= 1:
            raise PlanError(f"gamma must be in (0, 1], got {gamma}.")
        if not self.tasks:
            return {}
        scale = mean(task.estimated_time for task in self.tasks.values())
        children = self.children()
        order = list(TopologicalSorter(self.edges()).static_order())
        values: dict[str, float] = {}
        for name in reversed(order):  # dependents before the tasks they depend on
            task = self.tasks[name]
            if task.status is Status.COMPLETED:
                values[name] = 0.0
                continue
            future = max((values[child] for child in children[name]), default=0.0)
            discount = gamma ** (task.estimated_time / scale)
            values[name] = discount * (task.base_value + future)
        return values

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

    def update_task(
        self,
        name: str,
        *,
        description: str | None = None,
        priority: PriorityLevel | None = None,
        estimated_time: float | None = None,
        depends_on: Iterable[str] | None = None,
        dependents: Iterable[str] | None = None,
    ) -> None:
        """Change a task's fields or move it; a given edge list replaces that side's edges.

        Raises:
            PlanError: If the task or a referenced task does not exist, the estimate is
                not positive, or the move would create a cycle.
        """
        task = self.require(name)
        if estimated_time is not None:
            check_estimate(estimated_time)
        edges = self.edges()
        if depends_on is not None:
            edges[name] = set(depends_on)
        if dependents is not None:
            for deps in edges.values():
                deps.discard(name)
            link_dependents(edges, name, dependents)
        validate(edges)
        self.apply_edges(edges)
        if description is not None:
            task.description = description
        if priority is not None:
            task.priority = priority
        if estimated_time is not None:
            task.estimated_time = estimated_time

    def remove_task(self, name: str, bridge: bool = True) -> None:
        """Remove a task; with `bridge`, its dependencies are passed on to its dependents.

        Raises:
            PlanError: If the task does not exist.
        """
        parents = self.require(name).depends_on
        edges = self.edges()
        del edges[name]
        for deps in edges.values():
            if name in deps:
                deps.discard(name)
                if bridge:
                    deps |= parents
        del self.tasks[name]
        self.apply_edges(edges)

    def set_status(
        self,
        name: str,
        status: Status,
        actual_hours: float | None = None,
        now: datetime | None = None,
    ) -> None:
        """Change a task's status and record when it started and finished.

        On completion, actual time is `actual_hours` if given, otherwise the hours
        between start and completion (None if the task was never started).

        Raises:
            PlanError: If the task is unknown, its dependencies are not all completed
                (when starting or completing it), or `actual_hours` is invalid.
        """
        task = self.require(name)
        if actual_hours is not None and (
            status is not Status.COMPLETED or actual_hours <= 0
        ):
            raise PlanError(
                "actual_hours must be > 0 and is only allowed with 'completed'."
            )
        waiting = sorted(
            d for d in task.depends_on if self.tasks[d].status is not Status.COMPLETED
        )
        if status is not Status.NOT_STARTED and waiting:
            raise PlanError(
                f"'{name}' is still waiting on {', '.join(waiting)}. Complete those first, "
                "or move the task with topo_update_task."
            )
        now = now or datetime.now(UTC)
        logger.info("%s: %s -> %s", name, task.status, status)
        task.status = status
        match status:
            case Status.NOT_STARTED:
                task.started_at = task.completed_at = task.actual_time = None
            case Status.IN_PROGRESS:
                task.started_at = task.started_at or now
                task.completed_at = task.actual_time = None
            case Status.COMPLETED:
                task.completed_at = now
                if actual_hours is not None:
                    task.actual_time = actual_hours
                elif task.started_at is not None:
                    task.actual_time = (now - task.started_at).total_seconds() / 3600
                else:
                    task.actual_time = None

    def next_tasks(
        self, gamma: float = DEFAULT_GAMMA, budget: float | None = None
    ) -> list[str]:
        """Return tasks to work on next, best value first.

        Without a budget: every unfinished task whose dependencies are all completed.
        With a budget (hours): simulate working through the plan, repeatedly taking the
        highest-value ready task that still fits, so later picks may depend on earlier ones.

        Raises:
            PlanError: If gamma is out of range or the budget is not positive.
        """
        values = self.values(gamma)
        done = {n for n, t in self.tasks.items() if t.status is Status.COMPLETED}

        def ready() -> list[str]:
            """Return unfinished tasks whose dependencies are all in `done`, best first."""
            names = [
                n
                for n, t in self.tasks.items()
                if n not in done and t.depends_on <= done
            ]
            return sorted(names, key=lambda n: (-values[n], n))

        if budget is None:
            return ready()
        if budget <= 0:
            raise PlanError(f"budget must be > 0 hours, got {budget}.")
        # ponytail: greedy by value; optimal picking is knapsack with precedence (NP-hard)
        picked: list[str] = []
        remaining = budget
        while fits := [n for n in ready() if self.tasks[n].estimated_time <= remaining]:
            picked.append(fits[0])
            done.add(fits[0])
            remaining -= self.tasks[fits[0]].estimated_time
        return picked

    def etas(self) -> dict[str, float]:
        """Return each task's earliest finish in hours from now, via the longest path.

        A task can't start until all its dependencies finish, so its eta is its own
        estimate plus the largest eta among its dependencies. Completed tasks take 0 hours.
        """
        etas: dict[str, float] = {}
        for name in TopologicalSorter(self.edges()).static_order():
            task = self.tasks[name]
            own = 0.0 if task.status is Status.COMPLETED else task.estimated_time
            etas[name] = own + max((etas[d] for d in task.depends_on), default=0.0)
        return etas

    def render_next(
        self, gamma: float = DEFAULT_GAMMA, budget: float | None = None
    ) -> str:
        """Return the output of `next_tasks` as text."""
        if not self.tasks:
            return "No tasks yet. Add one with topo_add_task."
        names = self.next_tasks(gamma, budget)
        if budget is not None:
            used = sum(self.tasks[n].estimated_time for n in names)
            header = (
                f"Best picks for the budget, in order ({used:g} of {budget:g}h used):"
            )
            if not names:
                return f"Nothing unfinished fits in {budget:g}h."
        elif names:
            header = "Ready now (best value first):"
        else:  # some unfinished task is always ready, so this means all done
            return "All tasks are done."
        described = self.describe_all(self.values(gamma))
        lines = [header]
        lines.extend(f"  {i}. {described[n]}" for i, n in enumerate(names, start=1))
        return "\n".join(lines)

    def require(self, name: str) -> Task:
        """Return the named task, or raise PlanError pointing at topo_get_plan."""
        if name not in self.tasks:
            raise PlanError(
                f"Unknown task '{name}'. Call topo_get_plan to see task names."
            )
        return self.tasks[name]

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

    def render(self, gamma: float = DEFAULT_GAMMA) -> str:
        """Return the plan as staged text, each stage ordered by value (highest first)."""
        if not self.tasks:
            return "No tasks yet. Add one with topo_add_task."
        values = self.values(gamma)
        described = self.describe_all(values)
        done = sum(task.status is Status.COMPLETED for task in self.tasks.values())
        scale = mean(task.estimated_time for task in self.tasks.values())
        header = (
            f"Plan: {done}/{len(self.tasks)} done. "
            f"V discounted by γ={gamma:g} per average task ({scale:.2g}h)."
        )
        lines = [header]
        for number, stage in enumerate(self.stages(), start=1):
            lines.append(f"Stage {number}")
            for name in sorted(stage, key=lambda n: (-values[n], n)):
                lines.append(f"  {described[name]}")
        return "\n".join(lines)

    def describe_all(self, values: dict[str, float]) -> dict[str, str]:
        """Return one line per task: status, priority, estimate, eta, value and edges."""
        etas = self.etas()
        children = self.children()
        described: dict[str, str] = {}
        for name, task in self.tasks.items():
            line = (
                f"{STATUS_MARKERS[task.status]} {name}  {task.priority.upper()}"
                f"  est {task.estimated_time:g}h  eta {etas[name]:g}h  V={values[name]:.2f}"
            )
            if task.depends_on:
                line += "  ← " + ", ".join(sorted(task.depends_on))
            if children[name]:
                line += "  → " + ", ".join(sorted(children[name]))
            described[name] = line
        return described


def link_dependents(edges: Edges, name: str, dependents: Iterable[str]) -> None:
    """Make every task in `dependents` depend on `name`, in place.

    A direct edge from one of `name`'s dependencies to the dependent is dropped,
    because it is now implied through `name` (this is what "insert between" means).
    """
    for child in dependents:
        if child not in edges:
            raise PlanError(f"Unknown task '{child}'. Add it first with topo_add_task.")
        edges[child] -= edges[name]
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
