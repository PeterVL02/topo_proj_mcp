"""Tests for the Project graph: building, sorting, scoring, timing and persistence."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from topo_proj_mcp.models import PriorityLevel, Status, Task
from topo_proj_mcp.task_sorting import PlanError, Project


def make_task(
    name: str,
    depends_on: set[str] | None = None,
    priority: PriorityLevel = PriorityLevel.MEDIUM,
    estimated_time: float = 1.0,
) -> Task:
    """Build a task with sensible defaults for tests."""
    return Task(
        name=name,
        description="",
        priority=priority,
        estimated_time=estimated_time,
        depends_on=depends_on or set(),
    )


def make_diamond() -> Project:
    """Build A -> (B, C) -> D."""
    project = Project()
    project.add_task(make_task("A"))
    project.add_task(make_task("B", {"A"}))
    project.add_task(make_task("C", {"A"}))
    project.add_task(make_task("D", {"B", "C"}))
    return project


def test_diamond_sorts_into_stages() -> None:
    """Tasks are grouped into stages that respect every dependency."""
    assert make_diamond().stages() == [["A"], ["B", "C"], ["D"]]


def test_dependents_are_stored_as_edges_on_the_dependent() -> None:
    """Passing dependents=[X] makes X depend on the new task."""
    project = Project()
    project.add_task(make_task("B"))
    project.add_task(make_task("A"), dependents=["B"])
    assert project.tasks["B"].depends_on == {"A"}
    assert project.children()["A"] == {"B"}


def test_cycle_is_rejected_and_project_unchanged() -> None:
    """An edit that closes a cycle raises and leaves the graph as it was."""
    project = make_diamond()
    with pytest.raises(PlanError, match="cycle"):
        project.add_task(make_task("E", {"D"}), dependents=["A"])
    assert "E" not in project.tasks
    assert project.tasks["A"].depends_on == set()


@pytest.mark.parametrize(
    ("task", "dependents"),
    [
        (make_task("E", {"missing"}), []),
        (make_task("E"), ["missing"]),
        (make_task("A"), []),
        (make_task("E", estimated_time=0), []),
        (make_task(" "), []),
    ],
)
def test_invalid_add_is_rejected(task: Task, dependents: list[str]) -> None:
    """Unknown names, duplicates, non-positive estimates and blank names are rejected."""
    project = make_diamond()
    with pytest.raises(PlanError):
        project.add_task(task, dependents=dependents)
    assert len(project.tasks) == 4


def test_render_shows_stages_and_edges() -> None:
    """The text view lists each stage with status markers and edge arrows."""
    text = make_diamond().render()
    assert "Stage 1" in text and "Stage 3" in text
    assert "[ ] A" in text
    assert "← B, C" in text
    assert "→ B, C" in text


def test_values_match_hand_computed_chain() -> None:
    """V = γ^(est/τ)·(base + max V(children)); here τ = 1 so γ applies once per task."""
    project = Project()
    project.add_task(make_task("low", priority=PriorityLevel.LOW))
    project.add_task(make_task("high", {"low"}, priority=PriorityLevel.HIGH))
    values = project.values(gamma=0.9)
    assert values["high"] == pytest.approx(0.9 * 3)
    assert values["low"] == pytest.approx(0.9 * (1 + 2.7))


def test_shorter_task_is_worth_more_at_equal_priority() -> None:
    """Time discounting favours short tasks: τ = 2, so exponents are 0.5 and 1.5."""
    project = Project()
    project.add_task(make_task("short", priority=PriorityLevel.HIGH, estimated_time=1))
    project.add_task(make_task("long", priority=PriorityLevel.HIGH, estimated_time=3))
    values = project.values(gamma=0.9)
    assert values["short"] == pytest.approx(3 * 0.9**0.5)
    assert values["long"] == pytest.approx(3 * 0.9**1.5)


def test_blocker_of_high_priority_work_comes_first_in_its_stage() -> None:
    """A LOW task that unblocks a HIGH task outranks a lone MEDIUM task."""
    project = Project()
    project.add_task(make_task("medium"))
    project.add_task(make_task("low", priority=PriorityLevel.LOW))
    project.add_task(make_task("high", {"low"}, priority=PriorityLevel.HIGH))
    text = project.render(gamma=0.9)
    assert text.index("low") < text.index("medium")
    assert "V=3.33" in text


def test_completed_task_has_no_value() -> None:
    """Finished work earns nothing more."""
    project = make_diamond()
    project.tasks["D"].status = Status.COMPLETED
    values = project.values()
    assert values["D"] == 0
    assert values["B"] == pytest.approx(0.9 * 2)


@pytest.mark.parametrize("gamma", [0, -0.5, 1.5])
def test_gamma_out_of_range_is_rejected(gamma: float) -> None:
    """γ must be in (0, 1]."""
    with pytest.raises(PlanError, match="gamma"):
        make_diamond().values(gamma)


def test_insert_between_drops_the_direct_edge() -> None:
    """Inserting X between A and B leaves A -> X -> B, without a redundant A -> B."""
    project = make_diamond()
    project.add_task(make_task("X", {"A"}), dependents=["B"])
    assert project.tasks["B"].depends_on == {"X"}
    assert project.stages() == [["A"], ["C", "X"], ["B"], ["D"]]


def test_move_by_replacing_depends_on() -> None:
    """Rewiring depends_on moves a task to a new place in the order."""
    project = make_diamond()
    project.update_task("C", depends_on=["B"])
    assert project.stages() == [["A"], ["B"], ["C"], ["D"]]


def test_move_by_replacing_dependents() -> None:
    """Passing dependents replaces every task that waits for this one."""
    project = make_diamond()
    project.update_task("A", dependents=["B"])
    assert project.tasks["C"].depends_on == set()
    assert project.stages() == [["A", "C"], ["B"], ["D"]]


def test_move_into_a_cycle_is_rejected_and_project_unchanged() -> None:
    """A move that closes a cycle raises and keeps the old edges."""
    project = make_diamond()
    with pytest.raises(PlanError, match="cycle"):
        project.update_task("A", depends_on=["D"])
    assert project.tasks["A"].depends_on == set()


def test_update_fields() -> None:
    """Priority, estimate and description can be changed in place."""
    project = make_diamond()
    project.update_task(
        "B", priority=PriorityLevel.HIGH, estimated_time=4, description="new"
    )
    task = project.tasks["B"]
    assert (task.priority, task.estimated_time, task.description) == (
        PriorityLevel.HIGH,
        4,
        "new",
    )
    with pytest.raises(PlanError):
        project.update_task("B", estimated_time=-1)
    with pytest.raises(PlanError, match="Unknown task"):
        project.update_task("missing", description="x")


def test_remove_with_bridge_keeps_ordering() -> None:
    """Removing B connects its parents to its children."""
    project = make_diamond()
    project.remove_task("B")
    assert project.tasks["D"].depends_on == {"A", "C"}


def test_remove_without_bridge_drops_edges() -> None:
    """Without bridging, B's edges simply disappear."""
    project = make_diamond()
    project.remove_task("B", bridge=False)
    assert project.tasks["D"].depends_on == {"C"}
    assert "B" not in project.tasks


