"""P1-4: multi-figure event sources.

Each watched figure polls independently (own cursor), every event is tagged with
its figure, the composite merges them, and a sub-source failure is non-fatal.
"""
import json

from src.data.event_source import TruthSocialEventSource, CompositeEventSource


def _post(pid, text, created="2099-01-01T00:00:00Z"):
    return {"id": pid, "content": text, "created_at": created,
            "url": f"https://t/{pid}"}


def test_event_tagged_with_figure_and_namespaced_cursor(tmp_path):
    src = TruthSocialEventSource(
        account_id="111", data_dir=str(tmp_path), figure="trump",
        http_get=lambda url: [_post("1", "buy a Dell")],
    )
    events = src.poll()
    assert len(events) == 1 and events[0].figure == "trump"
    # cursor is namespaced by account, not the shared legacy file
    assert (tmp_path / "events" / "truth_cursor_111.json").exists()
    assert not (tmp_path / "events" / "truth_cursor.json").exists()


def test_independent_cursors_per_figure(tmp_path):
    a = TruthSocialEventSource("111", str(tmp_path), figure="trump",
                               http_get=lambda url: [_post("p1", "buy a Dell")])
    b = TruthSocialEventSource("222", str(tmp_path), figure="musk",
                               http_get=lambda url: [_post("p1", "buy a Tesla")])
    # same post id across the two accounts must NOT cross-dedup
    assert len(a.poll()) == 1
    assert len(b.poll()) == 1
    # second poll of A dedups its own seen id
    assert len(a.poll()) == 0
    files = {p.name for p in (tmp_path / "events").glob("*.json")}
    assert files == {"truth_cursor_111.json", "truth_cursor_222.json"}


def test_composite_merges_and_tags(tmp_path):
    a = TruthSocialEventSource("111", str(tmp_path), figure="trump",
                               http_get=lambda url: [_post("a", "buy a Dell",
                                                            "2099-01-02T00:00:00Z")])
    b = TruthSocialEventSource("222", str(tmp_path), figure="musk",
                               http_get=lambda url: [_post("b", "buy a Tesla",
                                                            "2099-01-01T00:00:00Z")])
    merged = CompositeEventSource([a, b]).poll()
    assert [e.figure for e in merged] == ["musk", "trump"]  # oldest-first by published_at


def test_composite_survives_a_failing_source(tmp_path):
    good = TruthSocialEventSource("111", str(tmp_path), figure="trump",
                                  http_get=lambda url: [_post("a", "buy a Dell")])

    class Boom:
        def poll(self):
            raise RuntimeError("down")

    merged = CompositeEventSource([Boom(), good]).poll()
    assert len(merged) == 1 and merged[0].figure == "trump"


def test_legacy_cursor_migration(tmp_path):
    """The orchestrator renames the legacy shared cursor to the namespaced one."""
    events = tmp_path / "events"
    events.mkdir(parents=True)
    (events / "truth_cursor.json").write_text(json.dumps({"seen_ids": ["old1"]}))
    # simulate the migration the orchestrator performs for the legacy account
    legacy_acct = "107780257626128497"
    target = events / f"truth_cursor_{legacy_acct}.json"
    (events / "truth_cursor.json").rename(target)
    src = TruthSocialEventSource(legacy_acct, str(tmp_path),
                                 http_get=lambda url: [_post("old1", "x")])
    # the migrated seen id is honored -> the old post is deduped, not re-emitted
    assert src.poll() == []
