"""MCP server exposing the topological task planner as tools."""

from collections.abc import Iterator
from contextlib import contextmanager

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
project = Project()

EDIT = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=False,
    open_world_hint=False,
)
READ = ToolAnnotations(read_only_hint=True, open_world_hint=False)


@contextmanager
def plan_errors() -> Iterator[None]:
    """Turn PlanError into ToolError so the model sees the message instead of a crash."""
    try:
        yield
    except PlanError as error:
        raise ToolError(str(error)) from error


@mcp.tool(annotations=EDIT)
def topo_add_task(
    name: str,
    estimated_time: float,
    priority: PriorityLevel = PriorityLevel.MEDIUM,
    description: str = "",
    depends_on: list[str] | None = None,
    dependents: list[str] | None = None,
) -> str:
    """Add a task to the plan and return the updated plan.

    Args:
        name: Unique task name, used as its ID by every other tool.
        estimated_time: Estimated hours to complete the task itself (> 0).
        priority: low, medium or high.
        description: What the task involves.
        depends_on: Existing tasks that must finish before this one.
        dependents: Existing tasks that must wait for this one. To insert between A and B,
            pass depends_on=[A] and dependents=[B].
    """
    task = Task(
        name=name,
        description=description,
        priority=priority,
        estimated_time=estimated_time,
        depends_on=set(depends_on or ()),
    )
    with plan_errors():
        project.add_task(task, dependents or ())
    return project.render()


@mcp.tool(annotations=EDIT)
def topo_update_task(
    name: str,
    description: str | None = None,
    priority: PriorityLevel | None = None,
    estimated_time: float | None = None,
    depends_on: list[str] | None = None,
    dependents: list[str] | None = None,
) -> str:
    """Change a task's fields or move it in the plan, and return the updated plan.

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
    with plan_errors():
        project.update_task(
            name,
            description=description,
            priority=priority,
            estimated_time=estimated_time,
            depends_on=depends_on,
            dependents=dependents,
        )
    return project.render()


@mcp.tool(
    annotations=ToolAnnotations(
        read_only_hint=False,
        destructive_hint=True,
        idempotent_hint=True,
        open_world_hint=False,
    )
)
def topo_remove_task(name: str, bridge: bool = True) -> str:
    """Remove a task and return the updated plan.

    Args:
        name: The task to remove.
        bridge: If true (default), tasks that waited for it now wait for its dependencies
            instead, so the ordering is preserved. If false, its edges are just dropped.
    """
    with plan_errors():
        project.remove_task(name, bridge)
    return project.render()


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
    """Mark a task not_started, in_progress or completed, and return what is ready next.

    A task can only be started or completed once everything it depends on is completed.
    Start and completion times are recorded to compare actual time with the estimate.

    Args:
        name: The task to update.
        status: not_started, in_progress or completed.
        actual_hours: Hours actually spent, only with completed. Defaults to the time
            between in_progress and completed, which includes any idle time.
    """
    with plan_errors():
        project.set_status(name, status, actual_hours)
    return f"{name}: {status}.\n\n{project.render_next()}"


@mcp.tool(annotations=READ)
def topo_next_tasks(gamma: float = DEFAULT_GAMMA) -> str:
    """Return the tasks that can be worked on now, highest value first.

    Args:
        gamma: Discount per average task duration, in (0, 1]. Lower favours quick wins.
    """
    with plan_errors():
        return project.render_next(gamma)


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
    with plan_errors():
        return project.render(gamma)
