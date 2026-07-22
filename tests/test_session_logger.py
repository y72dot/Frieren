"""Tests for LlmSessionLogger – per-session LLM conversation logging."""

from __future__ import annotations

import contextlib
from pathlib import Path

from loguru import logger

from src.core.llm.session_logger import LlmSessionLogger, _fmt_args, _short_id

# Unique key counter to avoid sink collisions across tests
_counter = 0


def _unique_key(prefix: str = "group") -> str:
    global _counter
    _counter += 1
    return f"{prefix}:test_{_counter}"


def _cleanup_sinks() -> None:
    """Remove all LlmSessionLogger sinks and reset the class-level _sinks dict."""
    for key in list(LlmSessionLogger._sinks):
        sid = LlmSessionLogger._sinks.pop(key)
        with contextlib.suppress(ValueError):
            logger.remove(sid)


class TestHelpers:
    def test_short_id_full(self):
        assert _short_id("call_00_1qWYoDtiEJ1YDc0j3SMp7759") == "call_00"

    def test_short_id_bare(self):
        assert _short_id("call_00") == "call_00"

    def test_short_id_single_segment(self):
        assert _short_id("simple") == "simple"

    def test_fmt_args_simple(self):
        result = _fmt_args({"a": 1, "b": "x"})
        assert "a=1" in result
        assert "b='x'" in result

    def test_fmt_args_empty(self):
        assert _fmt_args({}) == "()"

    def test_fmt_args_nested(self):
        result = _fmt_args({"nested": [1, 2, 3]})
        assert result.startswith("({")
        assert '"nested"' in result


class TestSessionLifecycle:
    def teardown_method(self):
        _cleanup_sinks()

    def test_trigger_writes_session_start(self, monkeypatch, tmp_path: Path):
        monkeypatch.chdir(tmp_path)
        key = _unique_key()
        s = LlmSessionLogger(key)
        s.trigger("Alice", "hello", is_group=True)
        logger.complete()

        content = s._file_path.read_text(encoding="utf-8")
        assert "SESSION START" in content
        assert "群聊" in content
        assert "Alice" in content
        assert "hello" in content

    def test_trigger_private_writes_correct_label(self, monkeypatch, tmp_path: Path):
        monkeypatch.chdir(tmp_path)
        key = _unique_key()
        s = LlmSessionLogger(key)
        s.trigger("Bob", "hi", is_group=False)
        logger.complete()

        content = s._file_path.read_text(encoding="utf-8")
        assert "私聊" in content
        assert "Bob" in content

    def test_session_reuse_logs_info(self, monkeypatch, tmp_path: Path):
        monkeypatch.chdir(tmp_path)
        key = _unique_key()
        s = LlmSessionLogger(key)
        s.session_reuse(5, 30.5)
        logger.complete()

        content = s._file_path.read_text(encoding="utf-8")
        assert "[REUSE]" in content
        assert "5 messages" in content
        assert "age=30s" in content

    def test_session_new_logs_info(self, monkeypatch, tmp_path: Path):
        monkeypatch.chdir(tmp_path)
        key = _unique_key()
        s = LlmSessionLogger(key)
        s.session_new(10)
        logger.complete()

        content = s._file_path.read_text(encoding="utf-8")
        assert "[NEW]" in content
        assert "10 messages" in content

    def test_session_end_logs_summary(self, monkeypatch, tmp_path: Path):
        monkeypatch.chdir(tmp_path)
        key = _unique_key()
        s = LlmSessionLogger(key)
        s.session_end(3)
        logger.complete()

        content = s._file_path.read_text(encoding="utf-8")
        assert "SESSION END" in content
        assert "3 turns" in content


class TestTurnBoundaries:
    def teardown_method(self):
        _cleanup_sinks()

    def test_turn_start(self, monkeypatch, tmp_path: Path):
        monkeypatch.chdir(tmp_path)
        key = _unique_key()
        s = LlmSessionLogger(key)
        s.turn_start(2, 5)
        logger.complete()

        content = s._file_path.read_text(encoding="utf-8")
        assert "Turn 2/5" in content


