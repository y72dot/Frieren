from __future__ import annotations

import pytest

from src.adapters.qq.file_gateway import QQFileGateway


class _Api:
    def __init__(self):
        self.calls = []

    async def call_action(self, action, **params):
        self.calls.append((action, params))
        if action.startswith("get_"):
            return {
                "status": "ok",
                "retcode": 0,
                "data": {"file": "/tmp/a", "file_size": "12"},
            }
        return {"status": "ok", "data": {"file_id": "new"}}


@pytest.mark.asyncio
async def test_record_resolution_uses_required_output_format():
    api = _Api()
    gateway = QQFileGateway(api)

    resolved = await gateway.resolve("record", "voice-id", out_format="wav")

    assert resolved.file_size == 12
    assert api.calls == [
        ("get_record", {"file_id": "voice-id", "file": "voice-id", "out_format": "wav"})
    ]


@pytest.mark.asyncio
async def test_upload_group_file_uses_official_fields():
    api = _Api()
    gateway = QQFileGateway(api)

    await gateway.upload_group_file(12, "/data/blob", "x.bin", folder_id="folder")

    assert api.calls[0] == (
        "upload_group_file",
        {
            "group_id": 12,
            "file": "/data/blob",
            "name": "x.bin",
            "upload_file": True,
            "folder": "folder",
        },
    )
