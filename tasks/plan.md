# Implementation Plan: topo-proj-mcp

## Overview

An MCP server that lets an agent plan work before doing it. The agent registers tasks along with
what each one depends on and what depends on it, and can insert, move or remove tasks later. The
server sorts the tasks topologically into stages and orders each stage by an RL-style discounted
value. It can also pick tasks under a time budget and track estimated vs actual time. There is one
project per server, persisted to JSON.

## Done so far

- Python 3.14 (`.python-version`, `requires-python >=3.14`).
- Dependencies: `mcp` (runtime), `pytest` and `ruff` (dev).
- `models.py` and `__init__.py` follow the global conventions (types, docstrings, ruff clean). Behavior is unchanged.

## Architecture Decisions

- **Store edges once.** `Task.depends_on: set[str]` (names) is the only record of an edge.
  Dependents are derived from it. Tools still *accept* both lists: `dependents=[X]` on T is stored
  as "X depends on T". The stored `dependents`, `_priority_value` and `discounted_count` are removed.
- **Derived values are computed, never stored.** Discounted value, earliest finish and stage
  number all depend on the current graph, so they're recomputed on each call. The graphs are small,
  so recomputing is cheap.
- **Sort with stdlib `graphlib.TopologicalSorter`.** It detects cycles (`CycleError`) and gives
  stages through `get_ready()`/`done()`.
- **Validate before mutating.** Every edit builds the new edge set, checks it (unknown names,
  cycles), and only then commits. A rejected edit leaves the project unchanged.
- **MCP via `FastMCP`** in `server.py`. `__init__.main()` runs it over stdio.
  **stdout is the protocol channel**, so all logging goes to stderr through stdlib `logging`.
- **Files:** `models.py` (data), `task_sorting.py` (`Project`: graph, sorting, scoring, timing),
  `server.py` (tools), `tests/test_task_sorting.py`.

### Discounted value (decided: Bellman backup, discounted by time)

`V(t) = γ^(est(t)/τ) · (base(t) + max(V(d) for d in dependents(t)))`, computed in reverse
topological order. Completed tasks have `V = 0`.

- The reward for a task arrives when it *finishes*, so it is discounted by that task's own
  duration. Each dependent's value already includes its own discount, so the reward of a task k
  steps down a chain is discounted by the total hours along that chain. This is the semi-MDP form
  of the Bellman equation.
- `τ` is the mean `estimated_time` across the project. Because the exponent is a ratio, it has no
  units: **γ is the discount per average-sized task**, so γ = 0.9 (the default) means "one average
  task of delay costs 10%". This holds whether estimates are in hours or days.
- Short tasks are naturally worth more than long tasks of the same priority, so V already mixes
  priority and time. Tasks that block valuable work inherit a discounted share of its value.

### Graph representation for the model

`get_plan` returns a compact text view. Task names are the IDs, and each line shows a task's
parents (←) and children (→), so a model can see where to insert or move a task without parsing
JSON:

```
Stage 0
  [ ] design-schema   HIGH  est 5.0h  eta 5.0h   V=5.43  → api, migrations
Stage 1
  [~] api             LOW   est 3.0h  eta 8.0h   V=2.70  ← design-schema  → ui
  [ ] migrations      MED   est 1.0h  eta 6.0h   V=2.00  ← design-schema
Stage 2
  [ ] ui              HIGH  est 4.0h  eta 12.0h  V=3.00  ← api
```

`[ ]` means not started, `[~]` in progress, `[x]` completed.

The editing tools are:
- `add_task(..., depends_on, dependents)` covers insert. Inserting between A and B is
  `depends_on=[A], dependents=[B]`.
- `update_task(name, ..., depends_on=None, dependents=None)` covers field edits and **move**.
  When a list is given, it replaces that side's edges.
- `remove_task(name, bridge=True)`: with bridging on, the removed task's parents are connected to
  its children, so the ordering is kept.

### Estimated time

- **`estimated_time` stays the task's own estimate, in hours, and is never overwritten.** Derived
  values are shown next to it.
- **Earliest finish ("eta") uses the *longest* path, not the shortest.** A task can't start until
  *all* its dependencies are done, so the slowest chain is the one that limits it:
  `eta(t) = est(t) + max(eta(dependencies))`. This is the critical path, and it assumes
  dependencies run in parallel. Completed tasks count as 0.
- **Picking under a budget:** `next_tasks(budget=None)` simulates the work. It repeatedly takes
  the ready task with the highest V that still fits the remaining budget, marks it done in the
  simulation, and repeats, so dependencies are respected. V already accounts for duration (see
  above), so no separate value-per-hour score is needed.
- **Comparing actual time with the estimate:** `update_status` records `started_at` and
  `completed_at`. `completed` takes an optional `actual_hours` that overrides the wall-clock time,
  because wall-clock includes idle time. `time_report` lists estimate vs actual per task, plus the
  overall ratio (your estimation bias).

## Task List

### Phase 1: Foundation
- [x] Task 1: MCP server skeleton (`ping`) running from Claude Code
- [x] Task 2: Add tasks and view the plan as staged text

### Phase 2: Core
- [x] Task 3: Discounted value ordering
- [x] Task 4: Edit the graph: update/move/remove

### Checkpoint: A model can build and reshape a plan

### Phase 3: Execution
- [x] Task 5: Progress tracking with timestamps and `next_tasks`
- [x] Task 6: JSON persistence

### Checkpoint: Plans survive restarts, and the full flow works

