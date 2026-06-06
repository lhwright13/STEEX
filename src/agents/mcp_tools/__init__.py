"""STEEX MCP package — split from mcp_server.py (P0-2).

Bootstrap (sys.path, .env, stderr console redirect) must run before any module
imports QuantManager (MCP uses stdout for JSON-RPC), so it lives here.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(Path(__file__).resolve().parents[3] / ".env")

from rich.console import Console  # noqa: E402
import src.strategy.manager as _mgr_mod  # noqa: E402
_mgr_mod.console = Console(stderr=True, quiet=True)

from .server import mcp, main  # noqa: E402,F401  (imports domains -> registers tools)
