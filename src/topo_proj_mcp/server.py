"""MCP server exposing the topological task planner as tools."""

import os
import threading
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import NotRequired, TypedDict

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations

from topo_proj_mcp.models import PriorityLevel, Status, Task
from topo_proj_mcp.task_sorting import DEFAULT_GAMMA, PlanError, Project

mcp = MCPServer(
    "topo_mcp",
    instructions=(
        "Plan work as a dependency graph before doing it. Add tasks with what they depend "
        "on and what depends on them, read the staged plan, then work through it in order. "
        "Task names are the IDs. Times are in hours."
    ),
)
project_file = Path(os.environ.get("TOPO_PROJECT_FILE", "project.json"))


def load_project() -> tuple[Project, str | None]:
    """Load the plan file; if it is unreadable, return an empty project and the reason."""
    try:
        return Project.load(project_file), None
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        return Project(), f"{type(error).__name__}: {error}"


project, load_error = load_project()
# ponytail: one global lock; fine for one project per server
project_lock = threading.Lock()

EDIT = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=False,
    open_world_hint=False,
)
READ = ToolAnnotations(read_only_hint=True, open_world_hint=False)


@contextmanager
def using_project() -> Generator[None]:
    """Hold the project lock and turn PlanError into ToolError for the model.

    The SDK runs sync tools in worker threads, so parallel calls would otherwise race
    on the shared project and its save file. If the plan file was unreadable, it is
    retried here, and nothing runs (or overwrites it) until it loads.
    """
    global project, load_error
    with project_lock:
        if load_error:
            project, load_error = load_project()
        if load_error:
            raise ToolError(
                f"Plan file {project_file} is unreadable ({load_error}). Fix or delete "
                "it; no changes are saved until it loads."
            )
        try:
            yield
        except PlanError as error:
            raise ToolError(str(error)) from error


class NewTask(TypedDict):
    name: str
    estimated_time: float
    priority: NotRequired[PriorityLevel]
    description: NotRequired[str]
    depends_on: NotRequired[list[str]]
    dependents: NotRequired[list[str]]


def to_task(spec: NewTask) -> Task:
    """Build a Task from a tool argument."""
    return Task(
        name=spec["name"],
        description=spec.get("description", ""),
        priority=PriorityLevel(spec.get("priority", PriorityLevel.MEDIUM)),
        estimated_time=spec["estimated_time"],
        depends_on=set(spec.get("depends_on", ())),
    )


def save_and_summarize(prefix: str, name: str) -> str:
    """Save after an edit and return one line about the changed task.

    Edits answer with a single line so that building a plan costs tokens in
    proportion to its size, not its size squared; topo_get_plan shows the whole plan.
    """
    project.save(project_file)
    return f"{prefix}: {project.summarize_task(name)}"


@mcp.tool(annotations=EDIT)
def topo_add_tasks(tasks: list[NewTask]) -> str:
    """Add several tasks in one call (best for a new plan) and return the full plan.

    Tasks may reference each other in any order. Either all are added or none are.
    Each item has the same fields as topo_add_task: name, estimated_time (hours > 0),
    and optionally priority, description, depends_on and dependents.
    """
    with using_project():
        project.add_tasks(
            [to_task(spec) for spec in tasks],
            {spec["name"]: spec.get("dependents", []) for spec in tasks},
        )
        project.save(project_file)
        return project.render()


@mcp.tool(annotations=EDIT)
def topo_add_task(
    name: str,
    estimated_time: float,
    priority: PriorityLevel = PriorityLevel.MEDIUM,
    description: str = "",
    depends_on: list[str] | None = None,
    dependents: list[str] | None = None,
) -> str:
    """Add one task to the plan and return its plan line.

    Args:
        name: Unique task name, used as its ID by every other tool.
        estimated_time: Estimated hours to complete the task itself (> 0).
        priority: low, medium or high.
        description: What the task involves.
        depends_on: Existing tasks that must finish before this one.
        dependents: Existing tasks that must wait for this one. To insert between A and B,
            pass depends_on=[A] and dependents=[B].
    """
    spec: NewTask = {
        "name": name,
        "estimated_time": estimated_time,
        "priority": priority,
        "description": description,
        "depends_on": depends_on or [],
    }
    with using_project():
        project.add_task(to_task(spec), dependents or ())
        return save_and_summarize("Added", name)


