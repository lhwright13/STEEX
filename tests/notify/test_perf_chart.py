"""EOD performance chart: pure PNG rendering + Telegram photo plumbing."""
from types import SimpleNamespace
from unittest.mock import patch

from src.notify.perf_chart import render_perf_chart
from src.notify.messaging import send_user_photo, DryRunChannel


def _perf_1m(n=22):
    series = [{"date": f"2026-06-{i+1:02d}", "equity": 50000 + i * 120,
               "portfolio_pct": round(i * 0.24, 2), "spy_pct": round(i * 0.15, 2),
               "alpha_pct": round(i * 0.09, 2)} for i in range(n)]
    return {"available": True, "period": "1M", "series": series,
            "summary": {"portfolio_return_pct": 5.04, "spy_return_pct": 3.15,
                        "alpha_pct": 1.89, "start_equity": 50000,
                        "end_equity": 52520, "spy_available": True}}


def _perf_1d(n=30):
    series = [{"date": f"{9 + i // 12:02d}:{(i * 5) % 60:02d}",
               "equity": 52000 + i * 8, "portfolio_pct": round(i * 0.015, 2),
               "spy_pct": None, "alpha_pct": None} for i in range(n)]
    return {"available": True, "period": "1D", "intraday": True, "series": series,
            "summary": {"portfolio_return_pct": 0.45, "spy_return_pct": 0.2,
                        "alpha_pct": 0.25, "start_equity": 52000,
                        "end_equity": 52232, "spy_available": True}}


def test_renders_valid_png_with_both_panels():
    png = render_perf_chart(_perf_1m(), _perf_1d())
    assert png is not None and png[:8] == b"\x89PNG\r\n\x1a\n"  # PNG magic
    assert len(png) > 10_000  # a real two-panel figure, not a stub


def test_renders_with_only_one_panel():
    assert render_perf_chart(_perf_1m(), None)[:4] == b"\x89PNG"
    assert render_perf_chart(None, _perf_1d())[:4] == b"\x89PNG"


def test_returns_none_when_nothing_plottable():
    assert render_perf_chart(None, None) is None
    assert render_perf_chart({"series": []}, {"series": [{"date": "x"}]}) is None


def test_handles_missing_spy_values():
    perf = _perf_1m()
    for p in perf["series"]:
        p["spy_pct"] = None
    perf["summary"]["spy_return_pct"] = None
    assert render_perf_chart(perf, None)[:4] == b"\x89PNG"


def test_send_user_photo_dry_run_when_messaging_disabled():
    s = SimpleNamespace(messaging_enabled=False)
    res = send_user_photo(b"\x89PNG-fake", caption="test", settings=s)
    assert res["dry_run"] is True and res["sent"] is True  # DryRunChannel


def test_send_user_photo_posts_multipart_when_enabled():
    s = SimpleNamespace(messaging_enabled=True, telegram_bot_token="tok",
                        telegram_chat_id="42")
    with patch("requests.post") as post:
        post.return_value.status_code = 200
        res = send_user_photo(b"\x89PNG-fake", caption="cap", settings=s)
    assert res["sent"] is True and res["dry_run"] is False
    url = post.call_args[0][0]
    assert "sendPhoto" in url and "tok" in url
    assert post.call_args.kwargs["files"]["photo"][2] == "image/png"


def test_photo_failure_never_raises():
    s = SimpleNamespace(messaging_enabled=True, telegram_bot_token="tok",
                        telegram_chat_id="42")
    with patch("requests.post", side_effect=ConnectionError("down")):
        res = send_user_photo(b"x", settings=s)
    assert res["sent"] is False