class TestRequestLogging:
    def teardown_method(self):
        _cleanup_sinks()

    def test_request_first_turn_prints_all(self, monkeypatch, tmp_path: Path):
        monkeypatch.chdir(tmp_path)
        key = _unique_key()
        s = LlmSessionLogger(key)
        s.turn_start(1, 5)
        msgs = [
            {"role": "system", "content": "You are a helpful bot."},
            {"role": "user", "content": "Hello!"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        s.request(msgs, "test-model", 0)
        logger.complete()

        content = s._file_path.read_text(encoding="utf-8")
        assert "REQ model=test-model msgs=3 tools=0" in content
        assert "You are a helpful bot" in content
        assert "Hello!" in content
        assert "Hi there!" in content
        assert "前" not in content

    def test_tool_view_records_names_and_packs(self, monkeypatch, tmp_path: Path):
        monkeypatch.chdir(tmp_path)
        key = _unique_key()
        session = LlmSessionLogger(key)

        session.tool_view(("query_history", "get_group_info"), ("core", "group_core"))
        logger.complete()

        content = session._file_path.read_text(encoding="utf-8")
        assert "TOOLS count=2 packs=core,group_core" in content
        assert "names=query_history,get_group_info" in content

    def test_request_second_turn_skips_old(self, monkeypatch, tmp_path: Path):
        monkeypatch.chdir(tmp_path)
        key = _unique_key()
        s = LlmSessionLogger(key)
        s.turn_start(1, 5)
        msgs_t1 = [
            {"role": "system", "content": "You are a bot."},
            {"role": "user", "content": "Q1"},
            {"role": "assistant", "content": "A1"},
        ]
        s.request(msgs_t1, "m", 0)

        s.turn_start(2, 5)
        msgs_t2 = [
            {"role": "system", "content": "You are a bot."},
            {"role": "user", "content": "Q1"},
            {"role": "assistant", "content": "A1"},
            {"role": "user", "content": "Q2"},
        ]
        s.request(msgs_t2, "m", 0)
        logger.complete()

        content = s._file_path.read_text(encoding="utf-8")
        assert "前 3 条消息同上轮" in content
        assert "Q2" in content

    def test_request_tool_calls_in_message(self, monkeypatch, tmp_path: Path):
        monkeypatch.chdir(tmp_path)
        key = _unique_key()
        s = LlmSessionLogger(key)
        s.turn_start(1, 5)
        msgs = [
            {"role": "user", "content": "do it"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_00_abc",
                        "function": {"name": "get_time", "arguments": "{}"},
                    }
                ],
            },
        ]
        s.request(msgs, "m", 0)
        logger.complete()

        content = s._file_path.read_text(encoding="utf-8")
        assert "ASSIST(tool)" in content
        assert "tool_calls=1" in content
        assert "get_time" in content

    def test_request_tool_role(self, monkeypatch, tmp_path: Path):
        monkeypatch.chdir(tmp_path)
        key = _unique_key()
        s = LlmSessionLogger(key)
        s.turn_start(1, 5)
        msgs = [{"role": "tool", "content": "result"}]
        s.request(msgs, "m", 0)
        logger.complete()

        content = s._file_path.read_text(encoding="utf-8")
        assert "TOOL" in content
        assert "result" in content

    def test_request_unknown_role(self, monkeypatch, tmp_path: Path):
        monkeypatch.chdir(tmp_path)
        key = _unique_key()
        s = LlmSessionLogger(key)
        s.turn_start(1, 5)
        msgs = [{"role": "custom", "content": "data"}]
        s.request(msgs, "m", 0)
        logger.complete()

        content = s._file_path.read_text(encoding="utf-8")
        assert "custom" in content
        assert "data" in content

    def test_request_empty_content(self, monkeypatch, tmp_path: Path):
        monkeypatch.chdir(tmp_path)
        key = _unique_key()
        s = LlmSessionLogger(key)
        s.turn_start(1, 5)
        msgs = [{"role": "user", "content": ""}]
        s.request(msgs, "m", 0)
        logger.complete()

        content = s._file_path.read_text(encoding="utf-8")
        assert "<empty>" in content


