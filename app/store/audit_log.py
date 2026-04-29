from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import threading

from app.models import TaggingResult


class AuditLogStore:
    """Persists append-only audit records in SQLite."""

    def __init__(self, db_path: Path) -> None:
        """Initializes the audit store and creates table if needed.

        Args:
            db_path: SQLite file path for shared runtime state.
        """
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_db()

    def _init_db(self) -> None:
        """Creates audit table schema."""
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_log_tenant_id ON audit_log(tenant_id)"
            )

    def append(self, result: TaggingResult) -> None:
        """Appends a single immutable audit event to SQLite.

        Args:
            result: Tagging result event to append.
        """
        with self._lock:
            payload = json.dumps(result.model_dump(mode="json"), ensure_ascii=True)
            with sqlite3.connect(self._db_path) as conn:
                conn.execute(
                    "INSERT INTO audit_log (tenant_id, payload_json) VALUES (?, ?)",
                    (result.tenant_id, payload),
                )

    def list_by_tenant(self, tenant_id: str) -> list[TaggingResult]:
        """Returns audit events for one tenant from SQLite.

        Args:
            tenant_id: Tenant identifier.

        Returns:
            Tenant audit events in insertion order.
        """
        with self._lock:
            with sqlite3.connect(self._db_path) as conn:
                rows = conn.execute(
                    "SELECT payload_json FROM audit_log WHERE tenant_id = ? ORDER BY id ASC",
                    (tenant_id,),
                ).fetchall()
            return [TaggingResult(**json.loads(row[0])) for row in rows]
