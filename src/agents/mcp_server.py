#!/usr/bin/env python3
"""Compatibility shim — the MCP server was split into the `mcp/` package (P0-2).

The claude CLI launches this path (hardcoded in src/agents/nodes.py) and tests
import tools as `from src.agents.mcp_server import <tool>`; both keep working via
the re-exports below. Shared state lives in src.agents.mcp_tools._state.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.agents.mcp_tools import _state  # noqa: F401,E402  (back-compat access)
from src.agents.mcp_tools.server import mcp, main  # noqa: F401,E402
from src.agents.mcp_tools._util import _safe_json  # noqa: F401,E402
from src.agents.mcp_tools._state import init_manager as _init_manager  # noqa: F401,E402
from src.agents.mcp_tools.screen import VARIANT_PARAMS, REGIME_PARAMS  # noqa: F401,E402
from src.agents.mcp_tools.trading import PAPER_ORDER_MAX_USD  # noqa: F401,E402
from src.agents.mcp_tools.market import *  # noqa: F401,F403
from src.agents.mcp_tools.portfolio import *  # noqa: F401,F403
from src.agents.mcp_tools.screen import *  # noqa: F401,F403
from src.agents.mcp_tools.trading import *  # noqa: F401,F403
from src.agents.mcp_tools.research import *  # noqa: F401,F403

if __name__ == "__main__":
    main()
