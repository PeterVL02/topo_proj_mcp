# Todo: topo-proj-mcp

See `tasks/plan.md` for the design. Every task finishes with `uv run ruff check`, `uv run ruff format`
and `uv run pytest` all passing.

## Task 1: MCP server skeleton ✅

**Description:** Add `server.py` with a `FastMCP` instance and a `ping` tool. `__init__.main()`
runs the server over stdio. Replace the hello `print`, because stdout is the protocol channel.

**Acceptance criteria:**
- [ ] `uv run topo-proj-mcp` starts a stdio server without printing to stdout
- [ ] `ping` is callable from Claude Code

**Verification:**
- [ ] `claude mcp add topo -- uv --directory C:/Users/pvest/code/topo_proj_mcp run topo-proj-mcp`, then call `ping`

**Dependencies:** None · **Files:** `__init__.py`, `server.py` · **Scope:** XS

## Task 2: Add tasks and view the plan as staged text ✅

**Description:** Make `Task` a dataclass with `depends_on: set[str]`, and drop the stored
`dependents`, `_priority_value` and `discounted_count`. Add `Project` (`dict[str, Task]`) to
`task_sorting.py` with `add_task(task, dependents)`, `stages()` (built on `graphlib`) and
`render()`. Expose the MCP tools `add_task` and `get_plan`.

**Acceptance criteria:**
- [ ] `dependents=[X]` is stored as an edge on X, and dependents are always derived
- [ ] Unknown names, duplicate names and cycles are rejected, and the project stays unchanged
- [ ] `get_plan` shows the stages in the text format from the plan (←/→ edges, status markers)

**Verification:**
- [ ] pytest: a diamond graph (A→B, A→C, B,C→D) gives `[[A],[B,C],[D]]`; a cycle raises; a failed add leaves the project unchanged
- [ ] Manual: add 4 tasks from Claude Code and read `get_plan`

**Dependencies:** 1 · **Files:** `models.py`, `task_sorting.py`, `server.py`, `tests/test_task_sorting.py` · **Scope:** M

## Task 3: Discounted value ordering ✅

**Description:** Add `Project.values(gamma) -> dict[str, float]` using
`V = γ^(est/τ)·(base + max V(dependents))` (τ = mean estimate) in reverse topological order; completed tasks have V = 0.
`get_plan(gamma=0.9)` sorts each stage by V and shows it.

**Acceptance criteria:**
- [ ] Matches hand-computed values: chain LOW→HIGH, both est 1, γ=0.9: V(HIGH) = 2.7, V(LOW) = 0.9·(1 + 2.7) = 3.33
- [ ] A LOW task blocking a HIGH task outranks a MEDIUM task with no dependents in the same stage
- [ ] γ outside [0, 1] is rejected

**Verification:** pytest for the cases above · **Dependencies:** 2 · **Files:** `task_sorting.py`, `server.py`, tests · **Scope:** S

## Task 4: Edit the graph ✅

