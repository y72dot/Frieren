from __future__ import annotations

import ipaddress
import socket
from pathlib import Path
from urllib.parse import urlparse

import httpx

from src.adapters.qq.file_gateway import QQFileGateway
from src.core.artifacts.store import Artifact, ArtifactStatus, ArtifactStore


class ArtifactService:
    """Coordinates lazy NapCat resolution, safe download and QQ uploads."""

    def __init__(
        self,
        store: ArtifactStore,
        gateway: QQFileGateway,
        *,
        download_timeout: int = 60,
    ) -> None:
        self.store = store
        self.gateway = gateway
        self.download_timeout = download_timeout

    async def materialize(self, artifact_id: str) -> Artifact:
        artifact = self.store.get(artifact_id, touch=True)
        if artifact is None:
            raise KeyError(f"artifact not found: {artifact_id}")
        if artifact.status == ArtifactStatus.AVAILABLE and artifact.local_path:
            return artifact
        self.store.set_materializing(artifact_id)
        try:
            resolved = await self.gateway.resolve(
                artifact.kind, artifact.napcat_file_id or artifact.remote_url or ""
            )
            file_name = resolved.file_name or artifact.file_name
            if resolved.base64:
                return self.store.import_base64(
                    artifact_id, resolved.base64, file_name=file_name
                )
            if resolved.file and Path(resolved.file).is_file():
                return self.store.import_path(
                    artifact_id, resolved.file, file_name=file_name
                )
            url = resolved.url or artifact.remote_url
            if url:
                return await self._download(artifact_id, url, file_name=file_name)
            raise RuntimeError("NapCat did not return accessible file content")
        except Exception as exc:
            self.store.fail(artifact_id, str(exc))
            raise

    async def _download(
        self, artifact_id: str, url: str, *, file_name: str | None
    ) -> Artifact:
        await _validate_public_url(url)
        async with (
            httpx.AsyncClient(
                timeout=self.download_timeout, follow_redirects=False
            ) as client,
            client.stream("GET", url) as response,
        ):
            response.raise_for_status()
            length = response.headers.get("content-length")
            if length and int(length) > self.store.max_file_size:
                raise ValueError("remote artifact exceeds max_file_size")
            content = bytearray()
            async for chunk in response.aiter_bytes():
                content.extend(chunk)
                if len(content) > self.store.max_file_size:
                    raise ValueError("remote artifact exceeds max_file_size")
        return self.store.import_bytes(artifact_id, bytes(content), file_name=file_name)

    async def send(
        self,
        artifact_id: str,
        *,
        group_id: int | None = None,
        user_id: int | None = None,
        name: str | None = None,
    ) -> dict:
        if (group_id is None) == (user_id is None):
            raise ValueError("provide exactly one of group_id or user_id")
        artifact = await self.materialize(artifact_id)
        if not artifact.local_path:
            raise RuntimeError("artifact is not locally available")
        upload_name = (
            name or artifact.file_name or artifact.sha256 or artifact.artifact_id
        )
        if group_id is not None:
            return await self.gateway.upload_group_file(
                group_id, artifact.local_path, upload_name
            )
        assert user_id is not None
        return await self.gateway.upload_private_file(
            user_id, artifact.local_path, upload_name
        )


async def _validate_public_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("artifact URL must use http or https")
    loop_results = (
        await __import__("asyncio")
        .get_running_loop()
        .run_in_executor(
            None,
            socket.getaddrinfo,
            parsed.hostname,
            parsed.port or (443 if parsed.scheme == "https" else 80),
        )
    )
    for item in loop_results:
        address = ipaddress.ip_address(item[4][0])
        if not address.is_global:
            raise ValueError("artifact URL resolves to a non-public address")
