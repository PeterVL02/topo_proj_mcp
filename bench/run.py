"""Benchmark topo-planner against a prose planning skill and no planning aid.

Runs headless Claude Code (`claude -p`) for every prompt in prompts.json under three
setups, records token use, cost, and whether the planning aid triggered, and writes
bench/results/<label>.json plus a combined bench/results.md.

Usage:
    uv run python bench/run.py --label baseline
    uv run python bench/run.py --label dry --only auth,trivial-temp --runs 1
"""

import argparse
import json
import re
import shutil
import statistics
import subprocess
import tempfile
import threading
import time
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal, NotRequired, TypedDict

BENCH_DIR = Path(__file__).parent
REPO = BENCH_DIR.parent
ARMS = ("none", "prose", "topo", "prose-forced", "topo-forced")
TOPO_TOOL_PREFIX = "mcp__plugin_topo-planner_topo__"
DEFAULT_PROSE_SKILL_GLOB = (
    ".claude/plugins/cache/addy-agent-skills/agent-skills/*/skills/"
    "planning-and-task-breakdown/SKILL.md"
)
# Forced setups start the prompt with the skill's slash command, so the skill always runs.
FORCE_COMMANDS: dict[str, str] = {
    "prose-forced": "/prose:planning-and-task-breakdown",
    "topo-forced": "/topo-planner:topo-plan",
}

# The prose skill writes its plan to tasks/plan.md and tasks/todo.md.
PROSE_PLAN_FILE = re.compile(r"(^|/)tasks/(plan|todo)\.md$")
# A task line in todo.md: "## Task 3: x", "- [ ] **Task 3: x**" or "- [x] Task 3: x".
TASK_LINE = re.compile(r"^(?:- \[[ xX]\]\s*|#+\s*)\**Task \d+")
RATE_LIMIT = re.compile(
    r"hit your (session|usage|weekly) limit|usage limit reached", re.IGNORECASE
)

type Arm = Literal["none", "prose", "topo", "prose-forced", "topo-forced"]
type BaseArm = Literal["none", "prose", "topo"]
type Kind = Literal["plan", "trivial", "execute"]


def base_arm(arm: Arm) -> BaseArm:
    """Return the setup whose plugin and metrics a (possibly forced) setup shares."""
    return arm.removesuffix("-forced")  # type: ignore[return-value]


def arm_prompt(arm: Arm, text: str) -> str:
    """Return the prompt as sent for this setup."""
    return f"{FORCE_COMMANDS[arm]} {text}" if arm in FORCE_COMMANDS else text


def applies(arm: Arm, kind: Kind) -> bool:
    """Forced setups only make sense on prompts that call for planning."""
    return arm not in FORCE_COMMANDS or kind != "trivial"


class Prompt(TypedDict):
    id: str
    kind: Kind
    prompt: str
    dag: NotRequired[dict[str, list[str]]]


class ToolCall(TypedDict):
    name: str
    input: dict[str, object]


class ParsedStream(TypedDict):
    tool_calls: list[ToolCall]
    cost_usd: float | None
    input_tokens: int | None
    output_tokens: int | None
    turns: int | None
    error: str | None


class RunRecord(TypedDict):
    arm: Arm
    kind: Kind
    prompt_id: str
    run: int
    cost_usd: float | None
    input_tokens: int | None
    output_tokens: int | None
    turns: int | None
    duration_s: float
    triggered: bool
    tool_calls: list[str]
    tasks_in_plan: int | None
    order_violations: int | None
    error: str | None


