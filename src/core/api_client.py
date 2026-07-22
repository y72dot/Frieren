from __future__ import annotations

import time as _time
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
    async def get_group_member_info(
        self, group_id: int, user_id: int
    ) -> dict[str, Any]: ...
    async def get_group_member_list(self, group_id: int) -> dict[str, Any]: ...
    async def set_group_ban(
        self, group_id: int, user_id: int, duration: int
    ) -> dict[str, Any]: ...
    async def set_group_kick(self, group_id: int, user_id: int) -> dict[str, Any]: ...
    async def send_group_poke(self, group_id: int, user_id: int) -> dict[str, Any]: ...
    async def get_login_info(self) -> dict[str, Any]: ...
    async def get_friend_list(self) -> dict[str, Any]: ...
    async def get_stranger_info(self, user_id: int) -> dict[str, Any]: ...
    async def send_group_forward_msg(
        self, group_id: int, nodes: list[dict[str, Any]]
    ) -> dict[str, Any]: ...
    async def get_msg(self, message_id: int) -> dict[str, Any]: ...
    async def get_forward_msg(self, forward_id: str) -> dict[str, Any]: ...
    async def get_group_msg_history(
        self, group_id: int, message_seq: int | None = None, count: int = 20
    ) -> dict[str, Any]: ...
    async def get_friend_msg_history(
        self, user_id: int, message_seq: int | None = None, count: int = 20
    ) -> dict[str, Any]: ...
    async def set_essence_msg(self, message_id: int) -> dict[str, Any]: ...
    async def delete_essence_msg(self, message_id: int) -> dict[str, Any]: ...
    async def call_action(self, action: str, **params: Any) -> dict[str, Any]: ...
    async def call_action_quiet(
        self, action: str, **params: Any
    ) -> dict[str, Any]: ...
    async def get_file(self, file_id: str) -> dict[str, Any]: ...
    async def upload_group_file(
        self, group_id: int, file: str, name: str, folder: str | None = None
    ) -> dict[str, Any]: ...
    async def upload_private_file(
        self, user_id: int, file: str, name: str
    ) -> dict[str, Any]: ...