class TestResponseLogging:
    def teardown_method(self):
        _cleanup_sinks()

    def test_text_response_non_empty(self, monkeypatch, tmp_path: Path):
        monkeypatch.chdir(tmp_path)
        key = _unique_key()
        s = LlmSessionLogger(key)
        s.text_response("Hello, world!")
        logger.complete()

        content = s._file_path.read_text(encoding="utf-8")
        assert "RES text=13chars" in content
        assert "Hello, world!" in content

    def test_text_response_empty(self, monkeypatch, tmp_path: Path):
        monkeypatch.chdir(tmp_path)
        key = _unique_key()
        s = LlmSessionLogger(key)
        s.text_response("   ")
        logger.complete()

        content = s._file_path.read_text(encoding="utf-8")
        assert "text=(empty)" in content

    def test_tool_calls_result_dict(self, monkeypatch, tmp_path: Path):
        monkeypatch.chdir(tmp_path)
        key = _unique_key()
        s = LlmSessionLogger(key)
        tcs = [{"id": "call_00_xyz", "name": "get_time", "arguments": {}}]
        s.tool_calls_result(tcs)
        logger.complete()

        content = s._file_path.read_text(encoding="utf-8")
        assert "RES tool_calls=1" in content
        assert "call_00" in content
        assert "get_time" in content

    def test_tool_calls_result_object(self, monkeypatch, tmp_path: Path):
        from src.core.llm import ToolCall

        monkeypatch.chdir(tmp_path)
        key = _unique_key()
        s = LlmSessionLogger(key)
        tcs = [ToolCall(id="call_01_abc", name="send_msg", arguments={"text": "hi"})]
        s.tool_calls_result(tcs)
        logger.complete()

        content = s._file_path.read_text(encoding="utf-8")
        assert "call_01" in content
        assert "send_msg" in content
        assert "text='hi'" in content

    def test_tool_calls_result_json_string_args(self, monkeypatch, tmp_path: Path):
        monkeypatch.chdir(tmp_path)
        key = _unique_key()
        s = LlmSessionLogger(key)
        tcs = [
            {
                "id": "call_02",
                "name": "search",
                "arguments": '{"query": "test"}',
            }
        ]
        s.tool_calls_result(tcs)
        logger.complete()

        content = s._file_path.read_text(encoding="utf-8")
        assert "call_02" in content
        assert "query='test'" in content

    def test_tool_result(self, monkeypatch, tmp_path: Path):
        monkeypatch.chdir(tmp_path)
        key = _unique_key()
        s = LlmSessionLogger(key)
        s.tool_result("call_00_xyz", "get_time", "2024-01-01")
        logger.complete()

        content = s._file_path.read_text(encoding="utf-8")
        assert "TRES" in content
        assert "call_00" in content
        assert "get_time" in content
        assert "2024-01-01" in content

    def test_final_text(self, monkeypatch, tmp_path: Path):
        monkeypatch.chdir(tmp_path)
        key = _unique_key()
        s = LlmSessionLogger(key)
        s.final_text("Done!")
        logger.complete()

        content = s._file_path.read_text(encoding="utf-8")
        assert "SEND" in content
        assert "Done!" in content


class TestErrorPaths:
    def teardown_method(self):
        _cleanup_sinks()

    def test_max_turns_forced(self, monkeypatch, tmp_path: Path):
        monkeypatch.chdir(tmp_path)
        key = _unique_key()
        s = LlmSessionLogger(key)
        s.max_turns_forced()
        logger.complete()

        content = s._file_path.read_text(encoding="utf-8")
        assert "MAX_TURNS" in content


class TestSinkManagement:
    def teardown_method(self):
        _cleanup_sinks()

    def test_sink_reused_for_same_key(self, monkeypatch, tmp_path: Path):
        monkeypatch.chdir(tmp_path)
        key = _unique_key("group")
        initial_count = len(LlmSessionLogger._sinks)

        LlmSessionLogger(key)
        assert key in LlmSessionLogger._sinks

        LlmSessionLogger(key)
        assert len(LlmSessionLogger._sinks) == initial_count + 1

    def test_session_key_sanitization(self, monkeypatch, tmp_path: Path):
        monkeypatch.chdir(tmp_path)
        key = _unique_key("group")
        s = LlmSessionLogger(key)
        assert ":" not in s._file_name
        assert s._file_name.endswith(".log")

    def test_session_key_with_slashes_sanitized(self, monkeypatch, tmp_path: Path):
        monkeypatch.chdir(tmp_path)
        key = "a_b_c_d"
        s = LlmSessionLogger(key)
        assert "/" not in s._file_name
        assert "\\" not in s._file_name
