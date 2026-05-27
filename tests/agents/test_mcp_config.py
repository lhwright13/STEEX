"""Tests for MCP server config generation, especially broker-mode safety.

Regression guard: the STEEX MCP server must never be launched against the
live Alpaca endpoint unless the broker is explicitly enabled AND configured
non-paper. A bare run (no flags) defaults to broker_enabled=False /
broker_paper=True, which previously launched --live and produced Alpaca auth
error 40110000 against paper keys.
"""

import json
import os
from pathlib import Path

import pytest

from config.settings import get_settings
from src.agents.nodes import get_mcp_config
from src.agents.state import RunnerContext


def _steex_args(broker_enabled: bool, broker_paper: bool) -> list:
    settings = get_settings()
    settings.broker_enabled = broker_enabled
    settings.broker_paper = broker_paper
    ctx = RunnerContext(
        settings=settings,
        paper=False,  # bare flag intentionally False to prove it is not consulted
        dry_run=True,
        auto_confirm=False,
        verbose=False,
        registry=None,
        evolver=None,
        project_root=Path("."),
    )
    ctx.mcp_config_path = None
    path = get_mcp_config(ctx)
    try:
        cfg = json.load(open(path))
    finally:
        os.unlink(path)
    return [a for a in cfg["mcpServers"]["steex"]["args"] if a.startswith("--")]


def test_default_run_never_goes_live():
    """No broker enabled (shipped default) → --no-broker, never --live."""
    args = _steex_args(broker_enabled=False, broker_paper=True)
    assert "--no-broker" in args
    assert "--live" not in args


def test_paper_broker_uses_paper_endpoint():
    args = _steex_args(broker_enabled=True, broker_paper=True)
    assert "--paper" in args
    assert "--live" not in args


def test_live_only_when_explicitly_enabled_and_non_paper():
    args = _steex_args(broker_enabled=True, broker_paper=False)
    assert "--live" in args