@mcp.tool(annotations=EDIT)
def topo_update_task(
    name: str,
    description: str | None = None,
    priority: PriorityLevel | None = None,
    estimated_time: float | None = None,
    depends_on: list[str] | None = None,
    dependents: list[str] | None = None,
) -> str:
    """Change a task's fields or move it in the plan, and return its new plan line.

    Only the arguments you pass are changed. To move a task, pass depends_on and/or
    dependents: each list *replaces* that side's edges (pass [] to clear it).

    Args:
        name: The task to change.
        description: New description.
        priority: New priority: low, medium or high.
        estimated_time: New estimate in hours (> 0).
        depends_on: New full list of tasks this one waits for.
        dependents: New full list of tasks that wait for this one.
    """
    with using_project():
        project.update_task(
            name,
            description=description,
            priority=priority,
            estimated_time=estimated_time,
            depends_on=depends_on,
            dependents=dependents,
        )
        return save_and_summarize("Updated", name)


@mcp.tool(
    annotations=ToolAnnotations(
        read_only_hint=False,
        destructive_hint=True,
        idempotent_hint=True,
        open_world_hint=False,
    )
)
def topo_remove_task(name: str, bridge: bool = True) -> str:
    """Remove a task and say how the tasks around it were reconnected.

    Args:
        name: The task to remove.
        bridge: If true (default), tasks that waited for it now wait for its dependencies
            instead, so the ordering is preserved. If false, its edges are just dropped.
    """
    with using_project():
        parents = sorted(project.require(name).depends_on)
        children = sorted(project.children()[name])
        project.remove_task(name, bridge)
        project.save(project_file)
        reply = f"Removed {name}."
        if bridge and parents and children:
            reply += f" {', '.join(children)} now wait for {', '.join(parents)}."
        return reply


@mcp.tool(
    annotations=ToolAnnotations(
        read_only_hint=False,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=False,
    )
)
def topo_set_status(
    name: str, status: Status, actual_hours: float | None = None
) -> str:
    """Mark a task not_started, in_progress or completed; reply with what it unlocked.

    A task can only be started or completed once everything it depends on is completed.
    Start and completion times are recorded to compare actual time with the estimate.

    Args:
        name: The task to update.
        status: not_started, in_progress or completed.
        actual_hours: Hours actually spent, only with completed. Defaults to the time
            between in_progress and completed, which includes any idle time.
    """
    with using_project():
        before = set(project.next_tasks())
        project.set_status(name, status, actual_hours)
        project.save(project_file)
        ready = project.next_tasks()
        reply = f"{name}: {status}."
        if unlocked := sorted(set(ready) - before - {name}):
            reply += f" Newly ready: {', '.join(unlocked)}."
        reply += f" Next best: {ready[0]}." if ready else " All tasks are done."
        return reply


@mcp.tool(annotations=READ)
def topo_next_tasks(gamma: float = DEFAULT_GAMMA, budget: float | None = None) -> str:
    """Return the tasks that can be worked on now, highest value first.

    With a budget, return an ordered work list that fits in that many hours instead:
    it may include tasks that become ready once earlier picks are done.

    Args:
        gamma: Discount per average task duration, in (0, 1]. Lower favours quick wins.
        budget: Hours available. Omit to list everything that is ready now.
    """
    with using_project():
        return project.render_next(gamma, budget)


@mcp.tool(annotations=READ)
def topo_get_plan(gamma: float = DEFAULT_GAMMA) -> str:
    """Return the plan as stages; a task appears after everything it depends on.

    Each line reads: status, name, priority, estimate, value V, ← what it depends on,
    → what depends on it. Status markers: [ ] not started, [~] in progress, [x] done.
    Within a stage, tasks are ordered by V (highest first).

    V is the task's priority plus the best value it unblocks, discounted by gamma once
    per average-sized task of time, so short tasks and blockers of important work rank higher.

    Args:
        gamma: Discount per average task duration, in (0, 1]. Lower favours quick wins.
    """
    with using_project():
        return project.render(gamma)


@mcp.tool(annotations=READ)
def topo_time_report() -> str:
    """Compare estimated and actual hours for completed tasks.

    Shows each task's actual/estimate ratio, the overall ratio (your estimation bias)
    and the remaining estimated work scaled by that ratio.
    """
    with using_project():
        return project.time_report()