def parse_stream(lines: Iterable[str]) -> ParsedStream:
    """Extract tool calls, token counts and cost from `claude -p` stream-json output.

    Input tokens include cache reads and writes, since they all occupy context.
    """
    parsed: ParsedStream = {
        "tool_calls": [],
        "cost_usd": None,
        "input_tokens": None,
        "output_tokens": None,
        "turns": None,
        "error": "no result line (crash or timeout)",
    }
    for line in lines:
        if not line.strip():
            continue
        message = json.loads(line)
        if message.get("type") == "assistant":
            for block in message["message"].get("content", []):
                if block.get("type") == "tool_use":
                    parsed["tool_calls"].append(
                        {"name": block["name"], "input": block.get("input", {})}
                    )
        elif message.get("type") == "result":
            usage = message.get("usage", {})
            parsed["input_tokens"] = sum(
                usage.get(key, 0)
                for key in (
                    "input_tokens",
                    "cache_creation_input_tokens",
                    "cache_read_input_tokens",
                )
            )
            parsed["output_tokens"] = usage.get("output_tokens", 0)
            parsed["cost_usd"] = message.get("total_cost_usd")
            parsed["turns"] = message.get("num_turns")
            parsed["error"] = (
                str(message.get("result")) if message.get("is_error") else None
            )
    return parsed


def triggered(arm: Arm, tool_calls: list[ToolCall]) -> bool:
    """Return whether the setup's own planning aid was used during the run."""
    arm = base_arm(arm)
    for call in tool_calls:
        skill = str(call["input"].get("skill", "")) if call["name"] == "Skill" else ""
        if arm == "topo" and (
            call["name"].startswith(TOPO_TOOL_PREFIX) or "topo-plan" in skill
        ):
            return True
        path = str(call["input"].get("file_path", "")).replace("\\", "/")
        if arm == "prose" and (
            "planning-and-task-breakdown" in skill or PROSE_PLAN_FILE.search(path)
        ):
            return True
    return False


def count_plan_tasks(arm: Arm, workdir: Path) -> int | None:
    """Return the number of tasks in the plan the run left behind, if any."""
    arm = base_arm(arm)
    if arm == "topo":
        plan = workdir / ".claude" / "topo-plan.json"
        if plan.exists():
            return len(json.loads(plan.read_text(encoding="utf-8"))["tasks"])
    if arm == "prose":
        todo = workdir / "tasks" / "todo.md"
        if todo.exists():
            lines = todo.read_text(encoding="utf-8").splitlines()
            return sum(bool(TASK_LINE.match(line)) for line in lines)
    return None


def order_violations(workdir: Path, dag: dict[str, list[str]]) -> int:
    """Count files missing or written before one of their dependencies."""
    violations = 0
    for name, deps in dag.items():
        path = workdir / name
        if not path.exists():
            violations += 1
            continue
        mtime = path.stat().st_mtime
        violations += sum(
            1
            for dep in deps
            if (workdir / dep).exists() and (workdir / dep).stat().st_mtime > mtime
        )
    return violations


def median(values: list[float]) -> float | None:
    """Return the median, or None for an empty list."""
    return statistics.median(values) if values else None


def summarize(records: list[RunRecord]) -> str:
    """Return a markdown table of medians per setup and prompt kind."""
    header = (
        "| setup | kind | runs | triggered | median $ | median input tok "
        "| median output tok | median turns | median tasks | order violations | errors |\n"
        "|---|---|---|---|---|---|---|---|---|---|---|"
    )
    rows = [header]
    groups = sorted({(r["arm"], r["kind"]) for r in records})
    for arm, kind in groups:
        group = [r for r in records if r["arm"] == arm and r["kind"] == kind]
        ok = [r for r in group if not r["error"]]

        def fmt(values: list[float], spec: str) -> str:
            """Format a median or a dash."""
            value = median(values)
            return "-" if value is None else format(value, spec)

        trigger = (
            "-"
            if base_arm(arm) == "none"
            else f"{sum(r['triggered'] for r in group) / len(group):.0%}"
        )
        violations = [
            r["order_violations"] for r in group if r["order_violations"] is not None
        ]
        rows.append(
            f"| {arm} | {kind} | {len(group)} | {trigger} "
            f"| {fmt([r['cost_usd'] for r in ok if r['cost_usd'] is not None], '.3f')} "
            f"| {fmt([r['input_tokens'] for r in ok if r['input_tokens'] is not None], ',.0f')} "
            f"| {fmt([r['output_tokens'] for r in ok if r['output_tokens'] is not None], ',.0f')} "
            f"| {fmt([r['turns'] for r in ok if r['turns'] is not None], '.0f')} "
            f"| {fmt([r['tasks_in_plan'] for r in group if r['tasks_in_plan'] is not None], '.0f')} "
            f"| {sum(violations) if violations else '-'} "
            f"| {len(group) - len(ok)} |"
        )
    return "\n".join(rows)


