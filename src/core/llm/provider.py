"""LLM provider abstraction – OpenAI-compatible chat completion with function calling."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

import httpx
from loguru import logger


@dataclass
class ToolCall:
    """A single function-calling tool invocation from the LLM."""

    id: str
    name: str
    arguments: dict


@dataclass
class LlmResponse:
    """Result of a chat completion request.

    Either *text* (plain text reply) or *tool_calls* (function calls)
    will be set, never both.
    """

    text: str | None = None
    tool_calls: list[ToolCall] | None = None


class LlmProvider(Protocol):
    """Protocol for LLM backends."""

    async def chat_completion(
        self, messages: list[dict], *, tools: list[dict] | None = None, **kwargs: Any
    ) -> LlmResponse: ...


# ---------------------------------------------------------------------------
# OpenAI-compatible implementation
# ---------------------------------------------------------------------------


class OpenAICompatibleProvider:
    """OpenAI-compatible chat completion provider via httpx.

    Parameters
    ----------
    api_base:
        Base URL of the API (e.g. ``https://api.openai.com/v1``).
    api_key:
        API key for authentication.
    timeout:
        HTTP request timeout in seconds.
    """

    def __init__(
        self, api_base: str, api_key: str, *, timeout: float = 60.0
    ) -> None:
        self._base = api_base.rstrip("/")
        self._api_key = api_key
        self._log_path = Path("logs/llm.log")
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )

    def _write_log(self, content: str) -> None:
        """Append debug entry to logs/llm.log."""
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        with open(self._log_path, "a", encoding="utf-8") as f:
            f.write(f"\n{'='*60}\n[{ts}]\n{content}\n")

    async def chat_completion(
        self,
        messages: list[dict],
        *,
        tools: list[dict] | None = None,
        **kwargs: Any,
    ) -> LlmResponse:
        """Send a chat completion request and parse the response.

        Parameters
        ----------
        messages:
            Conversation history in OpenAI format.
        tools:
            Optional function-calling tool definitions.
        **kwargs:
            Forwarded as JSON body fields (model, max_tokens, temperature, …).
        """
        body: dict[str, Any] = {"messages": messages, **kwargs}
        if tools:
            body["tools"] = tools

        url = f"{self._base}/chat/completions"
        logger.debug(f"LLM request: model={kwargs.get('model')} msgs={len(messages)} tools={len(tools or [])}")

        # Debug: log full request to logs/llm.log
        req_log = json.dumps(body, ensure_ascii=False, indent=2)
        self._write_log(f">>> REQUEST\n{req_log}")

        try:
            resp = await self._client.post(url, json=body)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            logger.opt(exception=True).error(f"LLM API error: {exc}")
            self._write_log(f">>> HTTP ERROR\n{exc}")
            return LlmResponse(text="(LLM API请求失败，请稍后再试)")

        data: dict = resp.json()

        # Debug: log full response to logs/llm.log
        resp_log = json.dumps(data, ensure_ascii=False, indent=2)
        self._write_log(f"<<< RESPONSE\n{resp_log}")
        choice = data.get("choices", [{}])[0]
        msg: dict = choice.get("message", {})

        # Tool calls
        raw_tool_calls: list[dict] = msg.get("tool_calls", [])
        if raw_tool_calls:
            parsed: list[ToolCall] = []
            for tc in raw_tool_calls:
                fn = tc.get("function", {})
                try:
                    args = json.loads(fn.get("arguments", "{}"))
                except json.JSONDecodeError:
                    args = {}
                parsed.append(ToolCall(id=tc["id"], name=fn.get("name", ""), arguments=args))
            logger.debug(f"LLM returned {len(parsed)} tool call(s): {[tc.name for tc in parsed]}")
            return LlmResponse(tool_calls=parsed)

        # Plain text
        content: str = msg.get("content", "") or ""
        logger.debug(f"LLM response: {content[:100]}...")
        return LlmResponse(text=content)

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()
