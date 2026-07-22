"""Tests for ApiClient wrapper."""

import pytest

from src.core.api_client import ApiClient

# -------------------------------------------------------------------
# ensure_client
# -------------------------------------------------------------------


def test_ensure_client_raises_when_not_connected():
    client = ApiClient()
    with pytest.raises(RuntimeError, match="not connected"):
        client._ensure_client()


def test_ensure_client_returns_client_when_set():
    client = ApiClient()
    dummy = object()
    client.set_client(dummy)
    assert client._ensure_client() is dummy


def test_clear_client_removes_reference():
    client = ApiClient()
    client.set_client(object())
    assert client._ensure_client() is not None
    client.clear_client()
    assert client._client is None


# -------------------------------------------------------------------
# messaging methods (delegation)
# -------------------------------------------------------------------


class _DummyNapCat:
    """A minimal fake that records method calls."""

    def __init__(self):
        self.called: list[dict] = []

    async def send_group_msg(self, **kwargs):
        self.called.append({"method": "send_group_msg", "args": kwargs})
        return {"message_id": 1}

    async def send_private_msg(self, **kwargs):
        self.called.append({"method": "send_private_msg", "args": kwargs})
        return {"message_id": 2}

    async def get_group_info(self, **kwargs):
        self.called.append({"method": "get_group_info", "args": kwargs})
        return {"group_name": "test"}

    async def get_group_member_info(self, **kwargs):
        self.called.append({"method": "get_group_member_info", "args": kwargs})
        return {}

    async def get_group_member_list(self, **kwargs):
        self.called.append({"method": "get_group_member_list", "args": kwargs})
        return {}

    async def set_group_ban(self, **kwargs):
        self.called.append({"method": "set_group_ban", "args": kwargs})
        return {}

    async def set_group_kick(self, **kwargs):
        self.called.append({"method": "set_group_kick", "args": kwargs})
        return {}

    async def send_group_forward_msg(self, **kwargs):
        self.called.append({"method": "send_group_forward_msg", "args": kwargs})
        return {"message_id": 100}

    async def get_msg(self, **kwargs):
        self.called.append({"method": "get_msg", "args": kwargs})
        return {"message_id": kwargs["message_id"], "content": "test"}

    async def call_action_unknown(self, **kwargs):
        self.called.append({"method": "call_action_unknown", "args": kwargs})
        return {}


@pytest.mark.asyncio
async def test_send_group_msg_delegates():
    client = ApiClient()
    dummy = _DummyNapCat()
    client.set_client(dummy)

    result = await client.send_group_msg(group_id=123, message="hello")
    assert result["message_id"] == 1
    assert dummy.called[0]["args"] == {"group_id": 123, "message": "hello"}


@pytest.mark.asyncio
async def test_send_private_msg_delegates():
    client = ApiClient()
    dummy = _DummyNapCat()
    client.set_client(dummy)

    result = await client.send_private_msg(user_id=456, message="hi")
    assert result["message_id"] == 2
    assert dummy.called[0]["args"] == {"user_id": 456, "message": "hi"}


@pytest.mark.asyncio
async def test_send_private_msg_fails_without_client():
    client = ApiClient()
    with pytest.raises(RuntimeError, match="not connected"):
        await client.send_private_msg(user_id=1, message="x")


# -------------------------------------------------------------------
# group management
# -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_group_info_delegates():
    client = ApiClient()
    dummy = _DummyNapCat()
    client.set_client(dummy)

    result = await client.get_group_info(group_id=789)
    assert result["group_name"] == "test"


@pytest.mark.asyncio
async def test_get_group_member_info_delegates():
    client = ApiClient()
    dummy = _DummyNapCat()
    client.set_client(dummy)

    await client.get_group_member_info(group_id=1, user_id=2)
    assert dummy.called[0]["args"] == {"group_id": 1, "user_id": 2}


@pytest.mark.asyncio
async def test_set_group_ban_delegates():
    client = ApiClient()
    dummy = _DummyNapCat()
    client.set_client(dummy)

    await client.set_group_ban(group_id=1, user_id=2, duration=60)
    assert dummy.called[0]["args"] == {"group_id": 1, "user_id": 2, "duration": 60}


@pytest.mark.asyncio
async def test_set_group_kick_delegates():
    client = ApiClient()
    dummy = _DummyNapCat()
    client.set_client(dummy)

    await client.set_group_kick(group_id=1, user_id=2)
    assert dummy.called[0]["args"] == {"group_id": 1, "user_id": 2}


# -------------------------------------------------------------------
# escape hatch
# -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_call_action_escape_hatch():
    client = ApiClient()
    dummy = _DummyNapCat()
    client.set_client(dummy)

    result = await client.call_action("call_action_unknown", param_a=1, param_b="x")
    assert result == {}
    assert dummy.called[0]["method"] == "call_action_unknown"
    assert dummy.called[0]["args"] == {"param_a": 1, "param_b": "x"}


# -------------------------------------------------------------------
# api call failure propagation
# -------------------------------------------------------------------


class _FailingNapCat:
    async def send_group_msg(self, **kwargs):
        raise ConnectionError("network down")

    async def get_msg(self, **kwargs):
        raise LookupError("消息不存在")


@pytest.mark.asyncio
async def test_api_call_propagates_error():
    client = ApiClient()
    client.set_client(_FailingNapCat())
    with pytest.raises(ConnectionError, match="network down"):
        await client.send_group_msg(group_id=1, message="x")


@pytest.mark.asyncio
async def test_quiet_action_returns_failure_instead_of_raising():
    client = ApiClient()
    client.set_client(_FailingNapCat())

    result = await client.call_action_quiet("get_msg", message_id=42)

    assert result["status"] == "failed"
    assert "消息不存在" in result["message"]


@pytest.mark.asyncio
async def test_bus_backed_client_records_all_outbound_messages(bot_config):
    """The common QQ execution boundary persists sends from every plugin."""
    from src.core.bot import Bot
    from src.core.message_store import MessageStore

    bot = Bot(config=bot_config)
    bot.msg_store.close()
    bot.msg_store = MessageStore(db_path=":memory:")
    dummy = _DummyNapCat()
    bot.api.set_client(dummy)

    await bot.api.send_group_msg(group_id=123, message="from any plugin")
    await bot.api.send_private_msg(user_id=456, message="private outbound")

    group_record = bot.msg_store.get_message_record(1)
    private_record = bot.msg_store.get_message_record(2)
    assert group_record is not None
    assert group_record["conversation_type"] == "group"
    assert group_record["conversation_id"] == 123
    assert group_record["is_from_bot"] == 1
    assert private_record is not None
    assert private_record["conversation_type"] == "private"
    assert private_record["conversation_id"] == 456
    assert private_record["is_from_bot"] == 1
