from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from app.db.base import Statement


class SQLiteDatabase:
    """Local SQLite database adapter matching Cloudflare D1 semantics."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON;")
        if db_path != ":memory:":
            self._conn.execute("PRAGMA journal_mode = WAL;")
        self._lock = asyncio.Lock()

    async def fetchone(self, sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
        async with self._lock:
            cur = self._conn.execute(sql, tuple(params))
            row = cur.fetchone()
            return dict(row) if row is not None else None

    async def fetchall(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        async with self._lock:
            cur = self._conn.execute(sql, tuple(params))
            return [dict(row) for row in cur.fetchall()]

    async def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        async with self._lock:
            cur = self._conn.execute(sql, tuple(params))
            self._conn.commit()
            return cur.rowcount

    async def batch(self, statements: Sequence[Statement]) -> None:
        async with self._lock:
            try:
                self._conn.execute("BEGIN")
                for stmt in statements:
                    self._conn.execute(stmt.sql, stmt.params)
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    async def executescript(self, sql: str) -> None:
        async with self._lock:
            self._conn.executescript(sql)
            self._conn.commit()

    def close(self) -> None:
        self._conn.close()
