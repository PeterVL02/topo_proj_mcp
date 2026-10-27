"""End-to-end tests that call the MCP tools in-process."""

import asyncio

import pytest

from topo_proj_mcp import server
from topo_proj_mcp.task_sorting import Project


@pytest.fixture(autouse=True)
def fresh_project(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give every test an empty project."""
    monkeypatch.setattr(server, "project", Project())


def call(tool: str, **arguments: object) -> str:
    """Call a tool through the MCP server and return its text output."""
    result = asyncio.run(server.mcp.call_tool(tool, arguments))
    return "".join(block.text for block in result.content)


def test_add_tasks_and_get_plan() -> None:
    """Tasks added through the tools show up in the staged plan."""
    call("topo_add_task", name="schema", estimated_time=2, priority="high")
    call("topo_add_task", name="api", estimated_time=3, depends_on=["schema"])
    plan = call("topo_get_plan")
    assert plan.index("schema") < plan.index("api")
    assert "Stage 2" in plan


def test_plan_error_reaches_the_model() -> None:
    """A rejected edit comes back as a readable tool error, not a crash."""
    from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError

    with pytest.raises(ToolError, match="Unknown task") as info:
        call("topo_add_task", name="api", estimated_time=1, depends_on=["nope"])
    assert not isinstance(info.value, UnexpectedToolError)


def test_move_and_remove_through_tools() -> None:
    """A model can reshape the plan with update and remove."""
    call("topo_add_task", name="a", estimated_time=1)
    call("topo_add_task", name="b", estimated_time=1)
    plan = call("topo_update_task", name="b", depends_on=["a"], priority="high")
    assert "b  HIGH" in plan and "← a" in plan
    plan = call("topo_remove_task", name="a")
    assert "a  " not in plan and "Stage 2" not in plan
