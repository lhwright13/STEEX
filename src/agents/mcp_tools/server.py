"""FastMCP server instance + stdio entry point (P0-2)."""
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("steex")

# Import domain modules AFTER `mcp` exists so their @mcp.tool() decorators register.
from . import market, portfolio, screen, trading, research, events  # noqa: E402,F401


def main():
    mcp.run()


if __name__ == "__main__":
    main()
