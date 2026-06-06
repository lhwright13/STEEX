"""P0-3: the user_updates stream — model + append-only store + reader contract.

Covers the termination criteria: write->read round-trip, day partitioning,
type/recency filters, get-by-id, and tolerant reads of malformed/forward lines.
No network.
"""
import json

import pytest

from src.notify import user_updates as uu


def test_write_read_roundtrip(tmp_path):
    rec = uu.write_update(tmp_path, type="buy", title="Bought AAPL",
                          summary="why", severity="success",
                          payload={"ticker": "AAPL", "shares": 10},
                          links=[{"label": "run", "href": "/runs/x"}])
    assert rec.id and rec.ts.endswith("Z") and rec.type == "buy"
    back = uu.read_updates(tmp_path)
    assert len(back) == 1
    got = back[0]
    assert got.id == rec.id
    assert got.payload["ticker"] == "AAPL"
    assert got.links[0].href == "/runs/x"


def test_get_by_id(tmp_path):
    a = uu.write_update(tmp_path, type="system", title="a")
    uu.write_update(tmp_path, type="system", title="b")
    found = uu.get_update(tmp_path, a.id)
    assert found is not None and found.title == "a"
    assert uu.get_update(tmp_path, "nonexistent") is None


def test_newest_first_ordering(tmp_path):
    uu.write_update(tmp_path, type="system", title="first")
    uu.write_update(tmp_path, type="system", title="second")
    uu.write_update(tmp_path, type="system", title="third")
    titles = [r.title for r in uu.read_updates(tmp_path)]
    assert titles == ["third", "second", "first"]


def test_day_partitioning_and_day_filter(tmp_path):
    # Two updates on different days (ts drives the partition file).
    uu.write_update(tmp_path, type="buy", title="day1", ts="2026-06-01T10:00:00Z")
    uu.write_update(tmp_path, type="buy", title="day2", ts="2026-06-02T10:00:00Z")
    files = sorted(p.name for p in (tmp_path / "user_updates").glob("*.jsonl"))
    assert files == ["2026-06-01.jsonl", "2026-06-02.jsonl"]
    only_d1 = uu.read_updates(tmp_path, day="2026-06-01")
    assert [r.title for r in only_d1] == ["day1"]


def test_type_filter(tmp_path):
    uu.write_update(tmp_path, type="buy", title="b")
    uu.write_update(tmp_path, type="sell", title="s")
    uu.write_update(tmp_path, type="event_trade", title="e")
    only = uu.read_updates(tmp_path, types=["buy", "event_trade"])
    assert sorted(r.type for r in only) == ["buy", "event_trade"]


def test_since_filter_and_limit(tmp_path):
    uu.write_update(tmp_path, type="system", title="old", ts="2026-06-01T00:00:00Z")
    uu.write_update(tmp_path, type="system", title="new", ts="2026-06-03T00:00:00Z")
    recent = uu.read_updates(tmp_path, since="2026-06-02T00:00:00Z")
    assert [r.title for r in recent] == ["new"]
    assert len(uu.read_updates(tmp_path, limit=1)) == 1


def test_tolerant_of_malformed_and_forward_lines(tmp_path):
    uu.write_update(tmp_path, type="system", title="good")
    day_file = next((tmp_path / "user_updates").glob("*.jsonl"))
    with open(day_file, "a") as f:
        f.write("this is not json\n")                      # malformed -> skipped
        f.write(json.dumps({"id": "z", "ts": "2026-06-09T00:00:00Z",
                            "type": "system", "title": "fwd",
                            "unknown_future_field": 1}) + "\n")  # extra -> ignored
    recs = uu.read_updates(tmp_path)
    titles = {r.title for r in recs}
    assert "good" in titles and "fwd" in titles  # malformed line did not break the read


def test_rejects_bad_type_and_severity(tmp_path):
    with pytest.raises(ValueError):
        uu.write_update(tmp_path, type="not_a_type", title="x")
    with pytest.raises(ValueError):
        uu.write_update(tmp_path, type="system", title="x", severity="loud")


def test_dashboard_service_reads_the_stream(tmp_path, monkeypatch):
    """The service contract the UI (P3-3) and producers (P1-2) will share."""
    from frontend.services import DashboardService
    svc = DashboardService()
    monkeypatch.setattr(svc, "data_dir", tmp_path)
    rec = uu.write_update(tmp_path, type="event_trade", title="DELL fired",
                          payload={"ticker": "DELL"})
    out = svc.get_user_updates(limit=10)
    assert out["count"] == 1 and out["updates"][0]["title"] == "DELL fired"
    one = svc.get_user_update(rec.id)
    assert one and one["payload"]["ticker"] == "DELL"
    assert svc.get_user_update("missing") is None
