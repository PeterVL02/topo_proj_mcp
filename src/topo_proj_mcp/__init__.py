def main() -> None:
    """Run the topo-proj-mcp server over stdio."""
    from topo_proj_mcp.server import mcp

    mcp.run()
