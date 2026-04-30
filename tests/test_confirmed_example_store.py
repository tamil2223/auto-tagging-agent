from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from app.store.confirmed_example_store import ConfirmedExampleStore


def _unique_db_path() -> Path:
    """Builds a unique sqlite path under the workspace for isolated tests."""
    return Path(__file__).resolve().parents[1] / "data" / "runtime" / f"test_confirmed_examples_{uuid4().hex}.db"


def test_confirmed_example_store_samples_deterministically_by_tx_id() -> None:
    store = ConfirmedExampleStore(_unique_db_path())
    tenant_id = "tenant_a"

    store.add_example(tenant_id, "vendor_a", {"vendor_key": "vendor_a", "coa_account_id": "6100"})
    store.add_example(tenant_id, "vendor_b", {"vendor_key": "vendor_b", "coa_account_id": "6200"})
    store.add_example(tenant_id, "vendor_c", {"vendor_key": "vendor_c", "coa_account_id": "7200"})

    first = store.sample_examples(tenant_id, exclude_vendor_key="vendor_b", tx_id="tx_stable_1", limit=2)
    second = store.sample_examples(tenant_id, exclude_vendor_key="vendor_b", tx_id="tx_stable_1", limit=2)

    assert first == second
    assert all(example["vendor_key"] != "vendor_b" for example in first)


def test_confirmed_example_store_respects_sample_limit() -> None:
    store = ConfirmedExampleStore(_unique_db_path())
    tenant_id = "tenant_a"

    for index in range(10):
        vendor_key = f"vendor_{index:03d}"
        store.add_example(tenant_id, vendor_key, {"vendor_key": vendor_key, "coa_account_id": "6100"})

    samples = store.sample_examples(tenant_id, exclude_vendor_key=None, tx_id="tx_limit", limit=5)
    assert len(samples) == 5
