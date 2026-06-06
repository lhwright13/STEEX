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


def get_settings_only() -> Settings:
    """Resolve session settings (with the paper/live/no-broker flags applied)
    WITHOUT constructing the QuantManager or its broker.

    Pure tools that only need settings — e.g. the notification tool — use this so
    a broker initialization failure can't take down work that never touches the
    broker. Caches on the shared `settings` global, same as init_manager.
    """
    global settings
    if settings is not None:
        return settings

    s = get_settings()
    if args.paper:
        s.broker_enabled = True
        s.broker_paper = True
    elif args.live:
        s.broker_enabled = True
        s.broker_paper = False
    elif args.no_broker:
        s.broker_enabled = False
    settings = s
    return s


def init_manager() -> QuantManager:
    """Lazy-init the QuantManager on first tool call."""
    global manager
    if manager is not None:
        return manager
    manager = QuantManager(settings=get_settings_only())
    return manager