def test_walk_diamond_with_next_tasks() -> None:
    """Completing tasks unlocks their dependents, and nothing starts early."""
    project = make_diamond()
    assert project.next_tasks() == ["A"]
    with pytest.raises(PlanError, match="waiting on A"):
        project.set_status("B", Status.IN_PROGRESS)
    project.set_status("A", Status.COMPLETED)
    assert sorted(project.next_tasks()) == ["B", "C"]
    project.set_status("B", Status.COMPLETED)
    project.set_status("C", Status.COMPLETED)
    assert project.next_tasks() == ["D"]
    project.set_status("D", Status.COMPLETED)
    assert project.next_tasks() == []


def test_actual_time_from_timestamps_or_override() -> None:
    """Actual hours come from start/finish times unless given explicitly."""
    start = datetime(2026, 1, 1, 9, tzinfo=UTC)
    project = make_diamond()
    project.set_status("A", Status.IN_PROGRESS, now=start)
    project.set_status("A", Status.COMPLETED, now=start + timedelta(hours=2))
    assert project.tasks["A"].actual_time == pytest.approx(2)

    project.set_status("B", Status.COMPLETED, actual_hours=1.5)
    assert project.tasks["B"].actual_time == 1.5

    project.set_status("C", Status.COMPLETED)
    assert project.tasks["C"].actual_time is None

    project.set_status("A", Status.NOT_STARTED)
    task = project.tasks["A"]
    assert (task.started_at, task.completed_at, task.actual_time) == (None, None, None)


