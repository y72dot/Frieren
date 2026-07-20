"""Tests for llm_memory – chat history formatting helpers."""

from __future__ import annotations

from src.core.message_store import StoredMessage


class TestCleanContent:
    """Tests for _clean_content – CQ codes are preserved as-is."""

    def test_reply_kept(self):
        from plugins.llm_memory import _clean_content

        assert _clean_content("[CQ:reply,id=1739164623]") == "[CQ:reply,id=1739164623]"

    def test_at_kept(self):
        from plugins.llm_memory import _clean_content

        assert _clean_content("[CQ:at,qq=3175476491]") == "[CQ:at,qq=3175476491]"

    def test_image_kept(self):
        from plugins.llm_memory import _clean_content

        assert _clean_content("[CQ:image,file=abc,url=xyz]") == "[CQ:image,file=abc,url=xyz]"

    def test_reply_and_at_combined(self):
        from plugins.llm_memory import _clean_content

        result = _clean_content("[CQ:reply,id=1739164623][CQ:at,qq=3175476491] hi")
        assert result == "[CQ:reply,id=1739164623][CQ:at,qq=3175476491] hi"

    def test_unknown_cq_kept(self):
        from plugins.llm_memory import _clean_content

        assert _clean_content("[CQ:face,id=123]hello") == "[CQ:face,id=123]hello"

    def test_plain_text_passthrough(self):
        from plugins.llm_memory import _clean_content

        assert _clean_content("hello world") == "hello world"

    def test_whitespace_trimmed(self):
        from plugins.llm_memory import _clean_content

        assert _clean_content("  hello  ") == "hello"


class TestFormatMsg:
    """Tests for _format_msg with QQ number display."""

    def test_shows_qq_number(self):
        from plugins.llm_memory import _format_msg

        m = StoredMessage(
            message_id=1, user_id=100, nickname="Alice",
            content="hello", time=1000, group_id=123,
        )
        assert _format_msg(m) == "[1] Alice(100): hello"

    def test_fallback_nickname(self):
        from plugins.llm_memory import _format_msg

        m = StoredMessage(
            message_id=2, user_id=200, nickname=None,
            content="hi", time=1001, group_id=123,
        )
        result = _format_msg(m)
        assert result.startswith("[2] 200(200): hi")

    def test_image_content_preserved(self):
        from plugins.llm_memory import _format_msg

        m = StoredMessage(
            message_id=3, user_id=300, nickname="Carol",
            content="[CQ:image,file=img.jpg,url=http://x]",
            time=1002, group_id=123,
        )
        assert _format_msg(m) == "[3] Carol(300): [CQ:image,file=img.jpg,url=http://x]"

    def test_reply_and_at_in_content(self):
        from plugins.llm_memory import _format_msg

        m = StoredMessage(
            message_id=4, user_id=400, nickname="Dave",
            content="[CQ:reply,id=1][CQ:at,qq=500] hello",
            time=1003, group_id=123,
        )
        assert _format_msg(m) == "[4] Dave(400): [CQ:reply,id=1][CQ:at,qq=500] hello"

    def test_self_tag(self):
        from plugins.llm_memory import _format_msg

        m = StoredMessage(
            message_id=5, user_id=3632757457, nickname="\u8299莉莲",
            content="你好", time=1004, group_id=123,
        )
        result = _format_msg(m, bot_qq=3632757457)
        assert result == "[5] \u8299莉莲(3632757457) [自己]: 你好"

    def test_self_tag_not_applied_for_other(self):
        from plugins.llm_memory import _format_msg

        m = StoredMessage(
            message_id=6, user_id=100, nickname="Alice",
            content="hi", time=1005, group_id=123,
        )
        result = _format_msg(m, bot_qq=3632757457)
        assert result == "[6] Alice(100): hi"
