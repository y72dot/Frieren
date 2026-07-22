from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest

from src.core.artifacts import ArtifactStore
from src.core.llm.memory_manager import MemoryManager
from src.core.message_store import MessageStore
from src.core.runtime import DurableRuntime, RuntimeStore
from src.core.search import SearchService
from src.core.workspace import WorkspaceService


def test_workspace_is_rooted_atomic_searchable_and_exportable(tmp_path):
    connection = sqlite3.connect(":memory:")
    artifacts = ArtifactStore(tmp_path / "artifacts", connection=connection)
    workspace = WorkspaceService(tmp_path / "workspace", artifact_store=artifacts)

    created = workspace.write_text("notes/plan.md", "alpha durable plan")
    assert created["path"] == "notes/plan.md"
    assert workspace.read_text("notes/plan.md")["content"] == "alpha durable plan"
    assert workspace.search("durable")[0]["path"] == "notes/plan.md"
    with pytest.raises(FileExistsError):
        workspace.write_text("notes/plan.md", "replace")
    workspace.write_text("notes/plan.md", "replacement", overwrite=True)
    with pytest.raises(ValueError, match="escapes root"):
        workspace.read_text("../secret.txt")

    artifact = workspace.export_artifact("notes/plan.md")
    assert artifact.status == "available"
    assert artifact.source_type == "workspace"
    assert artifact.sha256


def test_workspace_rejects_symlink_escape(tmp_path):
    connection = sqlite3.connect(":memory:")
    artifacts = ArtifactStore(tmp_path / "artifacts", connection=connection)
    workspace = WorkspaceService(tmp_path / "workspace", artifact_store=artifacts)
    outside = tmp_path / "outside"
    outside.mkdir()
    link = workspace.root / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are unavailable on this platform")
    with pytest.raises(ValueError, match="escapes root"):
        workspace.write_text("link/file.txt", "no")


def test_unified_search_returns_traceable_hits_across_domains(tmp_path):
    messages = MessageStore(db_path=":memory:")
    messages.record_bot_message(1, 99, 7, "Alice", "durable runtime", 123, True)
    artifacts = ArtifactStore(tmp_path / "artifacts", connection=messages.connection)
    workspace = WorkspaceService(tmp_path / "workspace", artifact_store=artifacts)
    workspace.write_text("research.txt", "durable workspace evidence")
    pending = artifacts.create_pending(
        kind="file", source_type="workspace", file_name="durable-report.txt"
    )
    artifacts.import_bytes(pending.artifact_id, b"report", file_name="durable-report.txt")
    runtime = DurableRuntime(RuntimeStore(messages.connection))
    runtime.create_task_run(
        goal="durable task", trigger_type="manual", template={"kind": "test"}
    )
    memory = MemoryManager(db_path=str(tmp_path / "memory.db"))
    memory.init_db()
    memory.store_episode("group:99", "durable episode")
    memory.store_fact("runtime", "quality", "durable", source="test")
    bot = SimpleNamespace(
        msg_store=messages,
        workspace=workspace,
        memory_mgr=memory,
    )
    search = SearchService(bot)

    for domain in ("messages", "artifacts", "workspace", "tasks", "memory"):
        result = search.search(domain, "durable", limit=10)
        assert result["count"] >= 1
        assert result["coverage"] == "local"
        assert result["hits"][0]["source_id"]
        assert result["hits"][0]["reference"].startswith(
            {"messages": "message:", "artifacts": "artifact:", "workspace": "workspace:", "tasks": "task:", "memory": "memory:"}[domain]
        )
    memory.close()
