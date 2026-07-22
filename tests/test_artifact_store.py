from __future__ import annotations

import hashlib
import json

import pytest

from src.core.artifacts import ArtifactStatus, ArtifactStore
from src.core.message_store import MessageStore
from src.plugin.base import Event


def _record_resource_message(store: MessageStore, message_id: int = 42) -> None:
    segments = [
        {"type": "text", "data": {"text": "附件："}},
        {
            "type": "file",
            "data": {
                "file_id": "qq-file-1",
                "file_name": "notes.txt",
                "file_size": "5",
            },
        },
        {
            "type": "image",
            "data": {"file": "image-key", "url": "https://example.com/a.png"},
        },
    ]
    store.record(
        Event(
            type="message.group",
            raw={"time": 1, "sender": {"nickname": "alice"}},
            user_id=10,
            message_id=message_id,
            message="附件：[CQ:file,file=qq-file-1]",
            group_id=20,
            is_group=True,
            raw_message="附件：[CQ:file,file=qq-file-1]",
            message_array=segments,
            raw_event_json=json.dumps({"message": segments}),
        )
    )


def test_discovery_links_resource_segments_and_is_idempotent(tmp_path):
    messages = MessageStore(db_path=":memory:")
    _record_resource_message(messages)
    artifacts = ArtifactStore(tmp_path, connection=messages.connection)

    first = artifacts.discover_message(42)
    second = artifacts.discover_message(42)

    assert len(first) == 2
    assert [item.artifact_id for item in second] == [item.artifact_id for item in first]
    assert first[0].napcat_file_id == "qq-file-1"
    linked = messages.connection.execute(
        "SELECT artifact_id FROM message_segments WHERE message_id=42 AND segment_index=1"
    ).fetchone()
    assert linked == (first[0].artifact_id,)


def test_content_addressed_import_deduplicates_blobs(tmp_path):
    messages = MessageStore(db_path=":memory:")
    _record_resource_message(messages)
    artifacts = ArtifactStore(tmp_path, connection=messages.connection)
    first, second = artifacts.discover_message(42)

    available_a = artifacts.import_bytes(first.artifact_id, b"hello", file_name="a.txt")
    available_b = artifacts.import_bytes(
        second.artifact_id, b"hello", file_name="b.txt"
    )

    assert available_a.status == ArtifactStatus.AVAILABLE
    assert available_a.sha256 == hashlib.sha256(b"hello").hexdigest()
    assert available_a.local_path == available_b.local_path
    assert (
        tmp_path / available_a.sha256[:2] / available_a.sha256
    ).read_bytes() == b"hello"


def test_import_enforces_size_limit(tmp_path):
    messages = MessageStore(db_path=":memory:")
    _record_resource_message(messages)
    artifacts = ArtifactStore(tmp_path, connection=messages.connection, max_file_size=3)
    artifact = artifacts.discover_message(42)[0]

    with pytest.raises(ValueError, match="max_file_size"):
        artifacts.import_bytes(artifact.artifact_id, b"four")
