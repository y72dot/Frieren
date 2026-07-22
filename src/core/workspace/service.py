from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from src.core.artifacts import ArtifactStore


@dataclass(frozen=True)
class WorkspaceEntry:
    path: str
    is_dir: bool
    size: int
    modified_at: int


class WorkspaceService:
    """Single safe filesystem owned by the Bot, rooted below data/."""

    def __init__(
        self,
        root: str | Path,
        *,
        artifact_store: ArtifactStore,
        max_file_size: int = 1_048_576,
        max_read_size: int = 524_288,
    ) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.artifact_store = artifact_store
        self.max_file_size = max_file_size
        self.max_read_size = max_read_size

    def write_text(self, path: str, content: str, *, overwrite: bool = False) -> dict:
        encoded = content.encode("utf-8")
        if len(encoded) > self.max_file_size:
            raise ValueError(f"workspace file exceeds {self.max_file_size} bytes")
        target = self._resolve(path, allow_root=False)
        if target.exists() and not overwrite:
            raise FileExistsError(f"workspace file already exists: {path}")
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(dir=target.parent, prefix=".write-")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(encoded)
            os.replace(temp_name, target)
        except Exception:
            Path(temp_name).unlink(missing_ok=True)
            raise
        return self.stat(path).__dict__

    def read_text(self, path: str) -> dict:
        target = self._resolve(path, allow_root=False)
        if not target.is_file():
            raise FileNotFoundError(path)
        size = target.stat().st_size
        if size > self.max_read_size:
            raise ValueError(f"workspace read exceeds {self.max_read_size} bytes")
        return {"path": self._relative(target), "content": target.read_text(encoding="utf-8")}

    def list(self, path: str = "") -> list[WorkspaceEntry]:
        target = self._resolve(path, allow_root=True)
        if not target.is_dir():
            raise NotADirectoryError(path)
        return [self._entry(item) for item in sorted(target.iterdir(), key=lambda p: p.name)]

    def search(self, query: str, *, limit: int = 20) -> list[dict]:
        needle = query.casefold()
        results: list[dict] = []
        for path in self.root.rglob("*"):
            if len(results) >= limit or not path.is_file() or path.is_symlink():
                continue
            relative = self._relative(path)
            if needle in relative.casefold():
                results.append({"path": relative, "snippet": relative, "modified_at": int(path.stat().st_mtime)})
                continue
            if path.stat().st_size > self.max_read_size:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            index = text.casefold().find(needle)
            if index >= 0:
                start = max(0, index - 80)
                results.append(
                    {
                        "path": relative,
                        "snippet": text[start : index + len(query) + 120],
                        "modified_at": int(path.stat().st_mtime),
                    }
                )
        return results

    def export_artifact(self, path: str):
        target = self._resolve(path, allow_root=False)
        if not target.is_file():
            raise FileNotFoundError(path)
        artifact = self.artifact_store.create_pending(
            kind="file",
            source_type="workspace",
            file_name=target.name,
            metadata={"workspace_path": self._relative(target)},
        )
        return self.artifact_store.import_path(artifact.artifact_id, target)

    def stat(self, path: str) -> WorkspaceEntry:
        target = self._resolve(path, allow_root=False)
        if not target.exists():
            raise FileNotFoundError(path)
        return self._entry(target)

    def _resolve(self, path: str, *, allow_root: bool) -> Path:
        if "\x00" in path:
            raise ValueError("workspace path contains NUL")
        candidate = (self.root / path).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise ValueError("workspace path escapes root")
        if candidate == self.root and not allow_root:
            raise ValueError("workspace root is not a file target")
        return candidate

    def _entry(self, path: Path) -> WorkspaceEntry:
        stat = path.stat()
        return WorkspaceEntry(
            path=self._relative(path),
            is_dir=path.is_dir(),
            size=0 if path.is_dir() else stat.st_size,
            modified_at=int(stat.st_mtime),
        )

    def _relative(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()
