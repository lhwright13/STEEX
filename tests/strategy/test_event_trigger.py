"""Tests for the deterministic event-trigger core."""

import pytest

from config.settings import get_settings
from src.data.event_source import NewsEvent, EventSource
from src.data.sentiment import SentimentProvider
from src.agents.conclusions import EventTickerResolution
from src.strategy.event_trigger import EventTrigger


class FixtureSource(EventSource):
    def __init__(self, events):
        self._events = events
    def poll(self):
        return self._events


class _Asset:
    def __init__(self, tradable=True):
        self.tradable = tradable


class StubBroker:
    def __init__(self, tradable=True, open_=True):
        self._tradable = tradable
        self._open = open_
    def get_asset(self, ticker):
        return _Asset(self._tradable)
    def get_clock(self):
        return {"is_open": self._open}


class StubMgr:
    def __init__(self, regime="risk_on", entries=True, held=None, tradable=True, price=100.0):
        self._regime = {"name": regime, "entries_allowed": entries}
        self._held = held or set()
        self.price_provider = self
        self.position_manager = self
        self.broker = StubBroker(tradable=tradable)
        self._price = price
        self.bought = []
    def get_regime(self):
        return self._regime
    def get_latest_price(self, t):
        return self._price
    def get_position(self, t):
        return object() if t in self._held else None
    def _get_portfolio_value(self):
        return 50000.0
    def _get_cash(self):
        return 30000.0
    def execute_entries(self, buy_list, dry_run=True, auto_confirm=True):
        self.bought.extend(b["ticker"] for b in buy_list)
        return buy_list


def _trigger(mgr, tmp_path):
    s = get_settings()
    s.data_dir = str(tmp_path)
    return EventTrigger(mgr, s, FixtureSource([]), SentimentProvider()), s


def _truth_event(pid, headline):
    return NewsEvent(id=pid, ticker="", headline=headline, url="u",
                     published_at="2026-05-29T18:00:00Z", source="truth_social")


def _resolver(mapping):
    """mapping: headline-substring -> (ticker, bullish, confidence)."""
    def resolve(ev):
        for sub, (tk, bull, conf) in mapping.items():
            if sub in ev.headline:
                return EventTickerResolution(mentions_company=True, company_name=tk,
                                             ticker=tk, is_bullish=bull, confidence=conf,
                                             reasoning="t")
        return EventTickerResolution(mentions_company=False, is_bullish=False,
                                     confidence=0.0, reasoning="no company")
    return resolve


def test_bullish_company_post_executes(tmp_path):
    mgr = StubMgr()
    trig, _ = _trigger(mgr, tmp_path)
    trig.source = FixtureSource([_truth_event("1", "Go out and buy a Dell")])
    res = trig.run(dry_run=True, resolver=_resolver({"Dell": ("DELL", True, 0.9)}))
    assert res["executed"] and res["executed"][0]["ticker"] == "DELL"
    assert mgr.bought == ["DELL"]


def test_political_post_skipped(tmp_path):
    mgr = StubMgr()
    trig, _ = _trigger(mgr, tmp_path)
    trig.source = FixtureSource([_truth_event("1", "The Radical Left judges are terrible")])
    res = trig.run(dry_run=True, resolver=_resolver({}))
    assert res["executed"] == []


def test_low_confidence_skipped(tmp_path):
    mgr = StubMgr()
    trig, _ = _trigger(mgr, tmp_path)
    trig.source = FixtureSource([_truth_event("1", "maybe buy Acme")])
    res = trig.run(dry_run=True, resolver=_resolver({"Acme": ("ACME", True, 0.3)}))
    assert res["executed"] == []


def test_untradable_ticker_skipped(tmp_path):
    mgr = StubMgr(tradable=False)
    trig, _ = _trigger(mgr, tmp_path)
    trig.source = FixtureSource([_truth_event("1", "buy a Dell")])
    res = trig.run(dry_run=True, resolver=_resolver({"Dell": ("DELL", True, 0.9)}))
    assert res["executed"] == []
    assert any("not tradable" in s.get("reason", "") for s in res["skipped"])


def test_crisis_regime_blocks_everything(tmp_path):
    mgr = StubMgr(regime="crisis", entries=False)
    trig, _ = _trigger(mgr, tmp_path)
    trig.source = FixtureSource([_truth_event("1", "buy a Dell")])
    res = trig.run(dry_run=True, resolver=_resolver({"Dell": ("DELL", True, 0.9)}))
    assert res["executed"] == []
    assert res["scanned"] == 0  # short-circuits before polling


def test_already_held_skipped(tmp_path):
    mgr = StubMgr(held={"DELL"})
    trig, _ = _trigger(mgr, tmp_path)
    trig.source = FixtureSource([_truth_event("1", "buy a Dell")])
    res = trig.run(dry_run=True, resolver=_resolver({"Dell": ("DELL", True, 0.9)}))
    assert res["executed"] == []


def test_daily_cap_enforced_on_real_trades(tmp_path):
    mgr = StubMgr()
    trig, s = _trigger(mgr, tmp_path)
    s.max_event_trades_per_day = 1
    trig.source = FixtureSource([
        _truth_event("1", "buy a Dell"),
        _truth_event("2", "buy some Acme"),
    ])
    res = trig.run(dry_run=False, resolver=_resolver({
        "Dell": ("DELL", True, 0.9), "Acme": ("ACME", True, 0.9),
    }))
    # Only the first should fill; the cap stops the second.
    assert len(res["executed"]) == 1
    assert any("daily event cap" in s.get("reason", "") for s in res["skipped"])


def test_dry_run_does_not_pollute_ledger(tmp_path):
    mgr = StubMgr()
    trig, _ = _trigger(mgr, tmp_path)
    trig.source = FixtureSource([_truth_event("1", "buy a Dell")])
    trig.run(dry_run=True, resolver=_resolver({"Dell": ("DELL", True, 0.9)}))
    # Ledger must stay empty after a dry run so the first live session isn't throttled.
    assert trig._load_trades() == []


def test_no_resolver_skips_untickered(tmp_path):
    mgr = StubMgr()
    trig, _ = _trigger(mgr, tmp_path)
    trig.source = FixtureSource([_truth_event("1", "buy a Dell")])
    res = trig.run(dry_run=True, resolver=None)
    assert res["executed"] == []


def test_kill_switch_disarms_event_path(tmp_path):
    from src.strategy.control import set_controls
    mgr = StubMgr()
    trig, s = _trigger(mgr, tmp_path)
    set_controls(str(tmp_path), event_armed=False)
    trig.source = FixtureSource([_truth_event("1", "buy a Dell")])
    res = trig.run(dry_run=False, resolver=_resolver({"Dell": ("DELL", True, 0.9)}))
    assert res["executed"] == []
    assert any("disarmed" in x.get("reason", "") for x in res["skipped"])
