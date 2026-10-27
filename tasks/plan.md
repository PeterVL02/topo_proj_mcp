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
- [ ] Task 10: Plugin manifest, marketplace and `topo-plan` skill, with a skill/tool sync test
- [ ] Task 11: Validate the plugin, run a headless end-to-end check, document the install in the README

### Checkpoint: plugin validates and the server starts from the plugin
