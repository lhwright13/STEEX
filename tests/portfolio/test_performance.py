"""Tests for src/portfolio/performance.py window alignment (WP3 / B5).

Covers:
  * 1D bases the day change on the broker's prior-close equity (overnight gap),
    and surfaces a 1-day SPY/alpha from a same-day quote vs SPY's prior close.
  * daily ranges align portfolio and SPY to the same last COMMON complete
    trading day, dropping a dangling newer portfolio bar.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pandas as pd
import pytest

import src.portfolio.performance as perf


# --------------------------------------------------------------------------
# helpers / fakes
# --------------------------------------------------------------------------
def _ts(day: str, hhmm: str = "14:30") -> int:
    """Unix ts (UTC) for an ISO day + HH:MM."""
    dt = datetime.fromisoformat(f"{day}T{hhmm}:00").replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


class _FakeHist:
    def __init__(self, timestamp, equity):
        self.timestamp = timestamp
        self.equity = equity


class _FakeAccount:
    def __init__(self, last_equity):
        self.last_equity = last_equity


class _FakeTradingClient:
    """Scriptable stand-in for alpaca TradingClient."""

    hist = None
    account = None

    def __init__(self, *a, **k):
        pass

    def get_portfolio_history(self, req):
        return type(self).hist

    def get_account(self):
        return type(self).account


def _spy_df(closes_by_date):
    """Build an OHLCV frame indexed by Timestamp with a Close column."""
    idx = pd.DatetimeIndex([pd.Timestamp(d) for d in closes_by_date])
    return pd.DataFrame({"Close": list(closes_by_date.values())}, index=idx)


class _FakePriceProvider:
    df = None
    latest = None

    def get_ohlcv(self, ticker, days=None):
        return type(self).df

    def get_latest_price(self, ticker):
        return type(self).latest


@pytest.fixture(autouse=True)
def _patch_broker(monkeypatch):
    """Patch the alpaca imports the module does lazily."""
    import alpaca.trading.client as tc_mod
    import alpaca.trading.requests as req_mod
    monkeypatch.setattr(tc_mod, "TradingClient", _FakeTradingClient)
    # GetPortfolioHistoryRequest just needs to be constructible.
    monkeypatch.setattr(req_mod, "GetPortfolioHistoryRequest", lambda **k: k)
    monkeypatch.setenv("ALPACA_API_KEY", "k")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "s")
    monkeypatch.setattr("src.data.price.PriceProvider", _FakePriceProvider)
    yield


# --------------------------------------------------------------------------
# 1D — prior-close base + overnight gap + 1-day SPY
# --------------------------------------------------------------------------
def test_1d_bases_day_change_on_prior_close_equity():
    """1D captures the overnight gap: first intraday bar already above prior
    close should count as a positive day, not a flat 0% start."""
    _FakeTradingClient.hist = _FakeHist(
        timestamp=[_ts("2026-07-09", "13:30"), _ts("2026-07-09", "15:00")],
        equity=[10100.0, 10200.0],
    )
    _FakeTradingClient.account = _FakeAccount(last_equity=10000.0)
    _FakePriceProvider.df = _spy_df({})
    _FakePriceProvider.latest = None

    out = perf.portfolio_performance("1D")
    assert out["available"] and out["intraday"]
    # start_equity is the prior close, not the first intraday bar.
    assert out["summary"]["start_equity"] == 10000.0
    # first plotted point already reflects the +1% overnight gap.
    assert out["series"][0]["portfolio_pct"] == 1.0
    # end = 10200/10000 - 1 = +2%.
    assert out["summary"]["portfolio_return_pct"] == 2.0


def test_1d_surfaces_spy_and_alpha_from_same_day_quote():
    """1-day SPY/alpha is populated from a same-day quote vs SPY prior close."""
    _FakeTradingClient.hist = _FakeHist(
        timestamp=[_ts("2026-07-09", "13:30"), _ts("2026-07-09", "15:00")],
        equity=[10050.0, 10200.0],
    )
    _FakeTradingClient.account = _FakeAccount(last_equity=10000.0)
    # SPY prior close = 500 (yesterday); today's quote = 505 -> +1%.
    _FakePriceProvider.df = _spy_df({"2026-07-07": 498.0, "2026-07-08": 500.0})
    _FakePriceProvider.latest = 505.0

    out = perf.portfolio_performance("1D")
    s = out["summary"]
    assert s["spy_available"] is True
    assert s["spy_return_pct"] == 1.0
    # portfolio +2%, spy +1% -> alpha +1%.
    assert s["portfolio_return_pct"] == 2.0
    assert s["alpha_pct"] == 1.0


def test_1d_null_spy_when_no_quote():
    """Regression on the old behaviour: absent a same-day quote, spy stays null
    but the portfolio day change is still correct."""
    _FakeTradingClient.hist = _FakeHist(
        timestamp=[_ts("2026-07-09", "13:30"), _ts("2026-07-09", "15:00")],
        equity=[10050.0, 10100.0],
    )
    _FakeTradingClient.account = _FakeAccount(last_equity=10000.0)
    _FakePriceProvider.df = _spy_df({"2026-07-08": 500.0})
    _FakePriceProvider.latest = None

    out = perf.portfolio_performance("1D")
    assert out["summary"]["spy_return_pct"] is None
    assert out["summary"]["alpha_pct"] is None
    assert out["summary"]["spy_available"] is False


def test_1d_falls_back_to_first_bar_without_last_equity():
    """No last_equity -> base off the first intraday bar (old behaviour)."""
    _FakeTradingClient.hist = _FakeHist(
        timestamp=[_ts("2026-07-09", "13:30"), _ts("2026-07-09", "15:00")],
        equity=[10000.0, 10100.0],
    )
    _FakeTradingClient.account = _FakeAccount(last_equity=None)
    _FakePriceProvider.df = _spy_df({})
    _FakePriceProvider.latest = None

    out = perf.portfolio_performance("1D")
    assert out["summary"]["start_equity"] == 10000.0
    assert out["series"][0]["portfolio_pct"] == 0.0
    assert out["summary"]["portfolio_return_pct"] == 1.0


# --------------------------------------------------------------------------
# daily ranges — endpoint alignment
# --------------------------------------------------------------------------
def test_daily_drops_dangling_newer_portfolio_bar():
    """A portfolio bar newer than the last SPY close is dropped so the summary
    endpoints compare the same last common complete trading day."""
    # Portfolio has an extra 07-09 bar; SPY only has closes through 07-08.
    _FakeTradingClient.hist = _FakeHist(
        timestamp=[_ts("2026-07-07"), _ts("2026-07-08"), _ts("2026-07-09")],
        equity=[10000.0, 10200.0, 10500.0],
    )
    _FakeTradingClient.account = _FakeAccount(last_equity=9900.0)
    _FakePriceProvider.df = _spy_df({"2026-07-07": 500.0, "2026-07-08": 510.0})

    out = perf.portfolio_performance("1M")
    assert not out["intraday"]
    # dangling 07-09 bar dropped -> series ends on 07-08.
    assert out["series"][-1]["date"] == "2026-07-08"
    assert out["summary"]["end_equity"] == 10200.0
    # endpoint alpha computed on matching days: port +2% vs spy +2% = 0.
    assert out["summary"]["portfolio_return_pct"] == 2.0
    assert out["summary"]["spy_return_pct"] == 2.0
    assert out["summary"]["alpha_pct"] == 0.0


def test_daily_keeps_series_when_alignment_would_empty_it():
    """If dropping newer bars would leave <2 points, keep the raw series."""
    _FakeTradingClient.hist = _FakeHist(
        timestamp=[_ts("2026-07-08"), _ts("2026-07-09")],
        equity=[10000.0, 10300.0],
    )
    _FakeTradingClient.account = _FakeAccount(last_equity=9900.0)
    # SPY only has 07-07 -> aligning to <=07-07 leaves 0 portfolio bars.
    _FakePriceProvider.df = _spy_df({"2026-07-07": 500.0})

    out = perf.portfolio_performance("1M")
    assert len(out["series"]) == 2
    assert out["series"][-1]["date"] == "2026-07-09"


def test_daily_all_aligned_no_drop():
    """When endpoints already match, nothing is dropped."""
    _FakeTradingClient.hist = _FakeHist(
        timestamp=[_ts("2026-07-07"), _ts("2026-07-08")],
        equity=[10000.0, 10100.0],
    )
    _FakeTradingClient.account = _FakeAccount(last_equity=9900.0)
    _FakePriceProvider.df = _spy_df({"2026-07-07": 500.0, "2026-07-08": 505.0})

    out = perf.portfolio_performance("1M")
    assert len(out["series"]) == 2
    assert out["series"][-1]["date"] == "2026-07-08"
    assert out["summary"]["spy_return_pct"] == 1.0


# --------------------------------------------------------------------------
# graceful failure paths preserved
# --------------------------------------------------------------------------
def test_broker_unavailable():
    def _boom(*a, **k):
        raise RuntimeError("no broker")
    import alpaca.trading.client as tc_mod
    with patch.object(tc_mod, "TradingClient", _boom):
        out = perf.portfolio_performance("1M")
    assert out["available"] is False and out["reason"] == "broker unavailable"


def test_insufficient_history():
    _FakeTradingClient.hist = _FakeHist(
        timestamp=[_ts("2026-07-08")], equity=[10000.0]
    )
    _FakeTradingClient.account = _FakeAccount(last_equity=9900.0)
    _FakePriceProvider.df = _spy_df({"2026-07-08": 500.0})
    out = perf.portfolio_performance("1M")
    assert out["available"] is False and out["reason"] == "insufficient history"


def test_unknown_period_normalizes_to_1m():
    _FakeTradingClient.hist = _FakeHist(
        timestamp=[_ts("2026-07-07"), _ts("2026-07-08")],
        equity=[10000.0, 10100.0],
    )
    _FakeTradingClient.account = _FakeAccount(last_equity=9900.0)
    _FakePriceProvider.df = _spy_df({"2026-07-07": 500.0, "2026-07-08": 505.0})
    out = perf.portfolio_performance("nonsense")
    assert out["period"] == "1M"
