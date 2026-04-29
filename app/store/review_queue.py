from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import threading

from app.models import ReviewQueueItem


class ReviewQueueStore:
    """Stores review queue items in SQLite."""

    def __init__(self, db_path: Path) -> None:
        """Initializes SQLite-backed review queue storage."""
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_db()

    def _init_db(self) -> None:
        """Creates review queue table schema."""
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS review_queue (
                    tenant_id TEXT NOT NULL,
                    tx_id TEXT NOT NULL,
                    item_json TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, tx_id)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_review_queue_tenant_id ON review_queue(tenant_id)"
            )

    def add(self, item: ReviewQueueItem) -> None:
        """Adds one review item into the tenant queue.

        Args:
            item: Review queue item.
        """
        with self._lock:
            with sqlite3.connect(self._db_path) as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO review_queue (tenant_id, tx_id, item_json)
                    VALUES (?, ?, ?)
                    """,
                    (
                        item.tenant_id,
                        item.tx_id,
                        json.dumps(item.model_dump(mode="json"), ensure_ascii=True),
                    ),
                )

    def resolve(self, tenant_id: str, tx_id: str) -> ReviewQueueItem | None:
        """Removes and returns a queued item by transaction ID.

        Args:
            tenant_id: Tenant identifier.
            tx_id: Transaction identifier.

        Returns:
            The removed queue item, or None if not found.
        """
        with self._lock:
            with sqlite3.connect(self._db_path) as conn:
                row = conn.execute(
                    """
                    SELECT item_json
                    FROM review_queue
                    WHERE tenant_id = ? AND tx_id = ?
                    """,
                    (tenant_id, tx_id),
                ).fetchone()
                if row is None:
                    return None
                conn.execute(
                    "DELETE FROM review_queue WHERE tenant_id = ? AND tx_id = ?",
                    (tenant_id, tx_id),
                )
            return ReviewQueueItem(**json.loads(row[0]))

    def list_by_tenant(self, tenant_id: str) -> list[ReviewQueueItem]:
        """Lists pending review items for a tenant.

        Args:
            tenant_id: Tenant identifier.

        Returns:
            Queue items for the tenant.
        """
        with self._lock:
            with sqlite3.connect(self._db_path) as conn:
                rows = conn.execute(
                    """
                    SELECT item_json
                    FROM review_queue
                    WHERE tenant_id = ?
                    ORDER BY tx_id ASC
                    """,
                    (tenant_id,),
                ).fetchall()
            return [ReviewQueueItem(**json.loads(row[0])) for row in rows]
