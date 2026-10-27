# topo-proj-mcp

An MCP server for planning work as a dependency graph before doing it. Tasks are sorted
topologically into stages, and each stage is ordered by an RL-style time-discounted value.

## Install as a Claude Code plugin (recommended)

The `topo-planner` plugin bundles the MCP server with a `topo-plan` skill that teaches Claude
when and how to plan with it. Requires [uv](https://docs.astral.sh/uv/) on your PATH; Python 3.14
and the dependencies are fetched on first start.

```
/plugin install topo-planner --marketplace PeterVL02/topo_proj_mcp
```

On Claude Code before 2.1.275, use two steps instead:

```
/plugin marketplace add PeterVL02/topo_proj_mcp
/plugin install topo-planner@topo-planner
```

Each project gets its own plan, at `<project>/.claude/topo-plan.json`. To try a local checkout
without installing it, run `claude --plugin-dir /path/to/topo_proj_mcp`.

## Or register only the MCP server

```sh
claude mcp add topo -e TOPO_PROJECT_FILE=C:/path/to/plan.json -- uv --directory C:/Users/pvest/code/topo_proj_mcp run topo-proj-mcp
```

There is one project per server. It is saved to `TOPO_PROJECT_FILE` (default `./project.json`)
after every change.

## Tools

| Tool | What it does |
|------|--------------|
| `topo_add_task` | Add a task with `depends_on` and/or `dependents`. Insert between A and B with `depends_on=[A], dependents=[B]` |
| `topo_update_task` | Change fields, or move a task: a given edge list replaces that side |
| `topo_remove_task` | Remove a task. By default its dependencies are passed on to its dependents |
| `topo_get_plan` | Staged text view: status, priority, estimate, eta, value, ← parents, → children |
| `topo_set_status` | `not_started` / `in_progress` / `completed` (optionally with `actual_hours`) |
| `topo_next_tasks` | What's ready now, or an ordered pick list that fits a `budget` of hours |
| `topo_time_report` | Estimated vs actual hours, overall ratio and a bias-adjusted forecast |

## How tasks are ranked

`V(t) = γ^(est(t)/τ) · (priority(t) + max V(dependents))`, where τ is the mean estimate.
A task's reward arrives when it finishes, so it is discounted by its own duration, and γ is the
discount per average-sized task (default 0.9). Short tasks, and tasks that unblock valuable work,
rank higher. Completed tasks are worth 0.

`eta` is the earliest finish: the task's own estimate plus the longest chain of unfinished
dependencies.

## Development

```sh
uv sync
uv run pytest
uv run ruff check && uv run ruff format
```
