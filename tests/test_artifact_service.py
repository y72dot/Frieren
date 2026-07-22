from __future__ import annotations

import base64
import json

import pytest

from src.adapters.qq.file_gateway import ResolvedQQFile
from src.core.artifacts import ArtifactService, ArtifactStatus, ArtifactStore
from src.core.message_store import MessageStore
from src.plugin.base import Event


class _Gateway:
    def __init__(self):
        self.uploads = []

    async def resolve(self, kind, file_id, *, out_format="mp3"):
        return ResolvedQQFile(
            base64=base64.b64encode(b"artifact-body").decode(),
            file_name="report.txt",
        )

    async def upload_group_file(self, group_id, path, name, *, folder_id=None):
        self.uploads.append(("group", group_id, path, name))
        return {"status": "ok", "data": {"file_id": "uploaded"}}

    async def upload_private_file(self, user_id, path, name):
        self.uploads.append(("private", user_id, path, name))
        return {"status": "ok"}


def _make_store(tmp_path):
    messages = MessageStore(db_path=":memory:")
    segment = {"type": "file", "data": {"file_id": "file-1", "name": "old.txt"}}
    messages.record(
        Event(
            type="message.private",
            raw={"time": 1},
            user_id=9,
            message_id=7,
            message="[CQ:file,file=file-1]",
            is_group=False,
            raw_message="[CQ:file,file=file-1]",
            message_array=[segment],
            raw_event_json=json.dumps({"message": [segment]}),
        )
    )
    store = ArtifactStore(tmp_path, connection=messages.connection)
    return messages, store, store.discover_message(7)[0]


@pytest.mark.asyncio
async def test_materialize_from_napcat_base64_and_reuse(tmp_path):
    messages, store, artifact = _make_store(tmp_path)
    gateway = _Gateway()
    service = ArtifactService(store, gateway)

    available = await service.materialize(artifact.artifact_id)
    reused = await service.materialize(artifact.artifact_id)

    assert available.status == ArtifactStatus.AVAILABLE
    assert available.file_name == "report.txt"
    assert available.local_path == reused.local_path
    messages.close()


@pytest.mark.asyncio
async def test_send_materializes_then_uploads(tmp_path):
    messages, store, artifact = _make_store(tmp_path)
    gateway = _Gateway()
    service = ArtifactService(store, gateway)

    result = await service.send(artifact.artifact_id, group_id=123)

    assert result["status"] == "ok"
    assert gateway.uploads[0][0:2] == ("group", 123)
    assert gateway.uploads[0][3] == "report.txt"
    messages.close()
