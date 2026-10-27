# topo-proj-mcp

An MCP server for planning work as a dependency graph before doing it. Tasks are sorted
topologically into stages, and each stage is ordered by an RL-style time-discounted value.

## Register with Claude Code

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
