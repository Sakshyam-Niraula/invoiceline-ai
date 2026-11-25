from __future__ import annotations

import time

from app.core.errors import RateLimitError
from app.db.base import Database


async def enforce_rate_limit(
    db: Database,
    bucket_key: str,
    limit_per_minute: int,
) -> None:
    """Enforce fixed-window per-minute rate limit in D1/SQLite."""
    if limit_per_minute <= 0:
        return

    now = int(time.time())
    window_start = now - (now % 60)

    row = await db.fetchone(
        "SELECT window_start, count FROM rate_limits WHERE bucket_key = ?",
        (bucket_key,),
    )
    if row is None or int(row["window_start"]) != window_start:
        await db.execute(
            """
            INSERT INTO rate_limits (bucket_key, window_start, count)
            VALUES (?, ?, 1)
            ON CONFLICT(bucket_key) DO UPDATE SET window_start = excluded.window_start, count = 1
            """,
            (bucket_key, window_start),
        )
        return

    current_count = int(row["count"])
    if current_count >= limit_per_minute:
        retry_after = max(1, (window_start + 60) - now)
        raise RateLimitError(retry_after)

    await db.execute(
        "UPDATE rate_limits SET count = count + 1 WHERE bucket_key = ? AND window_start = ?",
        (bucket_key, window_start),
    )
