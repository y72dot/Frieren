"""PluginContext, QQAgency, PluginConfigView, and permission enforcement.

New-style (package) plugins receive a restricted ``PluginContext`` instead
of the full ``Bot`` object.  Every QQ API call is mediated by
:class:`QQAgency` which checks declared :class:`ManifestPermissions` before
forwarding to the real API client.
"""

from __future__ import annotations

from collections.abc import Coroutine
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from loguru import logger

from src.plugin.manifest import ManifestPermissions

if TYPE_CHECKING:
    from src.core.message_bus import MessageBus
    from src.plugin.scope import ResourceScope


# ---------------------------------------------------------------------------
# PermissionDeniedError
# ---------------------------------------------------------------------------


class PermissionDeniedError(RuntimeError):
    """Raised when a plugin calls a capability it has not declared."""

    def __init__(
        self, plugin_id: str, permission: str, detail: str = ""
    ) -> None:
        self.plugin_id = plugin_id
        self.permission = permission
        self.detail = detail
        msg = f"Plugin '{plugin_id}' lacks permission '{permission}'"
        if detail:
            msg += f": {detail}"
        super().__init__(msg)


# ---------------------------------------------------------------------------
# PluginConfigView – read-only config subset
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PluginConfigView:
    """Immutable snapshot of bot configuration exposed to plugins.

    Built from ``BotConfig`` at context creation time.  Changes to the
    live config do **not** affect running handlers.
    """

    bot_id: int
    nickname: str
    admin_users: tuple[int, ...]
    llm_enabled: bool = False


# ---------------------------------------------------------------------------
# QQAgency – permission-checking API proxy
# ---------------------------------------------------------------------------


class QQAgency:
    """Restricted QQ API surface that checks :class:`ManifestPermissions.qq`
    before every call.
    """

    def __init__(
        self,
        api_client: Any,
        permissions: ManifestPermissions,
        plugin_id: str,
    ) -> None:
        self._api = api_client
        self._permissions = permissions
        self._plugin_id = plugin_id

    # -- permission check --------------------------------------------------

    def _check(self, required: str) -> None:
        if required not in self._permissions.qq:
            logger.warning(
                f"Permission denied: plugin='{self._plugin_id}' "
                f"permission='{required}'"
            )
            raise PermissionDeniedError(self._plugin_id, required)
        logger.debug(
            f"Permission allowed: plugin='{self._plugin_id}' "
            f"permission='{required}'"
        )

    # -- messaging ---------------------------------------------------------

    async def send_group_msg(self, group_id: int, message: str) -> dict:
        """Send a plain-text message to a group.

        Requires: ``qq.message.send``
        """
        self._check("message.send")
        return await self._api.send_group_msg(group_id, message)

    async def send_private_msg(self, user_id: int, message: str) -> dict:
        """Send a plain-text message to a private chat.

        Requires: ``qq.message.send``
        """
        self._check("message.send")
        return await self._api.send_private_msg(user_id, message)

    # -- interaction -------------------------------------------------------

    async def send_group_poke(self, group_id: int, user_id: int) -> dict:
        """Poke a user in a group.

        Requires: ``qq.message.react``
        """
        self._check("message.react")
        return await self._api.send_group_poke(group_id, user_id)

    # -- essence -----------------------------------------------------------

    async def set_essence_msg(self, message_id: int) -> dict:
        """Set a message as group essence.

        Requires: ``qq.message.react``
        """
        self._check("message.react")
        return await self._api.set_essence_msg(message_id)

    async def delete_essence_msg(self, message_id: int) -> dict:
        """Remove essence status from a message.

        Requires: ``qq.group.manage``
        """
        self._check("group.manage")
        return await self._api.delete_essence_msg(message_id)

    # -- escape hatch ------------------------------------------------------

    async def call_action(self, action: str, **params: Any) -> dict:
        """Low-level passthrough for NapCat actions not covered by typed methods.

        Requires: ``qq.message.react``
        """
        self._check("message.react")
        return await self._api.call_action(action, **params)


