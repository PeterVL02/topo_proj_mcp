"""Consistency checks between the plugin files, pyproject and the MCP server."""

import asyncio
import json
import re
import tomllib
from pathlib import Path

from topo_proj_mcp import server

ROOT = Path(__file__).parent.parent
SKILL = ROOT / "skills" / "topo-plan" / "SKILL.md"


def read_json(relative: str) -> dict[str, object]:
    """Load a JSON file from the repo root."""
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def test_manifest_runs_the_packaged_server_per_project() -> None:
    """The plugin starts our console script and keeps one plan file per project."""
    manifest = read_json(".claude-plugin/plugin.json")
    assert manifest["name"] == "topo-planner"
    config = manifest["mcpServers"]["topo"]  # type: ignore[index]
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert config["command"] == "uv"
    assert "${CLAUDE_PLUGIN_ROOT}" in config["args"]
    assert config["args"][-1] in pyproject["project"]["scripts"]
    assert config["env"]["TOPO_PROJECT_FILE"].startswith("${CLAUDE_PROJECT_DIR}/")


def test_marketplace_lists_this_repo_as_the_plugin() -> None:
    """`/plugin marketplace add PeterVL02/topo_proj_mcp` finds the plugin at the root."""
    marketplace = read_json(".claude-plugin/marketplace.json")
    entries = marketplace["plugins"]
    assert [(e["name"], e["source"]) for e in entries] == [("topo-planner", "./")]  # type: ignore[index, union-attr]


def test_skill_frontmatter() -> None:
    """The skill declares its name and a description Claude uses to decide when to load it."""
    match = re.match(r"---\n(.*?)\n---\n", SKILL.read_text(encoding="utf-8"), re.DOTALL)
    assert match, "SKILL.md must start with YAML frontmatter"
    fields = dict(line.split(": ", 1) for line in match.group(1).splitlines())
    assert fields["name"] == "topo-plan"
    assert len(fields["description"]) > 50


def test_skill_mentions_exactly_the_server_tools() -> None:
    """Fails when a tool is added, renamed or removed without updating the skill."""
    tools = {tool.name for tool in asyncio.run(server.mcp.list_tools())}
    mentioned = set(re.findall(r"\btopo_[a-z_]+", SKILL.read_text(encoding="utf-8")))
    assert mentioned == tools