### Phase 4: Time
- [x] Task 7: Earliest finish (eta) and budgeted `next_tasks`
- [x] Task 8: `time_report`, estimate vs actual

### Checkpoint: Complete

Details for each task are in `tasks/todo.md`.

## Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Logging or printing to stdout corrupts the stdio MCP stream | High | Only `logging` to stderr. Remove the `print` in `main` in Task 1 |
| Wall-clock "actual" time is inflated by idle time between sessions | Med | Optional `actual_hours` override on completion |
| Removing a task silently loses ordering constraints | Med | `bridge=True` by default |
| Greedy budget selection isn't optimal (this is knapsack with dependencies, which is NP-hard) | Low | Greedy is the standard heuristic. Marked with a `ponytail:` comment |

## Open Questions

1. ~~eta model~~: decided, longest path (parallel).
2. ~~Discount by time~~: decided, γ is applied per average-sized task (see Discounted value).

---

# Phase 5: Claude Code plugin

## Overview

Package the repo as a Claude Code plugin that bundles the MCP server and a skill, which teaches
Claude when and how to plan with it. It can be installed from this repo with
`/plugin marketplace add PeterVL02/topo_proj_mcp`.

## Decisions

- **Same repo, plugin root = repo root.** `.claude-plugin/plugin.json` declares the server
  **inline**, not in a root `.mcp.json`, so it doesn't clash with this repo's own project MCP
  config, where `${CLAUDE_PLUGIN_ROOT}` doesn't exist.
- **Server command:** `uv --directory ${CLAUDE_PLUGIN_ROOT} run topo-proj-mcp`. This requires `uv`
  on PATH; uv fetches Python 3.14 and the dependencies on first start.
- **One plan per project:** `TOPO_PROJECT_FILE=${CLAUDE_PROJECT_DIR}/.claude/topo-plan.json`,
  set in the server's `env`. `save()` creates missing folders.
- **Names:** the plugin is `topo-planner` (names may not start with `claude-`). The skill is `topo-plan`.

## Task List

- [x] Task 9: `save()` creates missing parent folders
- [x] Task 10: Plugin manifest, marketplace and `topo-plan` skill, with a skill/tool sync test
- [x] Task 11: Validate the plugin, run a headless end-to-end check, document the install in the README

### Checkpoint: plugin validates and the server starts from the plugin

---

# Phases 6–8: Prove the token and rigidity claims

## Goal

topo-planner exists to replace prose planning skills (such as `agent-skills:planning-and-task-breakdown`)
with something **algorithmic, rigid and cheaper in tokens**. The council found that this has never
been measured. These phases measure it, fix the one known token leak, and end with a decision:
harden the plugin or cut it back.

## Findings (2026-10-27)

- **Rigid: yes.** Ordering, cycle checks and dependency gating run in code. The model still
  chooses the inputs (breakdown, estimates, priorities) and whether to use the tools at all.
- **Tokens:** the skill costs ~880 when triggered, against ~2,640 for the prose skill. The work
  loop is cheap (~25–35 tokens per `next_tasks` / `set_status`). **The leak:** every edit returns
  the full plan. Building a 20-task plan echoes ~4,940 tokens back for a final plan of ~476, so
  the cost is quadratic, and those replies stay in context for the rest of the session.
- **Built-in Task tools** (`TaskCreate` etc., which support dependencies) are **off by default on
  current models**. The docs cite their context cost. The competitor to beat is the prose
  planning skill.

## Decisions

- **Measure before and after the fixes.** The benchmark runs once on the current code (baseline),
  then again after the token fixes, so the gain is shown rather than assumed.
- **The pass/fail bar is set before any run** (see Task 15).
- **Out of scope until the Phase 8 decision:** parallel claim/owner fields, gamma learning, JSON
  output, undo, CI.

## Task List

### Phase 6: Make it testable
- [x] Task 12: A corrupt plan file gives a clear tool error instead of killing the server
- [ ] Task 13: Benchmark harness (headless `claude -p`, three setups, token and trigger metrics), then a baseline run

### Checkpoint: baseline numbers recorded in `bench/results.md`

### Phase 7: Fix the token leak
- [ ] Task 14: Edits return a short confirmation instead of the full plan
- [ ] Task 15: `topo_add_tasks` adds a whole plan in one atomic call; the skill prefers it
- [ ] Task 16: Re-run the benchmark and compare against the baseline

### Checkpoint: decide with the human (Phase 8)
- **Pass** (all of): building the plan costs ≤ 50% of the tokens the prose skill uses for the same spec;
  the skill triggers on ≥ 80% of planning prompts and ≤ 10% of trivial prompts; zero
  dependency-order violations during execution.
  → harden: safety across processes (two sessions in one repo), a one-line legend in
  `get_plan`, measured numbers at the top of the README.
- **Fail** → cut back to the skill alone, or fold the useful parts into the prose skill.
  The cost so far is about one day.

## Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| Model runs vary from one run to the next | Med | ≥ 2 runs per prompt; report the median and the spread |
| The benchmark costs real tokens | Med | Planning-only prompts by default; one full execution run per setup |
| Self-graded results | Med | Metrics come from `--output-format json` usage and tool-call logs, not judgment |
| Two sessions in one repo overwrite each other's plan | Low (for now) | Fixed only if Phase 8 passes; noted in the README |

## Open Questions

1. ~~Model and runs~~: decided, Sonnet 5.5, 10 planning + 5 trivial prompts × 2 runs × 3 setups.
2. ~~Commit the plan file?~~: decided, commit `.claude/topo-plan.json` (git gives history and undo).
