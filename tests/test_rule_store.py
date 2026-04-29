from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.models import VendorRule
from app.store.rule_store import RuleStore


def test_rule_store_rejects_rules_with_accounts_outside_tenant_coa(tmp_path: Path) -> None:
    rules_file = tmp_path / "tenant_a_rules.json"
    rules_file.write_text(
        json.dumps(
            [
                {
                    "tenant_id": "tenant_a",
                    "vendor_key": "bad vendor",
                    "coa_account_id": "9999",
                    "created_by": "import",
                    "created_at": "2026-01-01T00:00:00Z",
                    "source_tx_id": None,
                }
            ]
        ),
        encoding="utf-8",
    )

    rules_paths = {"tenant_a": str(rules_file.relative_to(tmp_path))}
    coa_ids_by_tenant = {"tenant_a": {"6100", "6200"}}

    with pytest.raises(ValueError, match="invalid coa_account_id"):
        RuleStore(tmp_path, rules_paths, coa_ids_by_tenant)


def test_rule_store_upsert_persists_and_applies_rule(tmp_path: Path) -> None:
    rules_file = tmp_path / "tenant_a_rules.json"
    rules_file.write_text("[]", encoding="utf-8")
    rules_paths = {"tenant_a": str(rules_file.relative_to(tmp_path))}
    coa_ids_by_tenant = {"tenant_a": {"6100", "6200"}}

    store = RuleStore(tmp_path, rules_paths, coa_ids_by_tenant)
    new_rule = VendorRule(
        tenant_id="tenant_a",
        vendor_key="grab sg 5555",
        coa_account_id="6100",
        created_by="reviewer",
        created_at=datetime.now(timezone.utc),
        source_tx_id="tx_abc",
    )
    getattr(store, "upsert_rule")(new_rule)

    loaded = RuleStore(tmp_path, rules_paths, coa_ids_by_tenant)
    matched = loaded.match("tenant_a", "grab sg 5555")

    assert matched is not None
    assert matched.coa_account_id == "6100"
