"""Tests for the benchmark harness's parsing and metrics (no Claude calls)."""

import importlib.util
import json
import os
import sys
from pathlib import Path
from types import ModuleType

import pytest

BENCH = Path(__file__).parent.parent / "bench" / "run.py"


def load_bench() -> ModuleType:
    """Import bench/run.py as a module."""
    spec = importlib.util.spec_from_file_location("bench_run", BENCH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["bench_run"] = module
    spec.loader.exec_module(module)
    return module


bench = load_bench()


def stream(*messages: dict[str, object]) -> list[str]:
    """Encode messages as stream-json lines."""
    return [json.dumps(m) for m in messages]


def tool_use(name: str, **tool_input: object) -> dict[str, object]:
    """Build an assistant message holding one tool call."""
    block = {"type": "tool_use", "name": name, "input": tool_input}
    return {"type": "assistant", "message": {"content": [block]}}


RESULT = {
    "type": "result",
    "total_cost_usd": 0.12,
    "num_turns": 3,
    "duration_ms": 4500,
    "usage": {
        "input_tokens": 10,
        "cache_creation_input_tokens": 100,
        "cache_read_input_tokens": 1000,
        "output_tokens": 50,
    },
}


def test_parse_stream_collects_tool_calls_and_usage() -> None:
    """Tool calls are listed in order and token counts are summed."""
    parsed = bench.parse_stream(
        stream(tool_use("Skill", skill="x"), tool_use("Write", file_path="a"), RESULT)
    )
    assert [c["name"] for c in parsed["tool_calls"]] == ["Skill", "Write"]
    assert parsed["input_tokens"] == 1110
    assert parsed["output_tokens"] == 50
    assert parsed["cost_usd"] == 0.12
    assert parsed["turns"] == 3


def test_parse_stream_without_result_reports_error() -> None:
    """A run that died before its result line is marked as failed."""
    assert bench.parse_stream(stream(tool_use("Write")))["error"]


@pytest.mark.parametrize(
    ("arm", "calls", "expected"),
    [
        ("topo", [tool_use("mcp__plugin_topo-planner_topo__topo_add_task")], True),
        ("topo", [tool_use("Skill", skill="topo-planner:topo-plan")], True),
        ("topo", [tool_use("Write", file_path="plan.md")], False),
        ("prose", [tool_use("Skill", skill="prose:planning-and-task-breakdown")], True),
        ("prose", [tool_use("Skill", skill="other")], False),
        ("none", [tool_use("Skill", skill="prose:planning-and-task-breakdown")], False),
    ],
)
def test_triggered(arm: str, calls: list[dict[str, object]], expected: bool) -> None:
    """Each setup counts only its own planning aid as triggered."""
    parsed = bench.parse_stream(stream(*calls, RESULT))
    assert bench.triggered(arm, parsed["tool_calls"]) is expected


def test_count_plan_tasks(tmp_path: Path) -> None:
    """Plan size comes from the topo plan file or the prose todo list."""
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "topo-plan.json").write_text(
        json.dumps({"tasks": [{"name": "a"}, {"name": "b"}]}), encoding="utf-8"
    )
    (tmp_path / "tasks").mkdir()
    (tmp_path / "tasks" / "todo.md").write_text(
        "## Task 1: a\n## Task 2: b\n## Task 3: c\n## Checkpoint\n", encoding="utf-8"
    )
    assert bench.count_plan_tasks("topo", tmp_path) == 2
    assert bench.count_plan_tasks("prose", tmp_path) == 3
    assert bench.count_plan_tasks("none", tmp_path) is None


def test_order_violations(tmp_path: Path) -> None:
    """A file written before one of its dependencies, or never written, is a violation."""
    dag = {"a.txt": [], "b.txt": ["a.txt"], "c.txt": ["b.txt"], "d.txt": ["a.txt"]}
    for name, mtime in {"a.txt": 100, "b.txt": 300, "c.txt": 200}.items():
        path = tmp_path / name
        path.write_text(name, encoding="utf-8")
        os.utime(path, (mtime, mtime))
    # c before its dependency b: 1 violation; d missing: 1 violation
    assert bench.order_violations(tmp_path, dag) == 2


def test_summarize_reports_medians_per_arm_and_kind() -> None:
    """The summary table has one row per setup and prompt kind, with medians."""
    record = {
        "arm": "topo",
        "kind": "plan",
        "prompt_id": "p1",
        "run": 1,
        "cost_usd": 0.1,
        "input_tokens": 1000,
        "output_tokens": 100,
        "turns": 2,
        "duration_s": 3.0,
        "triggered": True,
        "tool_calls": [],
        "tasks_in_plan": 8,
        "order_violations": None,
        "error": None,
    }
    other = record | {"cost_usd": 0.3, "triggered": False, "tasks_in_plan": None}
    table = bench.summarize([record, other])
    assert "| topo | plan | 2 | 50% |" in table
    assert "0.200" in table  # median cost


def test_forced_arms_invoke_their_skill_and_share_base_metrics(tmp_path: Path) -> None:
    """Forced setups start with the skill's slash command and count like their base setup."""
    assert bench.arm_prompt("topo-forced", "Plan X").startswith(
        "/topo-planner:topo-plan "
    )
    assert bench.arm_prompt("prose-forced", "Plan X").startswith(
        "/prose:planning-and-task-breakdown "
    )
    assert bench.arm_prompt("topo", "Plan X") == "Plan X"
    parsed = bench.parse_stream(
        stream(tool_use("mcp__plugin_topo-planner_topo__topo_add_task"), RESULT)
    )
    assert bench.triggered("topo-forced", parsed["tool_calls"])
    assert bench.count_plan_tasks("prose-forced", tmp_path) is None


def test_forced_arms_skip_trivial_prompts() -> None:
    """Forcing a planning skill onto a trivial prompt measures nothing useful."""
    assert not bench.applies("topo-forced", "trivial")
    assert bench.applies("topo-forced", "plan")
    assert bench.applies("none", "trivial")


def test_prose_counts_as_triggered_when_it_writes_the_plan_files() -> None:
    """A slash-invoked skill makes no Skill call, but its tasks/ files give it away."""
    parsed = bench.parse_stream(
        stream(tool_use("Write", file_path=r"C:\tmp\run\tasks\todo.md"), RESULT)
    )
    assert bench.triggered("prose-forced", parsed["tool_calls"])
    assert not bench.triggered("topo", parsed["tool_calls"])


def test_count_plan_tasks_accepts_checklist_format(tmp_path: Path) -> None:
    """Checklist items count as tasks; dependency lines that mention a task do not."""
    (tmp_path / "tasks").mkdir()
    (tmp_path / "tasks" / "todo.md").write_text(
        "- [ ] **Task 1: a**\n  **Dependencies:** Task 1\n- [x] Task 2: b\n"
        "### Checkpoint\n- [ ] All tests pass\n",
        encoding="utf-8",
    )
    assert bench.count_plan_tasks("prose", tmp_path) == 2
