from __future__ import annotations

from pathlib import Path


class LocalObjectStorage:
    """Local filesystem private storage adapter for development and testing."""

    def __init__(self, root_dir: str) -> None:
        self._root = Path(root_dir).resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, key: str) -> Path:
        clean = key.lstrip("/").replace("\\", "/")
        target = (self._root / clean).resolve()
        if self._root not in target.parents and target != self._root:
            raise ValueError("Invalid storage key path")
        return target

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        target = self._resolve(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    async def get(self, key: str) -> bytes | None:
        target = self._resolve(key)
        if not target.exists():
            return None
        return target.read_bytes()

    async def delete(self, key: str) -> None:
        target = self._resolve(key)
        if target.exists():
            target.unlink()
