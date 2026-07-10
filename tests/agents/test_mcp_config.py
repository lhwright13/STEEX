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
import src.agents.nodes as nodes
from src.agents.nodes import get_mcp_config
from src.agents.state import RunnerContext


def _make_ctx(**overrides) -> RunnerContext:
    settings = get_settings()
    for key, val in overrides.items():
        setattr(settings, key, val)
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
    return ctx


def _config_for(ctx: RunnerContext) -> dict:
    path = get_mcp_config(ctx)
    try:
        return json.load(open(path))
    finally:
        os.unlink(path)


def _steex_args(broker_enabled: bool, broker_paper: bool) -> list:
    ctx = _make_ctx(broker_enabled=broker_enabled, broker_paper=broker_paper)
    cfg = _config_for(ctx)
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


# --- WP6: Alpha Vantage keyless noise ------------------------------------


def test_alphavantage_defaults_off():
    """Key never set on this box → shipped default is disabled.

    Read the model field default rather than the process-wide (lru_cached and
    thus mutation-prone) settings singleton.
    """
    field = type(get_settings()).model_fields["mcp_alphavantage_enabled"]
    assert field.default is False


def test_alphavantage_skipped_and_no_warning_when_disabled(monkeypatch, caplog):
    """Disabled → no server entry, no warning at all."""
    monkeypatch.delenv("ALPHA_VANTAGE_API_KEY", raising=False)
    nodes._av_key_warned = False
    ctx = _make_ctx(mcp_alphavantage_enabled=False)
    with caplog.at_level("WARNING", logger="steex.nodes"):
        cfg = _config_for(ctx)
    assert "alphavantage" not in cfg["mcpServers"]
    assert not any("Alpha Vantage" in r.message for r in caplog.records)


def test_alphavantage_enabled_without_key_skips_and_warns_once(monkeypatch, caplog):
    """Enabled but keyless → skip the server entry and warn only once per run,
    even across many agent invocations (each calls get_mcp_config)."""
    monkeypatch.delenv("ALPHA_VANTAGE_API_KEY", raising=False)
    nodes._av_key_warned = False
    with caplog.at_level("WARNING", logger="steex.nodes"):
        for _ in range(5):
            ctx = _make_ctx(mcp_alphavantage_enabled=True)
            cfg = _config_for(ctx)
            assert "alphavantage" not in cfg["mcpServers"]
    warnings = [r for r in caplog.records if "Alpha Vantage" in r.message]
    assert len(warnings) == 1


def test_alphavantage_enabled_with_key_adds_server(monkeypatch):
    """Key present → server entry included, passing the key as the arg."""
    monkeypatch.setenv("ALPHA_VANTAGE_API_KEY", "test-key-123")
    nodes._av_key_warned = False
    ctx = _make_ctx(mcp_alphavantage_enabled=True)
    cfg = _config_for(ctx)
    assert "alphavantage" in cfg["mcpServers"]
    assert "test-key-123" in cfg["mcpServers"]["alphavantage"]["args"]
