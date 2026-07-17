from __future__ import annotations

from typing import Any

from loguru import logger


class ApiClient:
    """Thin wrapper around the napcat-sdk client for API calls.

    The actual napcat client is injected via :meth:`set_client` after a
    WebSocket connection is established, and cleared via :meth:`clear_client`
    on disconnect.
    """

    def __init__(self) -> None:
        self._client: Any = None

    # ------------------------------------------------------------------
    # lifecycle (called by Bot)
    # ------------------------------------------------------------------

    def set_client(self, client: Any) -> None:
        """Inject the active napcat-sdk client."""
        self._client = client

    def clear_client(self) -> None:
        """Remove the client reference (e.g. on disconnect)."""
        self._client = None

    # ------------------------------------------------------------------
    # internal
    # ------------------------------------------------------------------

    def _ensure_client(self) -> Any:
        if self._client is None:
            raise RuntimeError("ApiClient is not connected – no active napcat client")
        return self._client

    async def _call(self, action: str, **params: Any) -> dict[str, Any]:
        client = self._ensure_client()
        try:
            method = getattr(client, action)
            return await method(**params)
        except Exception:
            logger.opt(exception=True).error(f"API call failed: {action}")
            raise

    # ------------------------------------------------------------------
    # messaging
    # ------------------------------------------------------------------

    async def send_group_msg(self, group_id: int, message: str) -> dict[str, Any]:
        """Send a plain-text message to a group."""
        return await self._call("send_group_msg", group_id=group_id, message=message)

    async def send_private_msg(self, user_id: int, message: str) -> dict[str, Any]:
        """Send a plain-text message to a private chat."""
        return await self._call("send_private_msg", user_id=user_id, message=message)

    # ------------------------------------------------------------------
    # group management
    # ------------------------------------------------------------------

    async def get_group_info(self, group_id: int) -> dict[str, Any]:
        return await self._call("get_group_info", group_id=group_id)

    async def get_group_member_info(
        self, group_id: int, user_id: int
    ) -> dict[str, Any]:
        return await self._call(
            "get_group_member_info", group_id=group_id, user_id=user_id
        )

    async def get_group_member_list(self, group_id: int) -> dict[str, Any]:
        return await self._call("get_group_member_list", group_id=group_id)

    async def set_group_ban(
        self, group_id: int, user_id: int, duration: int
    ) -> dict[str, Any]:
        return await self._call(
            "set_group_ban", group_id=group_id, user_id=user_id, duration=duration
        )

    async def set_group_kick(self, group_id: int, user_id: int) -> dict[str, Any]:
        return await self._call("set_group_kick", group_id=group_id, user_id=user_id)

    # ------------------------------------------------------------------
    # account
    # ------------------------------------------------------------------

    async def get_login_info(self) -> dict[str, Any]:
        return await self._call("get_login_info")

    async def get_friend_list(self) -> dict[str, Any]:
        return await self._call("get_friend_list")

    async def get_stranger_info(self, user_id: int) -> dict[str, Any]:
        return await self._call("get_stranger_info", user_id=user_id)

    # ------------------------------------------------------------------
    # escape hatch
    # ------------------------------------------------------------------

    async def call_action(self, action: str, **params: Any) -> dict[str, Any]:
        """Low-level API call for actions not yet wrapped as methods."""
        return await self._call(action, **params)
