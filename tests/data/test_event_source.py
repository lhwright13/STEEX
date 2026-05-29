"""Tests for the event-trigger ingestion layer."""

import json

from src.data.event_source import (
    NewsEvent,
    TruthSocialEventSource,
    StreamingEventSource,
    _strip_html,
)


def _post(pid, content, created="2999-01-01T00:00:00.000Z"):
    # Far-future created_at so the lookback window never excludes the fixture.
    return {"id": pid, "content": content, "created_at": created,
            "url": f"https://truthsocial.com/@x/{pid}"}


def test_strip_html():
    assert _strip_html("<p>Go buy a <b>Dell</b>!</p>") == "Go buy a Dell !"
    assert _strip_html("a &amp; b &quot;c&quot;") == 'a & b "c"'


def test_truth_source_parses_and_strips(tmp_path):
    posts = [_post("1", "<p>Hello <b>world</b></p>")]
    src = TruthSocialEventSource("123", str(tmp_path), http_get=lambda url: posts)
    events = src.poll()
    assert len(events) == 1
    assert events[0].ticker == ""  # unresolved
    assert events[0].source == "truth_social"
    assert "Hello world" in events[0].headline


def test_truth_source_dedupes_across_polls(tmp_path):
    posts = [_post("1", "<p>a</p>"), _post("2", "<p>b</p>")]
    src = TruthSocialEventSource("123", str(tmp_path), http_get=lambda url: posts)
    assert len(src.poll()) == 2
    # Same posts again -> all seen -> nothing fresh.
    assert len(src.poll()) == 0


def test_truth_source_skips_empty_posts(tmp_path):
    posts = [_post("1", "<p></p>"), _post("2", "<p>real</p>")]
    src = TruthSocialEventSource("123", str(tmp_path), http_get=lambda url: posts)
    events = src.poll()
    assert [e.id for e in events] == ["2"]


def test_truth_source_respects_lookback(tmp_path):
    old = _post("old", "<p>stale</p>", created="2000-01-01T00:00:00.000Z")
    src = TruthSocialEventSource("123", str(tmp_path), lookback_hours=24,
                                 http_get=lambda url: [old])
    assert src.poll() == []


def test_truth_source_handles_fetch_failure(tmp_path):
    def boom(url):
        raise RuntimeError("network down")
    src = TruthSocialEventSource("123", str(tmp_path), http_get=boom)
    assert src.poll() == []  # never raises


def test_streaming_source_drains_buffer():
    s = StreamingEventSource()
    s.push(NewsEvent(id="1", ticker="", headline="h", url="u", published_at="t", source="x"))
    assert len(s.poll()) == 1
    assert s.poll() == []  # drained
