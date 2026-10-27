---
name: topo-plan
description: Plan multi-step work as a dependency graph with the topo MCP tools before doing it, then work through it in dependency order, highest time-discounted value first. Use when the user asks to plan, break down or schedule work, when a request has several steps that depend on each other, when time is limited and you must choose what to do first, or when resuming work on an existing plan.
---

# Planning with topo

The `topo` MCP server keeps one dependency graph of tasks per project, saved to
`.claude/topo-plan.json` after every change. Plan first, agree on the plan with the user, then
work through it.

## 1. Start from what exists

Call `topo_get_plan`. If it has unfinished tasks, resume that plan unless the user wants a new
one. Remove tasks that no longer apply with `topo_remove_task`.

## 2. Build the plan

Add each piece of work with `topo_add_task`:

- `name`: short kebab-case ID such as `design-schema`. Every other tool refers to it.
- `estimated_time`: hours for this task alone, not including what it depends on.
- `priority`: `high` for what the user cares about most, `low` for nice-to-haves.
- `depends_on`: tasks that must finish first. `dependents`: existing tasks that must wait for this one.
  Add foundations first, or add later tasks first and point back to them with `dependents`.
- Insert X between A and B with `depends_on=[A]` and `dependents=[B]`. The direct A→B link is dropped
  because X now carries it.

Keep tasks small enough to finish in one sitting (roughly ≤ 4h). Split anything bigger.

## 3. Reshape it

- `topo_update_task` changes fields. Passing `depends_on` or `dependents` **replaces** that side's
  links, which is how you move a task. Pass `[]` to clear a side.
- `topo_remove_task` removes a task. With `bridge=true` (the default), whatever waited for it now waits for its
  dependencies, so the ordering is kept.

Show the user the result of `topo_get_plan` and get agreement before starting a large plan.

## 4. Work through it

1. Call `topo_next_tasks` and take the top task unless there is a reason not to.
2. Call `topo_set_status` with `in_progress`, do the work, then call it again with `completed`. Pass
   `actual_hours` when you know the real effort; otherwise the wall-clock time is used, which
   includes idle time.
3. The reply lists what is ready next. Repeat.

If you discover new work, add it to the plan instead of silently doing it. If the user has
limited time, call `topo_next_tasks` with `budget` (hours) to get an ordered list that fits.

When the work is done, or the user asks how the estimates held up, call `topo_time_report`.

## Reading the plan

Each line reads `status name PRIORITY est Xh eta Yh V=Z ← depends on → depended on by`:

- `[ ]` not started, `[~]` in progress, `[x]` done.
- Stages run top to bottom. Tasks in the same stage don't depend on each other.
- `eta`: earliest finish in hours from now. This is the longest chain of unfinished work leading to the task.
- `V`: time-discounted value, `γ^(est/τ)·(priority + best V it unblocks)`, where τ is the average estimate.
  A higher V means do it sooner. Short tasks and blockers of important work score higher.
  Lower `gamma` (default 0.9) favours quick wins even more.

## When a tool refuses

Errors explain the problem: an unknown task name, a change that would create a cycle, or a task
still waiting on its dependencies. Fix the cause (add the missing task, move the task, or finish
its dependencies) rather than working around the order.
