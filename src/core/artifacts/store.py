from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import os
import sqlite3
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any


class ArtifactStatus(StrEnum):
    DISCOVERED = "discovered"
    MATERIALIZING = "materializing"
    AVAILABLE = "available"
    FAILED = "failed"
    EXPIRED = "expired"
    DELETED = "deleted"


@dataclass(frozen=True)
class Artifact:
    artifact_id: str
    kind: str
    file_name: str | None
    mime_type: str | None
    size: int | None
    sha256: str | None
    local_path: str | None
    status: str
    source_type: str
    source_message_id: int | None
    source_segment_index: int | None
    napcat_file_id: str | None
    remote_url: str | None
    created_at: int
    discovered_at: int
    downloaded_at: int | None
    last_accessed_at: int | None
    error: str | None
    metadata_json: str

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["metadata"] = json.loads(self.metadata_json or "{}")
        del value["metadata_json"]
        return value


_RESOURCE_TYPES = {"image", "record", "video", "file", "online_file", "mface"}


class ArtifactStore:
    """Artifact metadata and immutable content-addressed blobs.

    The metadata tables live in the message database. Discovery never fetches
    network content; materialization is an explicit later operation.
    """

    _COLUMNS = (
        "artifact_id, kind, file_name, mime_type, size, sha256, local_path, "
        "status, source_type, source_message_id, source_segment_index, "
        "napcat_file_id, remote_url, created_at, discovered_at, downloaded_at, "
        "last_accessed_at, error, metadata_json"
    )

    def __init__(
        self,
        root_dir: str | Path = "data/artifacts",
        *,
        connection: sqlite3.Connection | None = None,
        db_path: str = "data/messages.db",
        max_file_size: int = 104_857_600,
    ) -> None:
        self.root_dir = Path(root_dir).resolve()
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.max_file_size = max_file_size
        self._owns_connection = connection is None
        if connection is None:
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(db_path, check_same_thread=False)
        self._conn = connection
        self._create_tables()

    def _create_tables(self) -> None:
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS artifacts (
                artifact_id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                file_name TEXT,
                mime_type TEXT,
                size INTEGER,
                sha256 TEXT,
                local_path TEXT,
                status TEXT NOT NULL,
                source_type TEXT NOT NULL,
                source_message_id INTEGER,
                source_segment_index INTEGER,
                napcat_file_id TEXT,
                remote_url TEXT,
                created_at INTEGER NOT NULL,
                discovered_at INTEGER NOT NULL,
                downloaded_at INTEGER,
                last_accessed_at INTEGER,
                error TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                UNIQUE(source_message_id, source_segment_index)
            )"""
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_artifacts_message "
            "ON artifacts(source_message_id, source_segment_index)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_artifacts_sha ON artifacts(sha256)"
        )
        self._conn.commit()

    @property
    def connection(self) -> sqlite3.Connection:
        return self._conn

    def discover_message(self, message_id: int) -> list[Artifact]:
        rows = self._conn.execute(
            "SELECT segment_index, segment_type, raw_segment_json "
            "FROM message_segments WHERE message_id=? ORDER BY segment_index",
            (message_id,),
        ).fetchall()
        found: list[Artifact] = []
        for index, segment_type, raw_json in rows:
            if segment_type not in _RESOURCE_TYPES:
                continue
            try:
                segment = json.loads(raw_json)
            except (TypeError, json.JSONDecodeError):
                segment = {}
            data = segment.get("data", {}) if isinstance(segment, dict) else {}
            if not isinstance(data, dict):
                data = {}
            found.append(self.discover(message_id, int(index), str(segment_type), data))
        return found

    def discover(
        self,
        message_id: int,
        segment_index: int,
        kind: str,
        metadata: dict[str, Any],
    ) -> Artifact:
        existing = self._conn.execute(
            f"SELECT {self._COLUMNS} FROM artifacts "
            "WHERE source_message_id=? AND source_segment_index=?",
            (message_id, segment_index),
        ).fetchone()
        if existing:
            return Artifact(*existing)
        artifact_id = uuid.uuid4().hex
        now = int(time.time())
        file_id = _first_string(metadata, "file_id", "file", "id")
        file_name = _first_string(metadata, "file_name", "name", "filename")
        remote_url = _first_string(metadata, "url")
        size = _to_int(metadata.get("file_size", metadata.get("size")))
        mime_type = _first_string(metadata, "mime_type", "mime")
        if mime_type is None and file_name:
            mime_type = mimetypes.guess_type(file_name)[0]
        self._conn.execute(
            """INSERT INTO artifacts (
                artifact_id, kind, file_name, mime_type, size, sha256,
                local_path, status, source_type, source_message_id,
                source_segment_index, napcat_file_id, remote_url, created_at,
                discovered_at, downloaded_at, last_accessed_at, error,
                metadata_json
            ) VALUES (?, ?, ?, ?, ?, NULL, NULL, ?, 'qq_message', ?, ?, ?, ?,
                      ?, ?, NULL, NULL, NULL, ?)""",
            (
                artifact_id,
                kind,
                file_name,
                mime_type,
                size,
                ArtifactStatus.DISCOVERED,
                message_id,
                segment_index,
                file_id,
                remote_url,
                now,
                now,
                json.dumps(metadata, ensure_ascii=False, separators=(",", ":")),
            ),
        )
        self._conn.execute(
            "UPDATE message_segments SET artifact_id=? "
            "WHERE message_id=? AND segment_index=?",
            (artifact_id, message_id, segment_index),
        )
        self._conn.commit()
        result = self.get(artifact_id)
        assert result is not None
        return result

    def create_pending(
        self,
        *,
        kind: str,
        source_type: str,
        file_name: str | None = None,
        remote_url: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Artifact:
        """Create an Artifact entry for non-QQ sources before importing bytes."""
        artifact_id = uuid.uuid4().hex
        now = int(time.time())
        mime_type = mimetypes.guess_type(file_name or "")[0]
        self._conn.execute(
            """INSERT INTO artifacts (
                   artifact_id, kind, file_name, mime_type, size, sha256,
                   local_path, status, source_type, source_message_id,
                   source_segment_index, napcat_file_id, remote_url, created_at,
                   discovered_at, downloaded_at, last_accessed_at, error,
                   metadata_json
               ) VALUES (?, ?, ?, ?, NULL, NULL, NULL, ?, ?, NULL, NULL,
                         NULL, ?, ?, ?, NULL, NULL, NULL, ?)""",
            (
                artifact_id,
                kind,
                file_name,
                mime_type,
                ArtifactStatus.DISCOVERED,
                source_type,
                remote_url,
                now,
                now,
                json.dumps(metadata or {}, ensure_ascii=False, separators=(",", ":")),
            ),
        )
        self._conn.commit()
        result = self.get(artifact_id)
        assert result is not None
        return result

    def get(self, artifact_id: str, *, touch: bool = False) -> Artifact | None:
        if touch:
            self._conn.execute(
                "UPDATE artifacts SET last_accessed_at=? WHERE artifact_id=?",
                (int(time.time()), artifact_id),
            )
            self._conn.commit()
        row = self._conn.execute(
            f"SELECT {self._COLUMNS} FROM artifacts WHERE artifact_id=?",
            (artifact_id,),
        ).fetchone()
        return Artifact(*row) if row else None

    def list_for_message(self, message_id: int) -> list[Artifact]:
        rows = self._conn.execute(
            f"SELECT {self._COLUMNS} FROM artifacts "
            "WHERE source_message_id=? ORDER BY source_segment_index",
            (message_id,),
        ).fetchall()
        return [Artifact(*row) for row in rows]

    def set_materializing(self, artifact_id: str) -> None:
        self._set_status(artifact_id, ArtifactStatus.MATERIALIZING, None)

    def fail(self, artifact_id: str, error: str) -> None:
        self._set_status(artifact_id, ArtifactStatus.FAILED, error[:2000])

    def _set_status(
        self, artifact_id: str, status: ArtifactStatus, error: str | None
    ) -> None:
        self._conn.execute(
            "UPDATE artifacts SET status=?, error=? WHERE artifact_id=?",
            (status, error, artifact_id),
        )
        self._conn.commit()

    def import_path(
        self, artifact_id: str, source: str | Path, *, file_name: str | None = None
    ) -> Artifact:
        source_path = Path(source)
        with source_path.open("rb") as handle:
            return self.import_stream(
                artifact_id, handle, file_name=file_name or source_path.name
            )

    def import_base64(
        self, artifact_id: str, encoded: str, *, file_name: str | None = None
    ) -> Artifact:
        try:
            content = base64.b64decode(encoded, validate=True)
        except ValueError as exc:
            raise ValueError("invalid base64 artifact payload") from exc
        return self.import_bytes(artifact_id, content, file_name=file_name)

    def import_bytes(
        self, artifact_id: str, content: bytes, *, file_name: str | None = None
    ) -> Artifact:
        from io import BytesIO

        return self.import_stream(artifact_id, BytesIO(content), file_name=file_name)

    def import_stream(
        self, artifact_id: str, stream: Any, *, file_name: str | None = None
    ) -> Artifact:
        digest = hashlib.sha256()
        total = 0
        temp_dir = self.root_dir / ".tmp"
        temp_dir.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(dir=temp_dir)
        try:
            with os.fdopen(fd, "wb") as output:
                while chunk := stream.read(1024 * 1024):
                    total += len(chunk)
                    if total > self.max_file_size:
                        raise ValueError(
                            f"artifact exceeds max_file_size={self.max_file_size}"
                        )
                    digest.update(chunk)
                    output.write(chunk)
            sha256 = digest.hexdigest()
            target_dir = self.root_dir / sha256[:2]
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / sha256
            if target.exists():
                Path(temp_name).unlink()
            else:
                os.replace(temp_name, target)
            now = int(time.time())
            mime_type = mimetypes.guess_type(file_name or "")[0]
            self._conn.execute(
                """UPDATE artifacts SET status=?, file_name=COALESCE(?, file_name),
                   mime_type=COALESCE(?, mime_type), size=?, sha256=?, local_path=?,
                   downloaded_at=?, last_accessed_at=?, error=NULL WHERE artifact_id=?""",
                (
                    ArtifactStatus.AVAILABLE,
                    file_name,
                    mime_type,
                    total,
                    sha256,
                    str(target),
                    now,
                    now,
                    artifact_id,
                ),
            )
            self._conn.commit()
        except Exception:
            Path(temp_name).unlink(missing_ok=True)
            raise
        result = self.get(artifact_id)
        assert result is not None
        return result

    def close(self) -> None:
        if self._owns_connection:
            self._conn.close()


def _first_string(data: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = data.get(key)
        if value is not None and str(value):
            return str(value)
    return None


def _to_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
