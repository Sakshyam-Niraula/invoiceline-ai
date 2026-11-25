from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from app.db.base import Statement


def _to_py(value: Any) -> Any:
    """Convert Pyodide JsProxy values cleanly into native Python structures."""
    if value is None:
        return None
    to_py = getattr(value, "to_py", None)
    if callable(to_py):
        return to_py()
    return value


class D1Database:
    """Cloudflare D1 binding adapter for Python Workers."""

    def __init__(self, binding: Any) -> None:
        self._db = binding

    async def fetchone(self, sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
        stmt = self._db.prepare(sql)
        if params:
            stmt = stmt.bind(*params)
        row = await stmt.first()
        if row is None:
            return None
        converted = _to_py(row)
        return dict(converted) if converted is not None else None

    async def fetchall(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        stmt = self._db.prepare(sql)
        if params:
            stmt = stmt.bind(*params)
        res = await stmt.all()
        converted = _to_py(res)
        if isinstance(converted, dict):
            return [dict(r) for r in converted.get("results", [])]
        results = getattr(res, "results", None)
        if results is None:
            return []
        return [dict(_to_py(r)) for r in results]

    async def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        stmt = self._db.prepare(sql)
        if params:
            stmt = stmt.bind(*params)
        res = await stmt.run()
        converted = _to_py(res)
        if isinstance(converted, dict):
            meta = converted.get("meta") or {}
            return int(meta.get("changes", 0))
        return 0

    async def batch(self, statements: Sequence[Statement]) -> None:
        if not statements:
            return
        prepared = []
        for s in statements:
            stmt = self._db.prepare(s.sql)
            if s.params:
                stmt = stmt.bind(*s.params)
            prepared.append(stmt)
        await self._db.batch(prepared)

    async def executescript(self, sql: str) -> None:
        await self._db.exec(sql)
