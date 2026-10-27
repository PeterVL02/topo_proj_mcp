"""MCP server exposing the topological task planner as tools."""

from collections.abc import Iterator
from contextlib import contextmanager

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations

from topo_proj_mcp.models import PriorityLevel, Task
from topo_proj_mcp.task_sorting import PlanError, Project

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


@mcp.tool(annotations=READ)
def topo_get_plan() -> str:
    """Return the plan as stages; a task appears after everything it depends on.

    Each line reads: status, name, priority, estimate, ← what it depends on,
    → what depends on it. Status markers: [ ] not started, [~] in progress, [x] done.
    """
    return project.render()