def build_prose_plugin(skill_path: Path, into: Path) -> Path:
    """Wrap the installed prose planning skill in a throwaway single-skill plugin."""
    target = into / "prose" / "skills" / "planning-and-task-breakdown"
    target.mkdir(parents=True)
    shutil.copy(skill_path, target / "SKILL.md")
    return into / "prose"


def run_one(
    arm: Arm,
    prompt: Prompt,
    run: int,
    model: str,
    plugin_dirs: dict[str, Path],
    raw_dir: Path,
    timeout: int,
) -> RunRecord:
    """Run one headless session in a fresh folder and measure it."""
    workdir = Path(tempfile.mkdtemp(prefix=f"topo-bench-{arm}-"))
    claude = shutil.which("claude") or "claude"
    command = [
        claude, "-p", arm_prompt(arm, prompt["prompt"]), "--model", model,
        "--output-format", "stream-json", "--verbose",
        "--setting-sources", "project,local", "--no-session-persistence",
        "--permission-mode", "acceptEdits",
    ]  # fmt: skip
    if base_arm(arm) in plugin_dirs:
        command += ["--plugin-dir", str(plugin_dirs[base_arm(arm)])]
    if base_arm(arm) == "topo":
        command += ["--allowedTools", f"{TOPO_TOOL_PREFIX}*"]
    start = time.monotonic()
    try:
        completed = subprocess.run(
            command, check=False, cwd=workdir, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )  # fmt: skip
        output = completed.stdout
    except subprocess.TimeoutExpired as expired:
        output = expired.stdout or ""
        if isinstance(output, bytes):
            output = output.decode("utf-8", "replace")
    duration = time.monotonic() - start

    stem = f"{arm}-{prompt['id']}-{run}"
    (raw_dir / f"{stem}.jsonl").write_text(output, encoding="utf-8")
    for artifact in (".claude/topo-plan.json", "tasks/plan.md", "tasks/todo.md"):
        if (workdir / artifact).exists():
            (raw_dir / stem).mkdir(exist_ok=True)
            shutil.copy(workdir / artifact, raw_dir / stem / Path(artifact).name)

    parsed = parse_stream(output.splitlines())
    record: RunRecord = {
        "arm": arm,
        "kind": prompt["kind"],
        "prompt_id": prompt["id"],
        "run": run,
        "cost_usd": parsed["cost_usd"],
        "input_tokens": parsed["input_tokens"],
        "output_tokens": parsed["output_tokens"],
        "turns": parsed["turns"],
        "duration_s": round(duration, 1),
        "triggered": triggered(arm, parsed["tool_calls"]),
        "tool_calls": [call["name"] for call in parsed["tool_calls"]],
        "tasks_in_plan": count_plan_tasks(arm, workdir),
        "order_violations": (
            order_violations(workdir, prompt["dag"]) if "dag" in prompt else None
        ),
        "error": parsed["error"],
    }
    shutil.rmtree(workdir, ignore_errors=True)
    print(
        f"{stem}: ${record['cost_usd']} triggered={record['triggered']} "
        f"tasks={record['tasks_in_plan']} error={record['error']}",
        flush=True,
    )
    return record


def is_rate_limited(error: str | None) -> bool:
    """Return whether a run failed because the account hit its usage limit."""
    return bool(error) and bool(RATE_LIMIT.search(error or ""))


def run_key(record: RunRecord) -> tuple[str, str, int]:
    """Identify a run by setup, prompt and repetition."""
    return (record["arm"], record["prompt_id"], record["run"])


