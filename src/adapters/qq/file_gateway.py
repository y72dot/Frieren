from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ResolvedQQFile:
    file: str | None = None
    url: str | None = None
    base64: str | None = None
    file_name: str | None = None
    file_size: int | None = None


class QQFileGateway:
    """Typed facade over NapCat's file actions."""

    def __init__(self, api: Any) -> None:
        self.api = api

    async def resolve(
        self, kind: str, file_id: str, *, out_format: str = "mp3"
    ) -> ResolvedQQFile:
        action = {
            "image": "get_image",
            "record": "get_record",
        }.get(kind, "get_file")
        params: dict[str, Any] = {"file_id": file_id, "file": file_id}
        if action == "get_record":
            params["out_format"] = out_format
        response = await self.api.call_action(action, **params)
        data = _response_data(response)
        return ResolvedQQFile(
            file=_string(data.get("file")),
            url=_string(data.get("url")),
            base64=_string(data.get("base64")),
            file_name=_string(data.get("file_name")),
            file_size=_integer(data.get("file_size")),
        )

    async def get_group_file_url(self, group_id: int, file_id: str) -> str:
        response = await self.api.call_action(
            "get_group_file_url", group_id=group_id, file_id=file_id
        )
        return str(_response_data(response).get("url", ""))

    async def get_private_file_url(self, user_id: int, file_id: str) -> str:
        response = await self.api.call_action(
            "get_private_file_url", user_id=user_id, file_id=file_id
        )
        return str(_response_data(response).get("url", ""))

    async def list_group_root_files(
        self, group_id: int, *, file_count: int = 50
    ) -> dict[str, Any]:
        response = await self.api.call_action(
            "get_group_root_files", group_id=group_id, file_count=file_count
        )
        return _response_data(response)

    async def upload_group_file(
        self,
        group_id: int,
        path: str,
        name: str,
        *,
        folder_id: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "group_id": group_id,
            "file": path,
            "name": name,
            "upload_file": True,
        }
        if folder_id:
            params["folder"] = folder_id
        return await self.api.call_action("upload_group_file", **params)

    async def upload_private_file(
        self, user_id: int, path: str, name: str
    ) -> dict[str, Any]:
        return await self.api.call_action(
            "upload_private_file",
            user_id=user_id,
            file=path,
            name=name,
            upload_file=True,
        )


def _response_data(response: Any) -> dict[str, Any]:
    if not isinstance(response, dict):
        raise RuntimeError("NapCat returned a non-object response")
    if response.get("status") == "failed" or response.get("retcode") not in (None, 0):
        raise RuntimeError(
            str(
                response.get("message")
                or response.get("wording")
                or "NapCat file action failed"
            )
        )
    data = response.get("data", response)
    if not isinstance(data, dict):
        raise RuntimeError("NapCat file action returned no data")
    return data


def _string(value: Any) -> str | None:
    return str(value) if value not in (None, "") else None


def _integer(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
