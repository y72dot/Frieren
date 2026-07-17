"""Tests for MessageStore: record, query, dedup, trim, stats."""

from src.core.message_store import MessageStore, StoredMessage
from src.plugin.base import Event


# -------------------------------------------------------------------
# helpers
# -------------------------------------------------------------------


def _make_event(
    message_id: int,
    user_id: int = 123,
    group_id: int = 456,
    message: str = "hello",
    raw: dict | None = None,
    is_group: bool = True,
) -> Event:
    if raw is None:
        raw = {
            "message_id": message_id,
            "user_id": user_id,
            "group_id": group_id,
            "raw_message": message,
            "time": 1000 + message_id,
            "sender": {"nickname": f"user{user_id}", "card": ""},
        }
    return Event(
        type="message.group" if is_group else "message.private",
        user_id=user_id,
        message_id=message_id,
        message=message,
        group_id=group_id if is_group else None,
        is_group=is_group,
        raw=raw,
    )


# -------------------------------------------------------------------
# record / dedup
# -------------------------------------------------------------------


def test_record_and_recent():
    store = MessageStore(db_path=":memory:")
    store.record(_make_event(1, user_id=111, message="first"))
    store.record(_make_event(2, user_id=222, message="second"))

    msgs = store.recent(456, n=10)
    assert len(msgs) == 2
    assert msgs[0].content == "first"
    assert msgs[1].content == "second"
    assert msgs[0].user_id == 111
    assert msgs[1].user_id == 222


def test_record_dedup():
    """INSERT OR IGNORE should prevent duplicate message_id."""
    store = MessageStore(db_path=":memory:")
    store.record(_make_event(1, message="original"))
    store.record(_make_event(1, message="duplicate"))

    msgs = store.recent(456)
    assert len(msgs) == 1
    assert msgs[0].content == "original"


def test_record_skips_no_message_id():
    """Events without message_id should be silently skipped."""
    store = MessageStore(db_path=":memory:")
    event = Event(type="notice.notify", user_id=1, group_id=456, is_group=True)
    store.record(event)

    msgs = store.recent(456)
    assert len(msgs) == 0


# -------------------------------------------------------------------
# recent
# -------------------------------------------------------------------


def test_recent_respects_limit():
    store = MessageStore(db_path=":memory:")
    for i in range(10):
        store.record(_make_event(i, message=f"msg{i}"))

    msgs = store.recent(456, n=3)
    assert len(msgs) == 3
    assert msgs[0].content == "msg7"
    assert msgs[2].content == "msg9"


def test_recent_empty_group():
    store = MessageStore(db_path=":memory:")
    assert store.recent(999) == []


# -------------------------------------------------------------------
# recent_private
# -------------------------------------------------------------------


def test_recent_private():
    store = MessageStore(db_path=":memory:")
    store.record(_make_event(1, user_id=789, group_id=None, is_group=False, message="hi"))
    store.record(_make_event(2, user_id=789, group_id=None, is_group=False, message="there"))
    # Also record a group message – should not appear in private results
    store.record(_make_event(3, user_id=111, group_id=456, is_group=True, message="group"))

    msgs = store.recent_private(789)
    assert len(msgs) == 2
    assert all(m.group_id is None for m in msgs)
    assert msgs[0].content == "hi"
    assert msgs[1].content == "there"


def test_recent_private_empty():
    store = MessageStore(db_path=":memory:")
    assert store.recent_private(999) == []


# -------------------------------------------------------------------
# by_user
# -------------------------------------------------------------------


def test_by_user():
    store = MessageStore(db_path=":memory:")
    store.record(_make_event(1, user_id=111, message="a"))
    store.record(_make_event(2, user_id=222, message="b"))
    store.record(_make_event(3, user_id=111, message="c"))

    msgs = store.by_user(456, user_id=111)
    assert len(msgs) == 2
    assert msgs[0].content == "a"
    assert msgs[1].content == "c"
    assert all(m.user_id == 111 for m in msgs)


def test_by_user_none():
    store = MessageStore(db_path=":memory:")
    assert store.by_user(456, user_id=999) == []


# -------------------------------------------------------------------
# search
# -------------------------------------------------------------------


def test_search_keyword():
    store = MessageStore(db_path=":memory:")
    store.record(_make_event(1, message="今天天气不错"))
    store.record(_make_event(2, message="明天可能下雨"))
    store.record(_make_event(3, message="天气很好"))

    results = store.search(456, "天气")
    assert len(results) == 2
    assert results[0].content == "今天天气不错"
    assert results[1].content == "天气很好"


def test_search_no_match():
    store = MessageStore(db_path=":memory:")
    store.record(_make_event(1, message="hello"))
    assert store.search(456, "zzz") == []


# -------------------------------------------------------------------
# trim
# -------------------------------------------------------------------


def test_trim_removes_excess():
    store = MessageStore(db_path=":memory:", max_per_group=3)
    for i in range(5):
        store.record(_make_event(i, message=f"msg{i}"))

    deleted = store.trim(456)
    assert deleted == 2

    msgs = store.recent(456, n=10)
    assert len(msgs) == 3
    # Oldest 2 should be gone
    assert msgs[0].message_id == 2
    assert msgs[2].message_id == 4


def test_trim_no_excess():
    store = MessageStore(db_path=":memory:", max_per_group=10)
    store.record(_make_event(1))
    deleted = store.trim(456)
    assert deleted == 0
    assert len(store.recent(456)) == 1


# -------------------------------------------------------------------
# stats
# -------------------------------------------------------------------


def test_stats():
    store = MessageStore(db_path=":memory:")
    # Group 1: 3 messages
    for i in range(3):
        store.record(_make_event(i, group_id=1))
    # Group 2: 2 messages
    for i in range(3, 5):
        store.record(_make_event(i, group_id=2))

    s = store.stats()
    assert s["total_messages"] == 5
    assert s["group_count"] == 2


def test_stats_empty():
    store = MessageStore(db_path=":memory:")
    s = store.stats()
    assert s["total_messages"] == 0
    assert s["group_count"] == 0


# -------------------------------------------------------------------
# nickname extraction
# -------------------------------------------------------------------


def test_nickname_from_dict():
    store = MessageStore(db_path=":memory:")
    raw = {
        "message_id": 1,
        "user_id": 123,
        "group_id": 456,
        "raw_message": "hi",
        "time": 1000,
        "sender": {"nickname": "Alice", "card": "A酱"},
    }
    event = _make_event(1, user_id=123, raw=raw)
    store.record(event)

    msgs = store.recent(456)
    assert msgs[0].nickname == "A酱"  # card takes priority


def test_nickname_fallback_to_user_id():
    store = MessageStore(db_path=":memory:")
    event = Event(
        type="message.group",
        user_id=555,
        message_id=1,
        message="hi",
        group_id=456,
        is_group=True,
        raw=None,
    )
    store.record(event)

    msgs = store.recent(456)
    assert msgs[0].nickname == "555"


# -------------------------------------------------------------------
# exclude_user_id
# -------------------------------------------------------------------


def test_recent_exclude_user():
    store = MessageStore(db_path=":memory:")
    store.record(_make_event(1, user_id=111, message="from_111"))
    store.record(_make_event(2, user_id=222, message="from_222"))
    store.record(_make_event(3, user_id=333, message="from_333"))

    msgs = store.recent(456, n=10, exclude_user_id=222)
    assert len(msgs) == 2
    assert all(m.user_id != 222 for m in msgs)
    assert msgs[0].user_id == 111
    assert msgs[1].user_id == 333
