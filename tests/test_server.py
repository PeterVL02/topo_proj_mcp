"""End-to-end tests that call the MCP tools in-process."""

import asyncio
from pathlib import Path

import pytest

from topo_proj_mcp import server
from topo_proj_mcp.task_sorting import Project


@pytest.fixture(autouse=True)
def project_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Give every test an empty project saved to a temporary file."""
    path = tmp_path / "project.json"
    monkeypatch.setattr(server, "project", Project())
    monkeypatch.setattr(server, "project_file", path)
    return path


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


def test_set_status_shows_what_is_next() -> None:
    """Finishing a task reports the newly unlocked work."""
    call("topo_add_task", name="a", estimated_time=1)
    call("topo_add_task", name="b", estimated_time=1, depends_on=["a"])
    assert "] b " not in call("topo_next_tasks")
    text = call("topo_set_status", name="a", status="completed")
    assert "Ready now" in text and "] b " in text


def test_edits_are_saved_to_the_project_file(project_file: Path) -> None:
    """Every successful edit is written to disk so the plan survives restarts."""
    call("topo_add_task", name="a", estimated_time=1)
    assert Project.load(project_file).tasks.keys() == {"a"}


def test_next_tasks_with_budget() -> None:
    """The budget variant lists picks in order and the hours they use."""
    call("topo_add_task", name="a", estimated_time=1)
    call("topo_add_task", name="b", estimated_time=3, depends_on=["a"])
    text = call("topo_next_tasks", budget=2)
    assert "1 of 2h" in text and "] a " in text and "] b " not in text


def test_time_report_tool() -> None:
    """The report is reachable through MCP."""
    call("topo_add_task", name="a", estimated_time=2)
    call("topo_set_status", name="a", status="completed", actual_hours=1)
    assert "×0.50" in call("topo_time_report")


def test_concurrent_calls_do_not_race(project_file: Path) -> None:
    """The SDK runs tools in threads; parallel edits must all land and be saved."""

    async def add_many() -> None:
        """Fire 20 add_task calls at once."""
        await asyncio.gather(
            *(
                server.mcp.call_tool(
                    "topo_add_task", {"name": f"t{i}", "estimated_time": 1}
                )
                for i in range(20)
            )
        )

    asyncio.run(add_many())
    assert len(Project.load(project_file).tasks) == 20
