from __future__ import annotations

import threading

from app.models import ReviewQueueItem


class ReviewQueueStore:
    """Stores review queue items in memory for Step 3."""

    def __init__(self) -> None:
        """Initializes an in-memory queue grouped by tenant."""
        self._lock = threading.RLock()
        self._items: dict[str, list[ReviewQueueItem]] = {}

    def add(self, item: ReviewQueueItem) -> None:
        """Adds one review item into the tenant queue.

        Args:
            item: Review queue item.
        """
        with self._lock:
            tenant_items = self._items.setdefault(item.tenant_id, [])
            tenant_items.append(item)

    def resolve(self, tenant_id: str, tx_id: str) -> ReviewQueueItem | None:
        """Removes and returns a queued item by transaction ID.

        Args:
            tenant_id: Tenant identifier.
            tx_id: Transaction identifier.

        Returns:
            The removed queue item, or None if not found.
        """
        with self._lock:
            tenant_items = self._items.get(tenant_id, [])
            for index, item in enumerate(tenant_items):
                if item.tx_id == tx_id:
                    return tenant_items.pop(index)
            return None

    def list_by_tenant(self, tenant_id: str) -> list[ReviewQueueItem]:
        """Lists pending review items for a tenant.

        Args:
            tenant_id: Tenant identifier.

        Returns:
            Queue items for the tenant.
        """
        with self._lock:
            return list(self._items.get(tenant_id, []))
