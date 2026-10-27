"""Tests for the Project graph: building, sorting, scoring, timing and persistence."""

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
    assert project.dependents_of("A") == {"B"}


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
