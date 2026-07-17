from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from loguru import logger

from src.core.message_bus import BusMessage, MessageType

if TYPE_CHECKING:
    from src.core.message_bus import MessageBus


class ApiClientProtocol(Protocol):
    """Protocol defining the ApiClient interface for testability.

    Test doubles can implement this protocol without importing napcat-sdk.
    """

    async def send_group_msg(self, group_id: int, message: str) -> dict[str, Any]: ...
    async def send_private_msg(self, user_id: int, message: str) -> dict[str, Any]: ...
    async def get_group_info(self, group_id: int) -> dict[str, Any]: ...
    async def get_group_member_info(self, group_id: int, user_id: int) -> dict[str, Any]: ...
    async def get_group_member_list(self, group_id: int) -> dict[str, Any]: ...
    async def set_group_ban(self, group_id: int, user_id: int, duration: int) -> dict[str, Any]: ...
    async def set_group_kick(self, group_id: int, user_id: int) -> dict[str, Any]: ...
    async def send_group_poke(self, group_id: int, user_id: int) -> dict[str, Any]: ...
    async def get_login_info(self) -> dict[str, Any]: ...
    async def get_friend_list(self) -> dict[str, Any]: ...
    async def get_stranger_info(self, user_id: int) -> dict[str, Any]: ...
    async def send_group_forward_msg(self, group_id: int, nodes: list[dict[str, Any]]) -> dict[str, Any]: ...
    async def get_msg(self, message_id: int) -> dict[str, Any]: ...
    async def call_action(self, action: str, **params: Any) -> dict[str, Any]: ...


class ApiClient:
    """Thin wrapper around the napcat-sdk client for API calls.

    When a :class:`MessageBus` is injected, all public methods route
    through the bus as ACTION messages (going through the send-filter
    chain).  Without a bus (e.g. during tests) they call the napcat
    client directly.
    """

    def __init__(self, bus: MessageBus | None = None) -> None:
        self._client: Any = None
        self._bus = bus
        self._bot: Any = None

    # ------------------------------------------------------------------
    # lifecycle (called by Bot)
    # ------------------------------------------------------------------

    def set_client(self, client: Any) -> None:
        """Inject the active napcat-sdk client."""
        self._client = client
        logger.info("NapCat client connected")

    def clear_client(self) -> None:
        """Remove the client reference (e.g. on disconnect)."""
        self._client = None
        logger.info("NapCat client disconnected")

    def set_bot(self, bot: Any) -> None:
        """Store a reference to the Bot instance (needed by the message bus)."""
        self._bot = bot

    # ------------------------------------------------------------------
    # internal
    # ------------------------------------------------------------------

    def _ensure_client(self) -> Any:
        if self._client is None:
            logger.warning("ApiClient is not connected – no active napcat client")
            raise RuntimeError("ApiClient is not connected – no active napcat client")
        return self._client

    async def _call(self, action: str, **params: Any) -> dict[str, Any]:
        client = self._ensure_client()
        # Truncate message content for logging brevity.
        log_params = {}
        for k, v in params.items():
            if k == "message" and isinstance(v, str) and len(v) > 50:
                log_params[k] = v[:50] + "..."
            else:
                log_params[k] = v
        logger.debug(f"API call {action} {log_params}")
        try:
            method = getattr(client, action)
            result = await method(**params)  # type: ignore[no-any-return]
            logger.debug(f"API ok {action}")
            return result
        except Exception:
            logger.opt(exception=True).error(f"API call failed: {action}")
            raise

    async def _raw_call(self, action: str, **params: Any) -> dict[str, Any]:
        """Direct API call that bypasses the message bus.

        Used by the built-in ``_qq_exec`` handler to perform the
        actual napcat API invocation without re-entering the bus.
        """
        return await self._call(action, **params)

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    async def _dispatch_action(self, action: str, **params: Any) -> dict[str, Any]:
        """Route an ACTION message through the bus, or direct call if no bus."""
        if self._bus is not None:
            logger.debug(f"Dispatching ACTION via bus: {action}")
            payload: dict[str, Any] = {"action": action, **params}
            msg = BusMessage(
                type=MessageType.ACTION,
                payload=payload,
                source="api_client",
            )
            result = await self._bus.dispatch(msg, self._bot)
            return result if isinstance(result, dict) else {}
        logger.debug(f"Direct API call (no bus): {action}")
        return await self._call(action, **params)

    # ------------------------------------------------------------------
    # messaging
    # ------------------------------------------------------------------

    async def send_group_msg(self, group_id: int, message: str) -> dict[str, Any]:
        """Send a plain-text message to a group."""
        return await self._dispatch_action(
            "send_group_msg", group_id=group_id, message=message
        )

    async def send_private_msg(self, user_id: int, message: str) -> dict[str, Any]:
        """Send a plain-text message to a private chat."""
        return await self._dispatch_action(
            "send_private_msg", user_id=user_id, message=message
        )

    # ------------------------------------------------------------------
    # group management
    # ------------------------------------------------------------------

    async def get_group_info(self, group_id: int) -> dict[str, Any]:
        return await self._dispatch_action("get_group_info", group_id=group_id)

    async def get_group_member_info(
        self, group_id: int, user_id: int
    ) -> dict[str, Any]:
        return await self._dispatch_action(
            "get_group_member_info", group_id=group_id, user_id=user_id
        )

    async def get_group_member_list(self, group_id: int) -> dict[str, Any]:
        return await self._dispatch_action("get_group_member_list", group_id=group_id)

    async def set_group_ban(
        self, group_id: int, user_id: int, duration: int
    ) -> dict[str, Any]:
        return await self._dispatch_action(
            "set_group_ban", group_id=group_id, user_id=user_id, duration=duration
        )

    async def set_group_kick(self, group_id: int, user_id: int) -> dict[str, Any]:
        return await self._dispatch_action(
            "set_group_kick", group_id=group_id, user_id=user_id
        )

    async def send_group_poke(self, group_id: int, user_id: int) -> dict[str, Any]:
        """Poke a user in a group."""
        return await self._dispatch_action(
            "group_poke", group_id=group_id, user_id=user_id, target_id=user_id
        )

    # ------------------------------------------------------------------
    # account
    # ------------------------------------------------------------------

    async def get_login_info(self) -> dict[str, Any]:
        return await self._dispatch_action("get_login_info")

    async def get_friend_list(self) -> dict[str, Any]:
        return await self._dispatch_action("get_friend_list")

    async def get_stranger_info(self, user_id: int) -> dict[str, Any]:
        return await self._dispatch_action("get_stranger_info", user_id=user_id)

    # ------------------------------------------------------------------
    # forwarding / message retrieval
    # ------------------------------------------------------------------

    async def send_group_forward_msg(
        self, group_id: int, nodes: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Send a merged-forward message composed of *nodes*."""
        return await self._dispatch_action(
            "send_group_forward_msg", group_id=group_id, messages=nodes
        )

    async def get_msg(self, message_id: int) -> dict[str, Any]:
        """Retrieve a single message by its napcat message_id."""
        return await self._dispatch_action("get_msg", message_id=message_id)

    # ------------------------------------------------------------------
    # escape hatch
    # ------------------------------------------------------------------

    async def call_action(self, action: str, **params: Any) -> dict[str, Any]:
        """Low-level API call for actions not yet wrapped as methods."""
        return await self._dispatch_action(action, **params)