@pytest.mark.parametrize(
    ("status", "hours"), [(Status.IN_PROGRESS, 1.0), (Status.COMPLETED, 0.0)]
)
def test_invalid_actual_hours_rejected(status: Status, hours: float) -> None:
    """actual_hours is only for completion and must be positive."""
    with pytest.raises(PlanError, match="actual_hours"):
        make_diamond().set_status("A", status, actual_hours=hours)


def test_save_and_load_round_trip(tmp_path: Path) -> None:
    """Every field, edge, status and timestamp survives a save and load."""
    start = datetime(2026, 1, 1, 9, tzinfo=UTC)
    project = make_diamond()
    project.set_status("A", Status.IN_PROGRESS, now=start)
    project.set_status("A", Status.COMPLETED, now=start + timedelta(hours=3))
    path = tmp_path / "project.json"
    project.save(path)
    assert Project.load(path).tasks == project.tasks


def test_load_missing_file_gives_empty_project(tmp_path: Path) -> None:
    """A project file that doesn't exist yet means an empty plan."""
    assert Project.load(tmp_path / "nope.json").tasks == {}


def make_timed_diamond() -> Project:
    """Diamond with estimates A=1, B=2, C=5, D=1."""
    project = make_diamond()
    for name, hours in {"A": 1, "B": 2, "C": 5, "D": 1}.items():
        project.update_task(name, estimated_time=hours)
    return project


def test_eta_is_the_longest_path() -> None:
    """D can't finish before its slowest chain A -> C -> D: 1 + 5 + 1 = 7."""
    project = make_timed_diamond()
    assert project.etas() == {"A": 1, "B": 3, "C": 6, "D": 7}
    project.set_status("A", Status.COMPLETED)
    assert project.etas()["D"] == 6
    assert "eta 7h" in make_timed_diamond().render()


@pytest.mark.parametrize(
    ("budget", "expected"), [(4, ["A", "B"]), (9, ["A", "B", "C", "D"]), (0.5, [])]
)
def test_budget_selection(budget: float, expected: list[str]) -> None:
    """Greedy picks respect dependencies and never exceed the budget."""
    project = make_timed_diamond()
    picked = project.next_tasks(budget=budget)
    assert picked == expected
    assert sum(project.tasks[n].estimated_time for n in picked) <= budget
    for i, name in enumerate(picked):
        assert project.tasks[name].depends_on <= set(picked[:i])


def test_non_positive_budget_is_rejected() -> None:
    """A budget must be a positive number of hours."""
    with pytest.raises(PlanError, match="budget"):
        make_diamond().next_tasks(budget=0)


def test_time_report_compares_estimates_with_actuals() -> None:
    """Per-task ratios, an overall ratio and a bias-adjusted remaining estimate."""
    project = make_diamond()
    project.update_task("A", estimated_time=2)
    project.set_status("A", Status.COMPLETED, actual_hours=3)
    project.set_status("B", Status.COMPLETED, actual_hours=0.5)
    project.set_status("C", Status.COMPLETED)
    report = project.time_report()
    assert "A  est 2h  actual 3h  ×1.50" in report
    assert "C  est 1h  no timing data" in report
    assert "3.5h actual vs 3h estimated (×1.17)" in report  # (3 + 0.5) / (2 + 1)
    assert "1h estimated, ~1.2h at that rate" in report


def test_time_report_without_completed_tasks() -> None:
    """With nothing finished there is nothing to compare yet."""
    assert "No completed tasks yet" in make_diamond().time_report()


def test_save_creates_missing_folders(tmp_path: Path) -> None:
    """The plugin saves to <project>/.claude/, which may not exist yet."""
    path = tmp_path / ".claude" / "topo-plan.json"
    make_diamond().save(path)
    assert Project.load(path).tasks.keys() == {"A", "B", "C", "D"}