class ApiClient:
    """Thin wrapper around the napcat-sdk client for API calls.

    When a :class:`MessageBus` is injected, all public methods route
    through the bus as ACTION messages (going through the send-filter
    chain).  Without a bus (e.g. during tests) they call the napcat
    client directly.
    """

    records_outbound = True

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

    async def _call(
        self, action: str, *, log_errors: bool = True, **params: Any
    ) -> dict[str, Any]:
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
            t0 = _time.time()
            method = getattr(client, action)
            result = await method(**params)  # type: ignore[no-any-return]
            elapsed = (_time.time() - t0) * 1000
            status = result.get("status") if isinstance(result, dict) else "?"
            msg_id = result.get("message_id") if isinstance(result, dict) else None
            retcode = result.get("retcode") if isinstance(result, dict) else None
            parts = [f"API ok {action}", f"status={status}", f"elapsed={elapsed:.0f}ms"]
            if msg_id is not None:
                parts.append(f"message_id={msg_id}")
            if retcode is not None:
                parts.append(f"retcode={retcode}")
            logger.debug(" ".join(parts))
            return result
        except Exception:
            if log_errors:
                logger.opt(exception=True).error(f"API call failed: {action}")
            else:
                logger.debug(f"Optional API call unavailable: {action}")
            raise

    async def _raw_call(
        self, action: str, *, log_errors: bool = True, **params: Any
    ) -> dict[str, Any]:
        """Direct API call that bypasses the message bus.

        Used by the built-in ``_qq_exec`` handler to perform the
        actual napcat API invocation without re-entering the bus.
        """
        result = await self._call(action, log_errors=log_errors, **params)
        self._record_outbound_message(action, params, result)
        return result

    def _record_outbound_message(
        self,
        action: str,
        params: dict[str, Any],
        result: dict[str, Any],
    ) -> None:
        """Persist every successful text send at the common NapCat boundary."""
        if action not in {"send_group_msg", "send_private_msg"} or self._bot is None:
            return
        message_id = result.get("message_id")
        if message_id is None and isinstance(result.get("data"), dict):
            message_id = result["data"].get("message_id")
        if message_id is None:
            return
        message = params.get("message")
        if not isinstance(message, str):
            return
        try:
            config = (
                self._bot.config_center.config
                if self._bot.config_center
                else self._bot.config
            )
            bot_qq = config.bot.qq
            nickname = config.bot.nickname[0] if config.bot.nickname else str(bot_qq)
            is_group = action == "send_group_msg"
            peer_id = None if is_group else int(params["user_id"])
            self._bot.msg_store.record_bot_message(
                message_id=int(message_id),
                group_id=int(params["group_id"]) if is_group else None,
                user_id=bot_qq,
                nickname=nickname,
                content=message,
                time=int(_time.time()),
                is_group=is_group,
                peer_id=peer_id,
            )
        except Exception:
            # QQ action already succeeded; storage failure is observable but must
            # not turn a successful send into a retry that duplicates the message.
            logger.opt(exception=True).error(
                f"Failed to persist outbound message: action={action} id={message_id}"
            )

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    async def _dispatch_action(self, action: str, **params: Any) -> dict[str, Any]:
        """Route an ACTION message through the bus, or direct call if no bus."""
        quiet = bool(params.pop("_qqbot_quiet", False))
        if self._bus is not None:
            logger.debug(f"Dispatching ACTION via bus: {action}")
            payload: dict[str, Any] = {"action": action, **params}
            if quiet:
                payload["_qqbot_quiet"] = True
            msg = BusMessage(
                type=MessageType.ACTION,
                payload=payload,
                source="api_client",
            )
            result = await self._bus.dispatch(msg, self._bot)
            return result if isinstance(result, dict) else {}
        logger.debug(f"Direct API call (no bus): {action}")
        try:
            return await self._call(action, log_errors=not quiet, **params)
        except Exception as exc:
            if not quiet:
                raise
            return {
                "status": "failed",
                "retcode": -1,
                "data": None,
                "message": str(exc),
            }

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

    async def get_forward_msg(self, forward_id: str) -> dict[str, Any]:
        """Retrieve the content of a merged-forward message by its forward ID."""
        return await self._dispatch_action("get_forward_msg", message_id=forward_id)

    # ------------------------------------------------------------------
    # files
    # ------------------------------------------------------------------

    async def get_file(self, file_id: str) -> dict[str, Any]:
        return await self._dispatch_action("get_file", file=file_id, file_id=file_id)

    async def get_image(self, file_id: str) -> dict[str, Any]:
        return await self._dispatch_action("get_image", file=file_id, file_id=file_id)

    async def get_record(self, file_id: str, out_format: str = "mp3") -> dict[str, Any]:
        return await self._dispatch_action(
            "get_record", file=file_id, file_id=file_id, out_format=out_format
        )

    async def get_recent_contact(self, count: int = 50) -> dict[str, Any]:
        return await self._dispatch_action("get_recent_contact", count=count)

    async def get_group_msg_history(
        self, group_id: int, message_seq: int | None = None, count: int = 20
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "group_id": group_id,
            "count": count,
            "reverse_order": False,
            "reverseOrder": False,
            "disable_get_url": False,
            "parse_mult_msg": True,
            "quick_reply": False,
        }
        if message_seq is not None:
            params["message_seq"] = message_seq
        return await self._dispatch_action("get_group_msg_history", **params)

    async def get_friend_msg_history(
        self, user_id: int, message_seq: int | None = None, count: int = 20
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "user_id": user_id,
            "count": count,
            "reverse_order": False,
            "reverseOrder": False,
            "disable_get_url": False,
            "parse_mult_msg": True,
            "quick_reply": False,
        }
        if message_seq is not None:
            params["message_seq"] = message_seq
        return await self._dispatch_action("get_friend_msg_history", **params)

    async def upload_group_file(
        self, group_id: int, file: str, name: str, folder: str | None = None
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "group_id": group_id,
            "file": file,
            "name": name,
            "upload_file": True,
        }
        if folder:
            params["folder"] = folder
        return await self._dispatch_action("upload_group_file", **params)

    async def upload_private_file(
        self, user_id: int, file: str, name: str
    ) -> dict[str, Any]:
        return await self._dispatch_action(
            "upload_private_file",
            user_id=user_id,
            file=file,
            name=name,
            upload_file=True,
        )

    # ------------------------------------------------------------------
    # essence
    # ------------------------------------------------------------------

    async def set_essence_msg(self, message_id: int) -> dict[str, Any]:
        """Set a message as group essence."""
        return await self._dispatch_action("set_essence_msg", message_id=message_id)

    async def delete_essence_msg(self, message_id: int) -> dict[str, Any]:
        """Remove a message from group essence."""
        return await self._dispatch_action("delete_essence_msg", message_id=message_id)

    # ------------------------------------------------------------------
    # escape hatch
    # ------------------------------------------------------------------

    async def call_action(self, action: str, **params: Any) -> dict[str, Any]:
        """Low-level API call for actions not yet wrapped as methods."""
        return await self._dispatch_action(action, **params)

    async def call_action_quiet(self, action: str, **params: Any) -> dict[str, Any]:
        """Call an optional read action and return failures without ERROR noise."""
        return await self._dispatch_action(action, _qqbot_quiet=True, **params)