# ---------------------------------------------------------------------------
# PluginContext – restricted capability surface
# ---------------------------------------------------------------------------


@dataclass
class PluginContext:
    """Restricted capability surface passed to new-style plugin handlers.

    Design rules:
    - NOT a subclass or wrapper of Bot — standalone restricted view.
    - No ``_raw_call``, no ``message_bus.subscribe``, no config mutation.
    - ``emit_internal()`` is the only bus access (publish-only).
    - ``create_task()`` delegates to the generation-scoped
      :class:`ResourceScope`.
    """

    plugin_id: str
    version: str
    generation: int
    permissions: ManifestPermissions

    # Capabilities (set after construction by runtime).
    api: QQAgency | None = None
    config: PluginConfigView | None = None
    plugin_config: Any = None  # Typed dataclass instance or dict if no schema
    storage: Any = None  # PluginStorage, set if permissions.storage is non-empty
    scheduler: Any = None  # SchedulerAgency, set if permissions.scheduler == True

    # Internal (not exposed as public attributes to plugins).
    _bus: MessageBus | None = None
    _bot: Any = None
    _scope: ResourceScope | None = None

    # ------------------------------------------------------------------
    # convenience methods
    # ------------------------------------------------------------------

    async def reply(self, event: Any, text: str) -> bool:
        """Send *text* back to the same conversation (group or private).

        Returns ``True`` if the API call produced a result dict.
        """
        if self.api is None:
            return False
        if getattr(event, "group_id", None) is not None:
            result = await self.api.send_group_msg(event.group_id, text)
        else:
            result = await self.api.send_private_msg(event.user_id, text)
        return result is not None

    def emit_internal(self, topic: str, data: dict | None = None) -> None:
        """Publish an INTERNAL message on the bus (fire-and-forget)."""
        if self._bus is None or self._bot is None:
            return
        import asyncio

        from src.core.message_bus import BusMessage, MessageType

        msg = BusMessage(
            type=MessageType.INTERNAL,
            payload={"topic": topic, "data": data or {}},
            source=self.plugin_id,
        )
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._bus.dispatch(msg, self._bot))

    async def emit_internal_and_wait(
        self, topic: str, data: dict | None = None
    ) -> None:
        """Publish an INTERNAL message and wait for all handlers to complete."""
        if self._bus is None or self._bot is None:
            return
        from src.core.message_bus import BusMessage, MessageType

        msg = BusMessage(
            type=MessageType.INTERNAL,
            payload={"topic": topic, "data": data or {}},
            source=self.plugin_id,
        )
        await self._bus.emit_and_wait(msg, self._bot)

    async def get_recent_messages(
        self, group_id: int, n: int = 10, exclude_user_id: int | None = None
    ) -> list:
        """Get recent messages for a group from the message store.

        Requires: ``qq.message.react``
        """
        if self._bot is None:
            return []
        return self._bot.msg_store.recent(group_id, n, exclude_user_id)

    def record_bot_message(
        self,
        message_id: int,
        group_id: int | None,
        user_id: int,
        nickname: str,
        content: str,
        time: int,
        is_group: bool,
        peer_id: int | None = None,
    ) -> None:
        """Record a bot-sent message in the message store.

        Requires: ``qq.message.send``
        """
        if self._bot is None:
            return
        self._bot.msg_store.record_bot_message(
            message_id=message_id,
            group_id=group_id,
            user_id=user_id,
            nickname=nickname,
            content=content,
            time=time,
            is_group=is_group,
            peer_id=peer_id,
        )

    def create_task(self, name: str, coro: Coroutine) -> Any:
        """Create a managed background task tracked by the plugin's
        :class:`ResourceScope`.

        Returns an :class:`asyncio.Task`.
        """
        if self._scope is None:
            coro.close()
            raise RuntimeError(
                f"Plugin '{self.plugin_id}': no ResourceScope – "
                f"cannot create background task"
            )
        return self._scope.create_task(name, coro)
