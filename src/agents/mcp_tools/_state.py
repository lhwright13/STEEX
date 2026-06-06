"""Shared MCP session state + lazy manager init (P0-2).

These attributes are written by tools in one domain module and read by tools in
another (screen -> trading -> portfolio), so they live here and are accessed as
`_state.<name>` (rebinding the attribute) — never copy-imported.
"""
import argparse
from typing import Optional

from config.settings import Settings, get_settings
from src.strategy.manager import QuantManager

_parser = argparse.ArgumentParser(description="STEEX MCP Server")
_parser.add_argument("--paper", action="store_true")
_parser.add_argument("--live", action="store_true")
_parser.add_argument("--dry-run", action="store_true")
_parser.add_argument("--no-broker", action="store_true")
args, _ = _parser.parse_known_args()

settings: Optional[Settings] = None
manager: Optional[QuantManager] = None
dry_run: bool = args.dry_run

# pipeline session cache (rebind these attributes; do not copy-import them)
pipeline_result = None
ranked = None
exit_signals = None
regime = None
buy_list = None
sell_list = None


def init_manager() -> QuantManager:
    """Lazy-init the QuantManager on first tool call."""
    global settings, manager
    if manager is not None:
        return manager

    settings = get_settings()
    if args.paper:
        settings.broker_enabled = True
        settings.broker_paper = True
    elif args.live:
        settings.broker_enabled = True
        settings.broker_paper = False
    elif args.no_broker:
        settings.broker_enabled = False

    manager = QuantManager(settings=settings)
    return manager
