"""Tests for LLM provider types and OpenAI-compatible implementation."""

from __future__ import annotations

import pytest

from src.core.llm.provider import (
    LlmResponse,
    OpenAICompatibleProvider,
    ToolCall,
)


class TestToolCall:
    def test_create(self):
        tc = ToolCall(id="call_1", name="set_essence", arguments={"message_id": 123})
        assert tc.id == "call_1"
        assert tc.name == "set_essence"
        assert tc.arguments == {"message_id": 123}


class TestLlmResponse:
    def test_text_response(self):
        resp = LlmResponse(text="hello")
        assert resp.text == "hello"
        assert resp.tool_calls is None

    def test_tool_calls_response(self):
        tc = ToolCall(id="call_1", name="set_essence", arguments={"message_id": 123})
        resp = LlmResponse(tool_calls=[tc])
        assert resp.text is None
        assert resp.tool_calls == [tc]

    def test_empty_response(self):
        resp = LlmResponse()
        assert resp.text is None
        assert resp.tool_calls is None


class TestOpenAICompatibleProvider:
    """Tests using httpx to mock the API (via respx or monkeypatch).

    These tests verify response parsing logic, not actual HTTP calls.
    """

    @pytest.fixture
    def provider(self):
        return OpenAICompatibleProvider(
            api_base="https://api.example.com/v1",
            api_key="sk-test",
        )

    @pytest.mark.asyncio
    async def test_text_response(self, provider, monkeypatch):
        """Provider parses a plain text response correctly."""

        class FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {
                    "choices": [
                        {"message": {"role": "assistant", "content": "你好！"}}
                    ]
                }

        class FakeClient:
            def __init__(self):
                self.sent = None

            async def post(self, url, json=None):
                self.sent = json
                return FakeResponse()

        fake = FakeClient()
        monkeypatch.setattr(provider, "_client", fake)

        resp = await provider.chat_completion(
            [{"role": "user", "content": "hi"}],
            model="gpt-4o-mini",
            max_tokens=100,
        )

        assert resp.text == "你好！"
        assert resp.tool_calls is None
        assert fake.sent["model"] == "gpt-4o-mini"

    @pytest.mark.asyncio
    async def test_tool_calls_response(self, provider, monkeypatch):
        """Provider parses tool_calls correctly."""

        class FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "call_abc",
                                        "type": "function",
                                        "function": {
                                            "name": "set_essence",
                                            "arguments": '{"message_id": 42}',
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                }

        class FakeClient:
            async def post(self, url, json=None):
                return FakeResponse()

        monkeypatch.setattr(provider, "_client", FakeClient())

        resp = await provider.chat_completion(
            [{"role": "user", "content": "设精"}],
            tools=[{"type": "function", "function": {"name": "set_essence"}}],
            model="gpt-4o-mini",
        )

        assert resp.text is None
        assert resp.tool_calls is not None
        assert len(resp.tool_calls) == 1
        assert resp.tool_calls[0].id == "call_abc"
        assert resp.tool_calls[0].name == "set_essence"
        assert resp.tool_calls[0].arguments == {"message_id": 42}

    @pytest.mark.asyncio
    async def test_tools_passed_in_body(self, provider, monkeypatch):
        """Tools are included in the request body when provided."""

        class FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {"choices": [{"message": {"content": "ok"}}]}

        class FakeClient:
            def __init__(self):
                self.sent = None

            async def post(self, url, json=None):
                self.sent = json
                return FakeResponse()

        fake = FakeClient()
        monkeypatch.setattr(provider, "_client", fake)

        tools = [{"type": "function", "function": {"name": "mute_user"}}]
        await provider.chat_completion(
            [{"role": "user", "content": "禁言"}],
            tools=tools,
            model="x",
        )

        assert fake.sent["tools"] == tools
        assert "tools" in fake.sent

    @pytest.mark.asyncio
    async def test_no_tools_when_none(self, provider, monkeypatch):
        """When tools=None, the body should not include 'tools' key."""

        class FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {"choices": [{"message": {"content": "ok"}}]}

        class FakeClient:
            def __init__(self):
                self.sent = None

            async def post(self, url, json=None):
                self.sent = json
                return FakeResponse()

        fake = FakeClient()
        monkeypatch.setattr(provider, "_client", fake)

        await provider.chat_completion(
            [{"role": "user", "content": "hi"}],
            model="x",
        )

        assert "tools" not in fake.sent

    @pytest.mark.asyncio
    async def test_empty_content(self, provider, monkeypatch):
        """Empty content string is handled."""

        class FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {"choices": [{"message": {"content": ""}}]}

        class FakeClient:
            async def post(self, url, json=None):
                return FakeResponse()

        monkeypatch.setattr(provider, "_client", FakeClient())

        resp = await provider.chat_completion(
            [{"role": "user", "content": "hi"}],
            model="x",
        )
        assert resp.text == ""

    @pytest.mark.asyncio
    async def test_http_error_fallback(self, provider, monkeypatch):
        """HTTP errors return a fallback text response instead of raising."""

        class FakeClient:
            async def post(self, url, json=None):
                import httpx

                raise httpx.HTTPError("Connection refused")

        monkeypatch.setattr(provider, "_client", FakeClient())

        resp = await provider.chat_completion(
            [{"role": "user", "content": "hi"}],
            model="x",
        )
        assert resp.text is not None
        assert "失败" in resp.text
        assert resp.tool_calls is None
