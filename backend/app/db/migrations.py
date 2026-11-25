from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from app.db.base import Database

DEFAULT_MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


async def apply_migrations(db: Database, migrations_dir: Path = DEFAULT_MIGRATIONS_DIR) -> list[str]:
    """Apply pending SQL migrations in deterministic filename order."""
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS _migrations (
            name TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
        """
    )
    applied_rows = await db.fetchall("SELECT name FROM _migrations")
    applied = {str(r["name"]) for r in applied_rows}

    newly_applied: list[str] = []
    for path in sorted(migrations_dir.glob("*.sql")):
        if path.name in applied:
            continue
        sql = path.read_text(encoding="utf-8")
        await db.executescript(sql)
        await db.execute(
            "INSERT INTO _migrations (name, applied_at) VALUES (?, ?)",
            (path.name, datetime.now(UTC).isoformat()),
        )
        newly_applied.append(path.name)
    return newly_applied