**Description:** Add the tools `update_task(name, description?, priority?, estimated_time?,
depends_on?, dependents?)` (a given list replaces that side's edges, which is how a task is moved)
and `remove_task(name, bridge=True)`.

**Acceptance criteria:**
- [ ] Moving a task that would create a cycle is rejected, and the graph stays unchanged
- [ ] `remove_task` with bridging keeps every parent ordered before every child; without bridging it just drops the edges
- [ ] Inserting between A and B with `add_task(depends_on=[A], dependents=[B])` renders correctly

**Verification:** pytest for each case; manual move and insert from Claude Code · **Dependencies:** 2 · **Files:** `task_sorting.py`, `server.py`, tests · **Scope:** S

## Checkpoint: A model can build and reshape a plan
- [ ] Ruff clean, pytest green
- [ ] Manual: have Claude plan a small real project through the tools, then reshape it
- [ ] Review with human

## Task 5: Progress tracking ✅

**Description:** Add the tool `update_status(name, status, actual_hours=None)`. It sets
`started_at`/`completed_at` (UTC) and logs the transition to stderr. Add
`next_tasks(gamma=0.9)`: the tasks that are not completed and whose dependencies are all completed,
ranked by V.

**Acceptance criteria:**
- [ ] A task with an unfinished dependency never shows up in `next_tasks`
- [ ] Completing a task unlocks its dependents; timestamps and `actual_hours` are stored
- [ ] Unknown name or invalid status gives a clear error

**Verification:** pytest walks the diamond graph using only `next_tasks` and `update_status` · **Dependencies:** 3 · **Files:** `models.py`, `task_sorting.py`, `server.py`, tests · **Scope:** S

## Task 6: JSON persistence ✅

**Description:** Load on startup and save after every change, to `TOPO_PROJECT_FILE` (default
`./project.json`), using stdlib `json` and an atomic write (temp file, then `os.replace`).

**Acceptance criteria:**
- [ ] A save/load round trip is lossless, including timestamps
- [ ] A missing file means an empty project

**Verification:** pytest round trip; manual: restart Claude Code and the plan is still there · **Dependencies:** 5 · **Files:** `task_sorting.py`, `server.py`, tests · **Scope:** S

## Checkpoint: Plans survive restarts, and the full flow works

## Task 7: Eta and budgeted next_tasks ✅

**Description:** Add `Project.etas()` using the longest path (`est + max eta(dependencies)`, with
completed tasks at 0) and show it in `get_plan`. Add `next_tasks(budget_hours=...)`: a greedy
simulation that picks the best `V/est` among ready tasks that still fit the budget.

**Acceptance criteria:**
- [ ] Diamond graph with est A=1, B=2, C=5, D=1 gives eta D = 7
- [ ] With a budget, the selection never includes a task whose dependencies aren't completed or selected earlier, and it never exceeds the budget

**Verification:** pytest · **Dependencies:** 5 · **Files:** `task_sorting.py`, `server.py`, tests · **Scope:** S

## Task 8: Time report ✅

**Description:** Add the tool `time_report()`: for each completed task, the estimate, the actual
time (`actual_hours`, or the wall-clock time between start and completion) and their ratio, plus
the overall ratio of total actual to total estimated time.

**Acceptance criteria:**
- [ ] Tasks without timing data are listed as such rather than crashing the report
- [ ] The overall ratio matches a hand-computed example

**Verification:** pytest · **Dependencies:** 5, 6 · **Files:** `task_sorting.py`, `server.py`, tests · **Scope:** S

## Checkpoint: Complete
- [ ] All acceptance criteria met, ruff clean, pytest green
- [ ] Review with human

# Phase 5: Claude Code plugin

## Task 9: save() creates missing parent folders ✅

**Acceptance:** saving to `<dir>/.claude/topo-plan.json` works when `.claude/` doesn't exist.
**Verification:** pytest · **Files:** `task_sorting.py`, tests · **Scope:** XS

## Task 10: Plugin manifest, marketplace and skill ✅

**Acceptance:**
- [ ] `.claude-plugin/plugin.json` declares the `topo` MCP server inline, with a per-project `TOPO_PROJECT_FILE`
- [ ] `.claude-plugin/marketplace.json` lists the plugin with `"source": "./"`
- [ ] `skills/topo-plan/SKILL.md` has frontmatter (`name`, `description`) and the workflow: plan → review → work → report
- [ ] A test fails if the skill mentions a tool that doesn't exist, or misses one that does

**Verification:** pytest · **Files:** `.claude-plugin/*`, `skills/topo-plan/SKILL.md`, `tests/test_plugin.py` · **Scope:** S

## Task 11: Validate, end-to-end check, docs ✅

**Acceptance:**
- [ ] `claude plugin validate .` passes
- [ ] Headless `claude -p --plugin-dir .` can call a `topo_` tool, and the plan file lands in the project's `.claude/`
- [ ] The README documents the plugin install

**Verification:** commands above · **Files:** `README.md` · **Scope:** XS

# Phases 6–8: Prove the token and rigidity claims

## Task 12: Corrupt plan file gives a clear error ✅

**Description:** Loading a broken or invalid plan file must not crash the server at import. Start
with an empty project in memory, keep the broken file untouched, and make every tool return
"Plan file X is unreadable: <reason>. Fix or delete it."

**Acceptance criteria:**
- [ ] Invalid JSON, a missing key, and a cycle in the file all leave the server running
- [ ] Tools explain the problem; the broken file is never overwritten

**Verification:** pytest (in-process tool calls with a corrupt fixture file) · **Dependencies:** None · **Files:** `server.py`, `tests/test_server.py` · **Scope:** XS

## Task 13: Benchmark harness and baseline

**Description:** Add `bench/run.py`, which runs `claude -p --output-format json` for each prompt in
`bench/prompts.json` under three setups:
- **none:** no planning aid
- **prose:** the `agent-skills` planning skill
- **topo:** `--plugin-dir .`

For each run it records input, output and cache tokens, the tool calls made, and whether a
planning tool or skill triggered. It writes `bench/results.md`. Prompts: 10 multi-step planning
specs and 5 trivial requests where planning should *not* trigger. The prompts ask for a plan only
and say not to implement it. One extra end-to-end run per setup executes a small plan, to count
dependency-order violations.

**Acceptance criteria:**
- [ ] One command reproduces the whole table; the per-run raw JSON is kept in `bench/raw/` (gitignored)
- [ ] Order violations are computed from tool and file logs, not judged by hand
- [ ] Baseline results are committed

**Verification:** a dry run with 1 prompt × 3 setups; then the full run · **Dependencies:** 12 · **Files:** `bench/run.py`, `bench/prompts.json`, `bench/results.md`, `.gitignore` · **Scope:** M

## Checkpoint: baseline recorded

## Task 14: Short edit replies ✅

**Description:** `add` / `update` / `remove` / `set_status` reply with a single line describing
the change (for example `added api: stage 2, ← schema, V=1.76`), plus anything that became ready
or blocked. `topo_get_plan` stays the only full view. Update the skill to say so.

**Acceptance criteria:**
- [ ] The edit reply size doesn't grow with the plan size (a test on 5-task vs 50-task plans)
- [ ] The skill still mentions exactly the server's tools (the sync test passes)

**Verification:** pytest · **Dependencies:** 13 · **Files:** `server.py`, `task_sorting.py`, `SKILL.md`, tests · **Scope:** S

## Task 15: Batch add ✅

**Description:** Add `topo_add_tasks(tasks: list[...])`. Tasks in one batch can reference each
other in any order. The whole batch is validated as one graph edit, and it either fully applies or
changes nothing. The reply is the compact plan, sent once. The skill says to build a new plan with
a single batch call.

**Acceptance criteria:**
- [ ] A batch with forward references works; a batch containing a cycle or an unknown name changes nothing
- [ ] Building the 20-task plan from the findings costs ≤ ~1,000 tokens of tool I/O (measured the same way)

**Verification:** pytest · **Dependencies:** 14 · **Files:** `task_sorting.py`, `server.py`, `SKILL.md`, tests · **Scope:** S

## Task 16: Re-run the benchmark

**Acceptance criteria:**
- [ ] `bench/results.md` shows the baseline, the after-fix numbers and the prose skill side by side, judged against the pass bar in plan.md

**Verification:** the full benchmark run · **Dependencies:** 15 · **Files:** `bench/results.md` · **Scope:** XS

## Checkpoint: Phase 8 decision with the human (see the pass/fail bar in plan.md)
