from __future__ import annotations

import json
from pathlib import Path
import threading

from app.models import VendorRule
from app.pipeline.rule_engine import build_rule_index, match_vendor_rule


class RuleStore:
    """Loads and serves deterministic vendor rules by tenant."""

    def __init__(
        self,
        repo_root: Path,
        rules_paths: dict[str, str],
        coa_ids_by_tenant: dict[str, set[str]],
    ) -> None:
        """Initializes rule indexes and validates CoA references.

        Args:
            repo_root: Repository root path.
            rules_paths: Mapping of tenant IDs to relative rules file paths.
            coa_ids_by_tenant: Valid CoA account IDs keyed by tenant.

        Raises:
            ValueError: If a rule references a CoA account not defined for the tenant.
        """
        self._lock = threading.RLock()
        self._coa_ids_by_tenant = coa_ids_by_tenant
        self._runtime_root = repo_root / "data" / "runtime" / "rules"
        self._runtime_root.mkdir(parents=True, exist_ok=True)
        self._runtime_rules_by_tenant: dict[str, dict[str, VendorRule]] = {}
        self._base_rules_by_tenant: dict[str, dict[str, VendorRule]] = {}
        self._rules_by_tenant: dict[str, dict[str, VendorRule]] = {}
        for tenant_id, relative_path in rules_paths.items():
            file_path = repo_root / relative_path
            payload = json.loads(file_path.read_text(encoding="utf-8"))
            rules = [VendorRule(**item) for item in payload]
            valid_coa_ids = coa_ids_by_tenant.get(tenant_id, set())
            invalid_rule = next(
                (rule for rule in rules if rule.coa_account_id not in valid_coa_ids),
                None,
            )
            if invalid_rule:
                raise ValueError(
                    "invalid coa_account_id in rule store "
                    f"tenant={tenant_id} vendor_key={invalid_rule.vendor_key} "
                    f"coa_account_id={invalid_rule.coa_account_id}"
                )
            base_index = build_rule_index(rules)
            self._base_rules_by_tenant[tenant_id] = base_index
            runtime_index = self._load_runtime_rules(tenant_id)
            self._runtime_rules_by_tenant[tenant_id] = runtime_index
            merged = dict(base_index)
            merged.update(runtime_index)
            self._rules_by_tenant[tenant_id] = merged

    def _load_runtime_rules(self, tenant_id: str) -> dict[str, VendorRule]:
        """Loads runtime-promoted rules for one tenant.

        Args:
            tenant_id: Tenant identifier.

        Returns:
            Runtime rule index keyed by normalized vendor key.
        """
        file_path = self._runtime_root / f"{tenant_id}.json"
        if not file_path.exists():
            return {}
        payload = json.loads(file_path.read_text(encoding="utf-8"))
        rules = [VendorRule(**item) for item in payload]
        valid_coa_ids = self._coa_ids_by_tenant.get(tenant_id, set())
        invalid_rule = next((rule for rule in rules if rule.coa_account_id not in valid_coa_ids), None)
        if invalid_rule:
            raise ValueError(
                "invalid coa_account_id in runtime rule store "
                f"tenant={tenant_id} vendor_key={invalid_rule.vendor_key} "
                f"coa_account_id={invalid_rule.coa_account_id}"
            )
        return build_rule_index(rules)

    def _persist_runtime_rules(self, tenant_id: str) -> None:
        """Persists runtime rules for one tenant.

        Args:
            tenant_id: Tenant identifier.
        """
        file_path = self._runtime_root / f"{tenant_id}.json"
        rules = list(self._runtime_rules_by_tenant.get(tenant_id, {}).values())
        file_path.write_text(
            json.dumps([rule.model_dump(mode="json") for rule in rules], ensure_ascii=True, indent=2),
            encoding="utf-8",
        )

    def match(self, tenant_id: str, vendor_key: str) -> VendorRule | None:
        """Looks up an exact vendor-key rule for a tenant.

        Args:
            tenant_id: Tenant identifier.
            vendor_key: Normalized vendor key.

        Returns:
            A matching vendor rule or None when no match exists.
        """
        with self._lock:
            tenant_rules = self._rules_by_tenant.get(tenant_id, {})
            return match_vendor_rule(tenant_rules, vendor_key)

    def list_rules(self, tenant_id: str) -> list[VendorRule]:
        """Lists all rules for one tenant.

        Args:
            tenant_id: Tenant identifier.

        Returns:
            Deterministic vendor rules for the tenant.
        """
        with self._lock:
            return list(self._rules_by_tenant.get(tenant_id, {}).values())

    def upsert_rule(self, rule: VendorRule) -> None:
        """Creates or updates a runtime-promoted rule.

        Args:
            rule: Rule to write.

        Raises:
            ValueError: If the rule references an account outside tenant CoA.
        """
        valid_coa_ids = self._coa_ids_by_tenant.get(rule.tenant_id, set())
        if rule.coa_account_id not in valid_coa_ids:
            raise ValueError(
                "invalid coa_account_id in runtime upsert "
                f"tenant={rule.tenant_id} vendor_key={rule.vendor_key} "
                f"coa_account_id={rule.coa_account_id}"
            )
        with self._lock:
            runtime_index = self._runtime_rules_by_tenant.setdefault(rule.tenant_id, {})
            runtime_index[rule.vendor_key] = rule
            merged = dict(self._base_rules_by_tenant.get(rule.tenant_id, {}))
            merged.update(runtime_index)
            self._rules_by_tenant[rule.tenant_id] = merged
            self._persist_runtime_rules(rule.tenant_id)
