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
    monkeypatch.setattr(server, "load_error", None)
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
    assert "Newly ready: b." in text and "Next best: b." in text


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


CORRUPT_FILES = {
    "invalid json": "{not json",
    "missing key": '{"tasks": [{"name": "a"}]}',
    "cycle": (
        '{"tasks": ['
        '{"name": "a", "description": "", "priority": "low", "estimated_time": 1,'
        ' "status": "not_started", "depends_on": ["b"], "started_at": null,'
        ' "completed_at": null, "actual_time": null},'
        '{"name": "b", "description": "", "priority": "low", "estimated_time": 1,'
        ' "status": "not_started", "depends_on": ["a"], "started_at": null,'
        ' "completed_at": null, "actual_time": null}]}'
    ),
}


@pytest.mark.parametrize("content", CORRUPT_FILES.values(), ids=CORRUPT_FILES.keys())
def test_corrupt_plan_file_gives_clear_error(
    monkeypatch: pytest.MonkeyPatch, project_file: Path, content: str
) -> None:
    """A broken plan file never crashes the server and is never overwritten."""
    project_file.write_text(content, encoding="utf-8")
    loaded, error = server.load_project()
    assert loaded.tasks == {} and error
    monkeypatch.setattr(server, "load_error", error)
    from mcp.server.mcpserver.exceptions import ToolError

    with pytest.raises(ToolError, match="unreadable"):
        call("topo_add_task", name="x", estimated_time=1)
    assert project_file.read_text(encoding="utf-8") == content


def test_server_recovers_once_the_file_is_fixed(
    monkeypatch: pytest.MonkeyPatch, project_file: Path
) -> None:
    """Deleting or fixing the file lets the next call proceed without a restart."""
    monkeypatch.setattr(server, "load_error", "JSONDecodeError: bad")
    assert not project_file.exists()  # the user deleted the broken file
    assert "No tasks yet" in call("topo_get_plan")


def test_add_tasks_builds_a_plan_in_one_call() -> None:
    """The batch tool takes a whole plan, forward references included."""
    plan = call(
        "topo_add_tasks",
        tasks=[
            {"name": "b", "estimated_time": 1, "depends_on": ["a"]},
            {"name": "a", "estimated_time": 2, "priority": "high"},
        ],
    )
    assert plan.index("] a ") < plan.index("] b ") and "Stage 2" in plan


def build_chain(size: int) -> None:
    """Add a chain of `size` tasks through the batch tool."""
    call(
        "topo_add_tasks",
        tasks=[
            {
                "name": f"t{i}",
                "estimated_time": 1,
                "depends_on": [f"t{i - 1}"] if i else [],
            }
            for i in range(size)
        ],
    )


def edit_replies() -> list[str]:
    """Run one of each edit tool on the current plan and return the replies."""
    return [
        call("topo_add_task", name="extra", estimated_time=1, depends_on=["t0"]),
        call("topo_update_task", name="extra", priority="high"),
        call("topo_set_status", name="t0", status="completed"),
        call("topo_remove_task", name="extra"),
    ]


def test_edit_replies_do_not_grow_with_the_plan() -> None:
    """Edits answer with one line, not the whole plan, so building stays linear."""
    build_chain(5)
    small = edit_replies()
    server.project = Project()
    build_chain(50)
    large = edit_replies()
    for short, long in zip(small, large, strict=True):
        assert len(long) <= len(short) + 10, (short, long)
    assert "Stage" not in "".join(large)
