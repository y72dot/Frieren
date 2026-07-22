from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class SearchHit:
    source_type: str
    source_id: str
    title: str
    snippet: str
    timestamp: int | float | None
    reference: str
    coverage: str = "local"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SearchService:
    """Unified traceable search over the Bot's durable local state."""

    def __init__(self, bot: Any) -> None:
        self.bot = bot

    def search(self, domain: str, query: str, *, limit: int = 20, **filters: Any) -> dict:
        method = getattr(self, f"search_{domain}", None)
        if method is None:
            raise ValueError(f"unsupported search domain: {domain}")
        hits = method(query, limit=limit, **filters)
        return {
            "domain": domain,
            "query": query,
            "coverage": "local",
            "count": len(hits),
            "hits": [hit.to_dict() for hit in hits],
        }

    def search_messages(
        self,
        query: str,
        *,
        limit: int = 20,
        conversation_type: str | None = None,
        conversation_id: int | None = None,
        **_: Any,
    ) -> list[SearchHit]:
        messages = self.bot.msg_store.query(
            keyword=query,
            conversation_type=conversation_type,
            conversation_id=conversation_id,
            n=limit,
        )
        return [
            SearchHit(
                source_type="message",
                source_id=str(item.message_id),
                title=f"{item.nickname} ({item.user_id})",
                snippet=item.content[:500],
                timestamp=item.time,
                reference=f"message:{item.message_id}",
                metadata={"group_id": item.group_id, "user_id": item.user_id},
            )
            for item in reversed(messages)
        ]

    def search_artifacts(self, query: str, *, limit: int = 20, **_: Any) -> list[SearchHit]:
        rows = self.bot.msg_store.connection.execute(
            """SELECT artifact_id, file_name, source_type, created_at, metadata_json,
                      status, mime_type FROM artifacts
               WHERE file_name LIKE ? OR metadata_json LIKE ? OR remote_url LIKE ?
               ORDER BY created_at DESC LIMIT ?""",
            (f"%{query}%", f"%{query}%", f"%{query}%", limit),
        ).fetchall()
        return [
            SearchHit(
                source_type="artifact",
                source_id=row[0],
                title=row[1] or row[0],
                snippet=(row[1] or "") + " " + row[4][:300],
                timestamp=row[3],
                reference=f"artifact:{row[0]}",
                metadata={"source_type": row[2], "status": row[5], "mime_type": row[6]},
            )
            for row in rows
        ]

    def search_workspace(self, query: str, *, limit: int = 20, **_: Any) -> list[SearchHit]:
        return [
            SearchHit(
                source_type="workspace",
                source_id=item["path"],
                title=item["path"],
                snippet=item["snippet"][:500],
                timestamp=item["modified_at"],
                reference=f"workspace:{item['path']}",
            )
            for item in self.bot.workspace.search(query, limit=limit)
        ]

    def search_tasks(self, query: str, *, limit: int = 20, **_: Any) -> list[SearchHit]:
        rows = self.bot.msg_store.connection.execute(
            """SELECT task_id, goal, status, trigger_type, updated_at, metadata_json
               FROM tasks WHERE goal LIKE ? OR metadata_json LIKE ? OR error LIKE ?
               ORDER BY updated_at DESC LIMIT ?""",
            (f"%{query}%", f"%{query}%", f"%{query}%", limit),
        ).fetchall()
        return [
            SearchHit(
                source_type="task",
                source_id=row[0],
                title=row[1],
                snippet=f"{row[2]} · {row[3]} · {row[5][:300]}",
                timestamp=row[4],
                reference=f"task:{row[0]}",
                metadata={"status": row[2], "trigger_type": row[3]},
            )
            for row in rows
        ]

    def search_memory(self, query: str, *, limit: int = 20, **_: Any) -> list[SearchHit]:
        manager = getattr(self.bot, "memory_mgr", None)
        if manager is None:
            return []
        hits: list[SearchHit] = []
        for episode in manager.search_episodes(keyword=query, limit=limit):
            hits.append(
                SearchHit(
                    source_type="memory.episode",
                    source_id=f"{episode['session_key']}:{episode['timestamp']}",
                    title=episode["session_key"],
                    snippet=episode["summary"][:500],
                    timestamp=episode["timestamp"],
                    reference=f"memory:episode:{episode['session_key']}:{episode['timestamp']}",
                    metadata=episode["metadata"],
                )
            )
        for fact in manager.search_facts(query, limit=max(0, limit - len(hits))):
            hits.append(
                SearchHit(
                    source_type="memory.fact",
                    source_id=f"{fact['subject']}:{fact['predicate']}",
                    title=fact["subject"],
                    snippet=f"{fact['predicate']}: {fact['object']}",
                    timestamp=None,
                    reference=f"memory:fact:{fact['subject']}:{fact['predicate']}",
                    metadata={"confidence": fact["confidence"], "source": fact["source"]},
                )
            )
        return hits[:limit]