def finished_keys(records: list[RunRecord]) -> set[tuple[str, str, int]]:
    """Return the runs that need no retry: everything except rate-limited ones."""
    return {run_key(r) for r in records if not is_rate_limited(r["error"])}


def merge_records(existing: list[RunRecord], new: list[RunRecord]) -> list[RunRecord]:
    """Combine earlier and new results; rate-limited runs are dropped so they rerun."""
    merged = {run_key(r): r for r in existing}
    merged.update((run_key(r), r) for r in new)
    return [r for r in merged.values() if not is_rate_limited(r["error"])]


def write_results_md() -> None:
    """Regenerate bench/results.md from every saved results file."""
    sections = [
        "# Benchmark results\n",
        "Generated by `bench/run.py`. Medians per setup.\n",
    ]
    for path in sorted((BENCH_DIR / "results").glob("*.json")):
        records: list[RunRecord] = json.loads(path.read_text(encoding="utf-8"))
        sections.append(f"## {path.stem}\n\n{summarize(records)}\n")
    (BENCH_DIR / "results.md").write_text("\n".join(sections), encoding="utf-8")


def main() -> None:
    """Parse arguments, run every (setup, prompt, run) job and save the results."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--label", required=True, help="results name, e.g. baseline")
    parser.add_argument("--runs", type=int, default=2)
    parser.add_argument("--model", default="sonnet")
    parser.add_argument("--arms", default=",".join(ARMS))
    parser.add_argument("--only", help="comma-separated prompt ids")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=900, help="seconds per run")
    parser.add_argument("--topo-plugin", type=Path, default=REPO)
    parser.add_argument("--prose-skill", type=Path, help="path to the prose SKILL.md")
    args = parser.parse_args()

    prompts: list[Prompt] = json.loads(
        (BENCH_DIR / "prompts.json").read_text(encoding="utf-8")
    )
    if args.only:
        prompts = [p for p in prompts if p["id"] in args.only.split(",")]
    arms: list[Arm] = [a for a in ARMS if a in args.arms.split(",")]
    prose_skill = args.prose_skill or max(Path.home().glob(DEFAULT_PROSE_SKILL_GLOB))
    raw_dir = BENCH_DIR / "raw" / args.label
    raw_dir.mkdir(parents=True, exist_ok=True)

    results_file = BENCH_DIR / "results" / f"{args.label}.json"
    existing: list[RunRecord] = (
        json.loads(results_file.read_text(encoding="utf-8"))
        if results_file.exists()
        else []
    )
    done = finished_keys(existing)
    stop = threading.Event()

    with tempfile.TemporaryDirectory() as scratch:
        plugin_dirs = {
            "prose": build_prose_plugin(prose_skill, Path(scratch)),
            "topo": args.topo_plugin.resolve(),
        }

        def run_job(job: tuple[Arm, Prompt, int]) -> RunRecord | None:
            """Run one job unless a usage limit has already stopped the benchmark."""
            if stop.is_set():
                return None
            record = run_one(*job, args.model, plugin_dirs, raw_dir, args.timeout)
            if is_rate_limited(record["error"]):
                stop.set()
            return record

        jobs = [
            (a, p, r)
            for r in range(1, args.runs + 1)
            for p in prompts
            for a in arms
            if applies(a, p["kind"]) and (a, p["id"], r) not in done
        ]
        print(f"{len(jobs)} runs to do ({len(done)} already recorded)", flush=True)
        with ThreadPoolExecutor(args.workers) as pool:
            new = [r for r in pool.map(run_job, jobs) if r is not None]

    records = merge_records(existing, new)
    results_file.parent.mkdir(exist_ok=True)
    results_file.write_text(json.dumps(records, indent=1), encoding="utf-8")
    write_results_md()
    print(summarize(records))
    if stop.is_set():
        print("Stopped early: usage limit reached. Re-run the same command to resume.")


if __name__ == "__main__":
    main()
