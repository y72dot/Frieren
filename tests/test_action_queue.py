"""Tests for the action_queue plugin."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from plugins.action_queue import ActionQueueBusAdapter, reset_state
from src.core.config import (
    ActionQueueConfig,
    BotConfig,
    BotConfigSection,
    LoggingConfigSection,
    NapCatConfig,
    PluginConfig,
)
from src.core.message_bus import BusMessage, MessageBus, MessageType

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_bot_config(**kwargs: Any) -> BotConfig:
    aq = ActionQueueConfig(**kwargs)
    return BotConfig(
        bot=BotConfigSection(qq=123456, nickname=["test"], admin_users=[111]),
        napcat=NapCatConfig(ws_url="ws://127.0.0.1:3001"),
        plugin=PluginConfig(auto_discover=False),
        logging=LoggingConfigSection(level="DEBUG"),
        action_queue=aq,
        env={},
    )


def _setup_bus_with_handler(
    bot_config: BotConfig,
) -> tuple[MessageBus, ActionQueueBusAdapter, Any]:
    """Create a bus with ActionQueueBusAdapter (p=1).

    Returns (bus, adapter, bot).
    """
    reset_state()

    bus = MessageBus()

    adapter = ActionQueueBusAdapter(config=bot_config.action_queue)
    bus.subscribe(MessageType.ACTION, adapter, 1)

    from src.core.bot import Bot
    from src.core.message_store import MessageStore

    bot = Bot(config=bot_config)
    bot.msg_store = MessageStore(db_path=":memory:")
    return bus, adapter, bot


class _TimedApiClient:
    """ApiClient stub that records calls with monotonic timestamps."""

    def __init__(self, bus: MessageBus | None = None):
        self.calls: list[dict[str, Any]] = []
        self._bus = bus

    async def _raw_call(self, action: str, **params: Any) -> dict[str, Any]:
        self.calls.append({"method": action, "time": time.monotonic(), **params})
        return {"status": "ok", "action": action}

    async def send_group_msg(self, group_id: int, message: str) -> dict[str, Any]:
        if self._bus is not None:
            msg = BusMessage(
                type=MessageType.ACTION,
                payload={
                    "action": "send_group_msg",
                    "group_id": group_id,
                    "message": message,
                },
                source="test",
            )
            result = await self._bus.dispatch(msg, None)
            return result if isinstance(result, dict) else {}
        self.calls.append(
            {
                "method": "send_group_msg",
                "time": time.monotonic(),
                "group_id": group_id,
                "message": message,
            }
        )
        return {"status": "ok"}

    async def get_group_info(self, group_id: int) -> dict[str, Any]:
        if self._bus is not None:
            msg = BusMessage(
                type=MessageType.ACTION,
                payload={"action": "get_group_info", "group_id": group_id},
                source="test",
            )
            result = await self._bus.dispatch(msg, None)
            return result if isinstance(result, dict) else {}
        self.calls.append(
            {"method": "get_group_info", "time": time.monotonic(), "group_id": group_id}
        )
        return {"status": "ok", "group_id": group_id}

    async def send_private_msg(self, user_id: int, message: str) -> dict[str, Any]:
        if self._bus is not None:
            msg = BusMessage(
                type=MessageType.ACTION,
                payload={
                    "action": "send_private_msg",
                    "user_id": user_id,
                    "message": message,
                },
                source="test",
            )
            result = await self._bus.dispatch(msg, None)
            return result if isinstance(result, dict) else {}
        self.calls.append(
            {
                "method": "send_private_msg",
                "time": time.monotonic(),
                "user_id": user_id,
                "message": message,
            }
        )
        return {"status": "ok"}


# ---------------------------------------------------------------------------
# bypass tests
# ---------------------------------------------------------------------------


class TestBypassActions:
    async def test_bypass_action_passes_through(self):
        """get_group_info (in default bypass) passes through adapter to _raw_call."""
        cfg = _make_bot_config()
        bus, _adapter, bot = _setup_bus_with_handler(cfg)
        api = _TimedApiClient(bus=bus)
        bot.api = api  # type: ignore[assignment]

        msg = BusMessage(
            type=MessageType.ACTION,
            payload={"action": "get_group_info", "group_id": 123},
            source="test",
        )
        result = await bus.dispatch(msg, bot)

        assert isinstance(result, dict)
        assert result.get("action") == "get_group_info"
        assert len(api.calls) == 1
        assert api.calls[0]["method"] == "get_group_info"

    async def test_bypass_action_custom(self):
        """Custom bypass list is respected."""
        cfg = _make_bot_config(bypass_actions=["send_group_msg"])
        bus, _adapter, bot = _setup_bus_with_handler(cfg)
        api = _TimedApiClient(bus=bus)
        bot.api = api  # type: ignore[assignment]

        msg = BusMessage(
            type=MessageType.ACTION,
            payload={"action": "send_group_msg", "group_id": 1, "message": "x"},
            source="test",
        )
        result = await bus.dispatch(msg, bot)

        assert isinstance(result, dict)
        assert len(api.calls) == 1
        assert api.calls[0]["method"] == "send_group_msg"

    async def test_non_bypass_goes_through_rate_limit(self):
        """send_group_msg is NOT in default bypass; goes through rate limit."""
        cfg = _make_bot_config(global_rate=0, group_cooldown=0, per_action_delay=0)
        bus, _adapter, bot = _setup_bus_with_handler(cfg)
        api = _TimedApiClient(bus=bus)
        bot.api = api  # type: ignore[assignment]

        msg = BusMessage(
            type=MessageType.ACTION,
            payload={"action": "send_group_msg", "group_id": 1, "message": "x"},
            source="test",
        )
        result = await bus.dispatch(msg, bot)

        assert isinstance(result, dict)
        assert len(api.calls) == 1


# ---------------------------------------------------------------------------
# block tests
# ---------------------------------------------------------------------------


class TestBlockActions:
    async def test_blocked_action_dropped(self):
        """send_group_msg in block list is consumed by adapter."""
        cfg = _make_bot_config(block_actions=["send_group_msg"])
        bus, _adapter, bot = _setup_bus_with_handler(cfg)
        api = _TimedApiClient(bus=bus)
        bot.api = api  # type: ignore[assignment]

        msg = BusMessage(
            type=MessageType.ACTION,
            payload={"action": "send_group_msg", "group_id": 1, "message": "x"},
            source="test",
        )
        result = await bus.dispatch(msg, bot)

        assert result is True  # consumed by handler
        assert len(api.calls) == 0  # adapter consumed, no API call made

    async def test_non_blocked_action_still_executes(self):
        """Only actions in block list are dropped; others proceed."""
        cfg = _make_bot_config(
            block_actions=["send_group_msg"],
            global_rate=0,
            group_cooldown=0,
            per_action_delay=0,
        )
        bus, _adapter, bot = _setup_bus_with_handler(cfg)
        api = _TimedApiClient(bus=bus)
        bot.api = api  # type: ignore[assignment]

        msg = BusMessage(
            type=MessageType.ACTION,
            payload={"action": "send_private_msg", "user_id": 1, "message": "x"},
            source="test",
        )
        result = await bus.dispatch(msg, bot)

        assert isinstance(result, dict)
        assert len(api.calls) == 1
        assert api.calls[0]["method"] == "send_private_msg"


# ---------------------------------------------------------------------------
# rate limit tests
# ---------------------------------------------------------------------------


class TestGlobalRateLimit:
    async def test_actions_respect_global_rate(self):
        """Actions are spaced at least 1/global_rate apart."""
        cfg = _make_bot_config(global_rate=10, group_cooldown=0, per_action_delay=0)
        bus, _adapter, bot = _setup_bus_with_handler(cfg)
        api = _TimedApiClient(bus=bus)
        bot.api = api  # type: ignore[assignment]

        n = 3
        for i in range(n):
            msg = BusMessage(
                type=MessageType.ACTION,
                payload={
                    "action": "send_group_msg",
                    "group_id": i,
                    "message": f"msg{i}",
                },
                source="test",
            )
            await bus.dispatch(msg, bot)

        assert len(api.calls) == n
        min_interval = 1.0 / 10
        for i in range(1, n):
            gap = api.calls[i]["time"] - api.calls[i - 1]["time"]
            assert gap >= min_interval - 0.02, f"gap {gap:.3f} < {min_interval:.3f}"


class TestGroupCooldown:
    async def test_same_group_respects_cooldown(self):
        """Actions to the same group wait for group_cooldown."""
        cfg = _make_bot_config(
            global_rate=0, group_cooldown=0.2, per_action_delay=0, spam_window=0
        )
        bus, _adapter, bot = _setup_bus_with_handler(cfg)
        api = _TimedApiClient(bus=bus)
        bot.api = api  # type: ignore[assignment]

        for _ in range(3):
            msg = BusMessage(
                type=MessageType.ACTION,
                payload={
                    "action": "send_group_msg",
                    "group_id": 42,
                    "message": "x",
                },
                source="test",
            )
            await bus.dispatch(msg, bot)

        assert len(api.calls) == 3
        for i in range(1, 3):
            gap = api.calls[i]["time"] - api.calls[i - 1]["time"]
            assert gap >= 0.18, f"gap {gap:.3f} < 0.18"

    async def test_different_groups_no_cooldown(self):
        """Different groups are not affected by group cooldown."""
        cfg = _make_bot_config(global_rate=0, group_cooldown=1.0, per_action_delay=0)
        bus, _adapter, bot = _setup_bus_with_handler(cfg)
        api = _TimedApiClient(bus=bus)
        bot.api = api  # type: ignore[assignment]

        for gid in [1, 2, 3]:
            msg = BusMessage(
                type=MessageType.ACTION,
                payload={
                    "action": "send_group_msg",
                    "group_id": gid,
                    "message": "x",
                },
                source="test",
            )
            await bus.dispatch(msg, bot)

        assert len(api.calls) == 3
        total = api.calls[-1]["time"] - api.calls[0]["time"]
        assert total < 0.5, f"different groups took {total:.3f}s, expected < 0.5s"


class TestPerActionDelay:
    async def test_per_action_delay_applied(self):
        """Each action adds per_action_delay seconds."""
        delay = 0.05
        cfg = _make_bot_config(global_rate=0, group_cooldown=0, per_action_delay=delay)
        bus, _adapter, bot = _setup_bus_with_handler(cfg)
        api = _TimedApiClient(bus=bus)
        bot.api = api  # type: ignore[assignment]

        for i in range(3):
            msg = BusMessage(
                type=MessageType.ACTION,
                payload={
                    "action": "send_group_msg",
                    "group_id": i,
                    "message": "x",
                },
                source="test",
            )
            await bus.dispatch(msg, bot)

        assert len(api.calls) == 3
        for i in range(1, 3):
            gap = api.calls[i]["time"] - api.calls[i - 1]["time"]
            assert gap >= delay - 0.02, f"gap {gap:.3f} < {delay:.3f}"


# ---------------------------------------------------------------------------
# serialisation (semaphore) tests
# ---------------------------------------------------------------------------


class TestSemaphoreSerialisation:
    async def test_concurrent_actions_serialised(self):
        """Concurrent dispatch runs actions one at a time."""
        cfg = _make_bot_config(global_rate=0, group_cooldown=0, per_action_delay=0.05)
        bus, _adapter, bot = _setup_bus_with_handler(cfg)
        api = _TimedApiClient(bus=bus)
        bot.api = api  # type: ignore[assignment]

        async def send(i: int) -> None:
            msg = BusMessage(
                type=MessageType.ACTION,
                payload={
                    "action": "send_group_msg",
                    "group_id": i,
                    "message": f"msg{i}",
                },
                source="test",
            )
            await bus.dispatch(msg, bot)

        await asyncio.gather(send(1), send(2), send(3))

        assert len(api.calls) == 3
        for i in range(1, 3):
            gap = api.calls[i]["time"] - api.calls[i - 1]["time"]
            assert gap >= 0.03, f"Concurrent gap {gap:.3f} too small"


# ---------------------------------------------------------------------------
# enabled/disabled tests
# ---------------------------------------------------------------------------


class TestDisableSwitch:
    async def test_disabled_passes_all_actions(self):
        """When enabled=False, even block-listed actions pass through."""
        cfg = _make_bot_config(enabled=False, block_actions=["send_group_msg"])
        bus, _adapter, bot = _setup_bus_with_handler(cfg)
        api = _TimedApiClient(bus=bus)
        bot.api = api  # type: ignore[assignment]

        msg = BusMessage(
            type=MessageType.ACTION,
            payload={"action": "send_group_msg", "group_id": 1, "message": "x"},
            source="test",
        )
        result = await bus.dispatch(msg, bot)

        assert isinstance(result, dict)
        assert len(api.calls) == 1

    async def test_disabled_no_rate_limit(self):
        """When enabled=False, actions are not delayed."""
        cfg = _make_bot_config(enabled=False, global_rate=0.1, per_action_delay=1.0)
        bus, _adapter, bot = _setup_bus_with_handler(cfg)
        api = _TimedApiClient(bus=bus)
        bot.api = api  # type: ignore[assignment]

        t0 = time.monotonic()
        for i in range(3):
            msg = BusMessage(
                type=MessageType.ACTION,
                payload={
                    "action": "send_group_msg",
                    "group_id": i,
                    "message": "x",
                },
                source="test",
            )
            await bus.dispatch(msg, bot)
        elapsed = time.monotonic() - t0

        assert len(api.calls) == 3
        assert elapsed < 0.5  # no delay applied


# ---------------------------------------------------------------------------
# spam / dedup filter tests
# ---------------------------------------------------------------------------


class TestSpamFirstOccurrence:
    async def test_first_occurrence_passes(self):
        """First occurrence of an action is not blocked."""
        cfg = _make_bot_config(
            spam_window=5.0,
            spam_actions=["send_group_msg"],
            global_rate=0,
            group_cooldown=0,
        )
        bus, _adapter, bot = _setup_bus_with_handler(cfg)
        api = _TimedApiClient(bus=bus)
        bot.api = api  # type: ignore[assignment]

        msg = BusMessage(
            type=MessageType.ACTION,
            payload={"action": "send_group_msg", "group_id": 1, "message": "hello"},
            source="test",
        )
        result = await bus.dispatch(msg, bot)

        assert isinstance(result, dict)
        assert len(api.calls) == 1


class TestSpamDedup:
    async def test_duplicate_within_window_blocked(self):
        """Same action within spam_window is dropped."""
        cfg = _make_bot_config(
            spam_window=5.0,
            spam_actions=["send_group_msg"],
            global_rate=0,
            group_cooldown=0,
        )
        bus, _adapter, bot = _setup_bus_with_handler(cfg)
        api = _TimedApiClient(bus=bus)
        bot.api = api  # type: ignore[assignment]

        payload = {"action": "send_group_msg", "group_id": 1, "message": "hello"}

        r1 = await bus.dispatch(
            msg=BusMessage(type=MessageType.ACTION, payload=payload, source="test"),
            bot=bot,
        )
        r2 = await bus.dispatch(
            msg=BusMessage(type=MessageType.ACTION, payload=payload, source="test"),
            bot=bot,
        )

        assert isinstance(r1, dict)  # first passes
        assert r2 is True  # second dropped
        assert len(api.calls) == 1

    async def test_different_message_independent(self):
        """Different messages produce different dedup keys."""
        cfg = _make_bot_config(
            spam_window=5.0,
            spam_actions=["send_group_msg"],
            global_rate=0,
            group_cooldown=0,
        )
        bus, _adapter, bot = _setup_bus_with_handler(cfg)
        api = _TimedApiClient(bus=bus)
        bot.api = api  # type: ignore[assignment]

        await bus.dispatch(
            BusMessage(
                type=MessageType.ACTION,
                payload={"action": "send_group_msg", "group_id": 1, "message": "hello"},
                source="test",
            ),
            bot,
        )
        await bus.dispatch(
            BusMessage(
                type=MessageType.ACTION,
                payload={"action": "send_group_msg", "group_id": 1, "message": "world"},
                source="test",
            ),
            bot,
        )

        assert len(api.calls) == 2  # both pass, different msgs

    async def test_different_group_independent(self):
        """Same message to different groups are independent."""
        cfg = _make_bot_config(
            spam_window=5.0,
            spam_actions=["send_group_msg"],
            global_rate=0,
            group_cooldown=0,
        )
        bus, _adapter, bot = _setup_bus_with_handler(cfg)
        api = _TimedApiClient(bus=bus)
        bot.api = api  # type: ignore[assignment]

        await bus.dispatch(
            BusMessage(
                type=MessageType.ACTION,
                payload={"action": "send_group_msg", "group_id": 1, "message": "hello"},
                source="test",
            ),
            bot,
        )
        await bus.dispatch(
            BusMessage(
                type=MessageType.ACTION,
                payload={"action": "send_group_msg", "group_id": 2, "message": "hello"},
                source="test",
            ),
            bot,
        )

        assert len(api.calls) == 2  # both pass, different groups

    async def test_window_expiry_allows_again(self):
        """After spam_window passes, same action is allowed."""
        cfg = _make_bot_config(
            spam_window=0.05,
            spam_actions=["send_group_msg"],
            global_rate=0,
            group_cooldown=0,
        )
        bus, _adapter, bot = _setup_bus_with_handler(cfg)
        api = _TimedApiClient(bus=bus)
        bot.api = api  # type: ignore[assignment]

        payload = {"action": "send_group_msg", "group_id": 1, "message": "hello"}

        r1 = await bus.dispatch(
            msg=BusMessage(type=MessageType.ACTION, payload=payload, source="test"),
            bot=bot,
        )
        assert isinstance(r1, dict)

        # Wait for window to expire.
        await asyncio.sleep(0.1)

        r2 = await bus.dispatch(
            msg=BusMessage(type=MessageType.ACTION, payload=payload, source="test"),
            bot=bot,
        )
        assert isinstance(r2, dict)  # now passes again
        assert len(api.calls) == 2

    async def test_window_extends_on_pass(self):
        """Each pass resets the window (sliding window)."""
        cfg = _make_bot_config(
            spam_window=0.2,
            spam_actions=["send_group_msg"],
            global_rate=0,
            group_cooldown=0,
        )
        bus, _adapter, bot = _setup_bus_with_handler(cfg)
        api = _TimedApiClient(bus=bus)
        bot.api = api  # type: ignore[assignment]

        payload = {"action": "send_group_msg", "group_id": 1, "message": "hello"}

        await bus.dispatch(
            msg=BusMessage(type=MessageType.ACTION, payload=payload, source="test"),
            bot=bot,
        )
        await asyncio.sleep(0.1)  # within window
        # Duplicate within window → blocked.
        r = await bus.dispatch(
            msg=BusMessage(type=MessageType.ACTION, payload=payload, source="test"),
            bot=bot,
        )
        assert r is True

        await asyncio.sleep(
            0.15
        )  # still within original window, but last (dropped) didn't update timestamp
        r = await bus.dispatch(
            msg=BusMessage(type=MessageType.ACTION, payload=payload, source="test"),
            bot=bot,
        )
        assert isinstance(r, dict)  # passes because original timestamp + 0.25 > 0.2
        assert len(api.calls) == 2


    async def test_poke_dedup(self):
        """send_group_poke (戳一戳) with same target is deduped."""
        cfg = _make_bot_config(
            spam_window=5.0,
            spam_actions=["send_group_poke"],
            global_rate=0,
            group_cooldown=0,
        )
        bus, _adapter, bot = _setup_bus_with_handler(cfg)
        api = _TimedApiClient(bus=bus)
        bot.api = api  # type: ignore[assignment]

        payload = {"action": "send_group_poke", "group_id": 1, "user_id": 999}
        await bus.dispatch(
            msg=BusMessage(type=MessageType.ACTION, payload=payload, source="test"),
            bot=bot,
        )
        r = await bus.dispatch(
            msg=BusMessage(type=MessageType.ACTION, payload=payload, source="test"),
            bot=bot,
        )

        assert r is True  # duplicate poke dropped
        assert len(api.calls) == 1


class TestSpamDisable:
    async def test_spam_window_zero_disables(self):
        """spam_window=0 disables dedup entirely."""
        cfg = _make_bot_config(
            spam_window=0,
            spam_actions=["send_group_msg"],
            global_rate=0,
            group_cooldown=0,
        )
        bus, _adapter, bot = _setup_bus_with_handler(cfg)
        api = _TimedApiClient(bus=bus)
        bot.api = api  # type: ignore[assignment]

        payload = {"action": "send_group_msg", "group_id": 1, "message": "hello"}
        for _ in range(3):
            await bus.dispatch(
                msg=BusMessage(type=MessageType.ACTION, payload=payload, source="test"),
                bot=bot,
            )

        assert len(api.calls) == 3

    async def test_action_not_in_spam_list(self):
        """Actions not in spam_actions skip dedup."""
        cfg = _make_bot_config(
            spam_window=5.0,
            spam_actions=["send_private_msg"],  # only private
            global_rate=0,
            group_cooldown=0,
        )
        bus, _adapter, bot = _setup_bus_with_handler(cfg)
        api = _TimedApiClient(bus=bus)
        bot.api = api  # type: ignore[assignment]

        payload = {"action": "send_group_msg", "group_id": 1, "message": "hello"}
        await bus.dispatch(
            msg=BusMessage(type=MessageType.ACTION, payload=payload, source="test"),
            bot=bot,
        )
        await bus.dispatch(
            msg=BusMessage(type=MessageType.ACTION, payload=payload, source="test"),
            bot=bot,
        )

        assert len(api.calls) == 2  # not deduped


class TestSpamBypass:
    async def test_bypass_skips_spam(self):
        """Bypassed actions are not checked for spam."""
        cfg = _make_bot_config(
            spam_window=5.0,
            spam_actions=["send_group_msg"],
            bypass_actions=["send_group_msg"],  # override bypass to include
            global_rate=0,
            group_cooldown=0,
        )
        bus, _adapter, bot = _setup_bus_with_handler(cfg)
        api = _TimedApiClient(bus=bus)
        bot.api = api  # type: ignore[assignment]

        payload = {"action": "send_group_msg", "group_id": 1, "message": "hello"}
        await bus.dispatch(
            msg=BusMessage(type=MessageType.ACTION, payload=payload, source="test"),
            bot=bot,
        )
        await bus.dispatch(
            msg=BusMessage(type=MessageType.ACTION, payload=payload, source="test"),
            bot=bot,
        )

        assert len(api.calls) == 2  # bypass skips spam check


class TestSpamPrivate:
    async def test_private_message_dedup(self):
        """send_private_msg with same params is deduped."""
        cfg = _make_bot_config(
            spam_window=5.0,
            spam_actions=["send_private_msg"],
            global_rate=0,
            group_cooldown=0,
        )
        bus, _adapter, bot = _setup_bus_with_handler(cfg)
        api = _TimedApiClient(bus=bus)
        bot.api = api  # type: ignore[assignment]

        payload = {"action": "send_private_msg", "user_id": 888, "message": "spam"}
        await bus.dispatch(
            msg=BusMessage(type=MessageType.ACTION, payload=payload, source="test"),
            bot=bot,
        )
        r = await bus.dispatch(
            msg=BusMessage(type=MessageType.ACTION, payload=payload, source="test"),
            bot=bot,
        )

        assert r is True
        assert len(api.calls) == 1


class TestSpamConcurrent:
    async def test_concurrent_dedup_atomic(self):
        """Concurrent identical actions: one passes, one is blocked."""
        cfg = _make_bot_config(
            spam_window=5.0,
            spam_actions=["send_group_msg"],
            global_rate=0,
            group_cooldown=0,
        )
        bus, _adapter, bot = _setup_bus_with_handler(cfg)
        api = _TimedApiClient(bus=bus)
        bot.api = api  # type: ignore[assignment]

        payload = {"action": "send_group_msg", "group_id": 1, "message": "hello"}

        async def send() -> bool:
            result = await bus.dispatch(
                msg=BusMessage(type=MessageType.ACTION, payload=payload, source="test"),
                bot=bot,
            )
            return isinstance(result, dict)

        results = await asyncio.gather(send(), send())
        passed = sum(results)
        assert passed == 1  # exactly one passes
        assert len(api.calls) == 1


# ---------------------------------------------------------------------------
# edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    async def test_empty_action_field(self):
        """ACTION with empty action field passes through."""
        cfg = _make_bot_config()
        bus, _adapter, bot = _setup_bus_with_handler(cfg)
        api = _TimedApiClient(bus=bus)
        bot.api = api  # type: ignore[assignment]

        msg = BusMessage(
            type=MessageType.ACTION,
            payload={"action": ""},
            source="test",
        )
        result = await bus.dispatch(msg, bot)

        # ActionQueueBusAdapter's handle calls _raw_call("") via call_next,
        # so it returns the dict with consumed result.
        assert isinstance(result, dict)
        assert result.get("action") == ""

    async def test_non_dict_payload(self):
        """Non-dict payload passes through."""
        cfg = _make_bot_config()
        bus, _adapter, bot = _setup_bus_with_handler(cfg)
        api = _TimedApiClient(bus=bus)
        bot.api = api  # type: ignore[assignment]

        msg = BusMessage(
            type=MessageType.ACTION,
            payload="not a dict",
            source="test",
        )
        result = await bus.dispatch(msg, bot)

        # adapter doesn't match non-dict payload → not consumed
        assert result is False

    async def test_private_message_no_group_id(self):
        """Private message without group_id does not trigger group cooldown."""
        cfg = _make_bot_config(global_rate=0, group_cooldown=0.5, per_action_delay=0)
        bus, _adapter, bot = _setup_bus_with_handler(cfg)
        api = _TimedApiClient(bus=bus)
        bot.api = api  # type: ignore[assignment]

        for i in range(2):
            msg = BusMessage(
                type=MessageType.ACTION,
                payload={
                    "action": "send_private_msg",
                    "user_id": i,
                    "message": "x",
                },
                source="test",
            )
            await bus.dispatch(msg, bot)

        assert len(api.calls) == 2
        gap = api.calls[1]["time"] - api.calls[0]["time"]
        assert gap < 0.3, f"private messages had gap {gap:.3f}"


# ---------------------------------------------------------------------------
# config integration (no bot needed, pure unit)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# spam cleanup (lazy eviction when dict exceeds _SPAM_MAX_ENTRIES)
# ---------------------------------------------------------------------------


class TestSpamCleanup:
    async def test_spam_cleanup_on_overflow(self, monkeypatch):
        """When _spam_last exceeds _SPAM_MAX_ENTRIES, stale entries are evicted."""
        from plugins.action_queue import ActionQueueMiddleware

        mw = ActionQueueMiddleware()
        # Enable spam for send_group_msg
        mw._spam_actions = {"send_group_msg"}
        mw._spam_window = 999.0  # huge window so entries stay "fresh"

        # Set a very low max to trigger cleanup easily
        mw._SPAM_MAX_ENTRIES = 5

        # Add entries up to the max
        for i in range(5):
            key = f"send_group_msg|{{\"group_id\": {i}, \"message\": \"msg{i}\"}}"
            mw._spam_last[key] = time.monotonic()

        assert len(mw._spam_last) == 5

        # Add one more – should trigger cleanup on next _check_spam
        mw._spam_last["extra_key"] = time.monotonic()
        assert len(mw._spam_last) == 6  # not cleaned yet (lazy, called from _check_spam)

        # Set a tiny window so the existing entries look stale
        mw._spam_window = 0.001  # tiny window
        await asyncio.sleep(0.01)  # ensure entries are older than cutoff

        # Now _check_spam for a new entry should trigger cleanup
        result = await mw._check_spam(
            "send_group_msg",
            {"action": "send_group_msg", "group_id": 999, "message": "new"},
        )
        # Should pass (not spam), and cleanup should have run
        assert result is False
        # After cleanup, old stale entries should be gone
        assert len(mw._spam_last) < 6


class TestConfigParsing:
    def test_default_config(self):
        """Default ActionQueueConfig has sensible values."""
        cfg = ActionQueueConfig()
        assert cfg.enabled is True
        assert cfg.global_rate == 5.0
        assert cfg.group_cooldown == 1.0
        assert cfg.per_action_delay == 0.0
        assert "get_group_info" in cfg.bypass_actions
        assert "get_msg" in cfg.bypass_actions
        assert cfg.block_actions == []

    def test_bot_config_includes_action_queue(self):
        """BotConfig has action_queue field with default."""
        cfg = BotConfig(
            bot=BotConfigSection(qq=1),
            napcat=NapCatConfig(),
            plugin=PluginConfig(),
            logging=LoggingConfigSection(),
        )
        assert cfg.action_queue.enabled is True
        assert cfg.action_queue.global_rate == 5.0
