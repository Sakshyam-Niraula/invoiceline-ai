from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class Statement:
    """Parameterized SQL statement for batch or single execution."""

    sql: str
    params: tuple[Any, ...] = ()


class Database(Protocol):
    """Async relational database abstraction shared by Cloudflare D1 and local SQLite."""

    async def fetchone(self, sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None: ...

    async def fetchall(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]: ...

    async def execute(self, sql: str, params: Sequence[Any] = ()) -> int: ...

    async def batch(self, statements: Sequence[Statement]) -> None: ...

    async def executescript(self, sql: str) -> None: ...
