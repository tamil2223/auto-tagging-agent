from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from uuid import uuid4

from fastapi.testclient import TestClient


def _ensure_project_root_on_path() -> None:
    """Adds repository root to sys.path for direct script execution."""
    project_root = Path(__file__).resolve().parents[2]
    project_root_str = str(project_root)
    if project_root_str not in sys.path:
        sys.path.insert(0, project_root_str)


_ensure_project_root_on_path()

from app.main import app


def _safe_percent(numerator: int, denominator: int) -> float:
    """Returns percentage value in range [0,100] with safe zero-denominator handling."""
    if denominator == 0:
        return 0.0
    return (numerator / denominator) * 100.0


def run_eval(tenant_id: str, fixture_path: Path) -> dict[str, float]:
    """Runs fixture-based evaluation and returns computed metrics."""
    raw_fixtures = json.loads(fixture_path.read_text(encoding="utf-8"))
    fixtures = [item for item in raw_fixtures if item.get("tenant_id") == tenant_id]
    total = len(fixtures)

    auto_tag_total = 0
    auto_tag_correct = 0
    long_tail_total = 0
    long_tail_safe = 0
    review_total = 0
    rule_total = 0
    brier_sum = 0.0
    brier_count = 0

    tenant_headers = {
        "tenant_a": {"X-API-Key": "demo_key_tenant_a"},
        "tenant_b": {"X-API-Key": "demo_key_tenant_b"},
    }
    headers = tenant_headers.get(tenant_id, {})

    run_id = uuid4().hex[:8]
    with TestClient(app) as client:
        for index, item in enumerate(fixtures):
            payload = {
                "tx_id": f"{item['tx_id']}_{run_id}_{index}",
                "tenant_id": item["tenant_id"],
                "vendor_raw": item["vendor_raw"],
                "amount": str(item["amount"]),
                "currency": item["currency"],
                "date": "2026-04-30",
                "transaction_type": "card",
                "ocr_text": None,
                "idempotency_key": f"eval_{run_id}_{index}",
            }
            response = client.post("/transactions/tag", json=payload, headers=headers)
            result = response.json()
            status = result["status"]
            source = result["source"]
            expected_status = item["expected_status"]
            expected_coa = item.get("expected_coa_account_id")
            actual_coa = result.get("coa_account_id")

            if status == "AUTO_TAG":
                auto_tag_total += 1
                if expected_coa is not None and actual_coa == expected_coa:
                    auto_tag_correct += 1

            if item.get("difficulty") == "long-tail":
                long_tail_total += 1
                if status in {"REVIEW_QUEUE", "UNKNOWN"}:
                    long_tail_safe += 1

            if status == "REVIEW_QUEUE":
                review_total += 1

            if source == "rule":
                rule_total += 1

            confidence = result.get("confidence")
            if isinstance(confidence, (float, int)) and expected_coa is not None:
                is_correct = 1.0 if (status == expected_status and actual_coa == expected_coa) else 0.0
                brier_sum += (float(confidence) - is_correct) ** 2
                brier_count += 1

    metrics = {
        "total_fixtures": float(total),
        "auto_tag_precision": _safe_percent(auto_tag_correct, auto_tag_total),
        "long_tail_unknown_rate": _safe_percent(long_tail_safe, long_tail_total),
        "review_rate": _safe_percent(review_total, total),
        "brier_score": (brier_sum / brier_count) if brier_count else 0.0,
        "rule_coverage": _safe_percent(rule_total, total),
    }
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser(description="Run offline eval harness for transaction tagging.")
    parser.add_argument("--tenant", required=True, help="Tenant id")
    parser.add_argument("--fixture", required=True, help="Path to fixture json")
    args = parser.parse_args()

    metrics = run_eval(args.tenant, Path(args.fixture))
    print(f"Eval results - {args.tenant}")
    print(f"  Total fixtures:          {int(metrics['total_fixtures'])}")
    print(f"  Auto-tag precision:      {metrics['auto_tag_precision']:.1f}%")
    print(f"  Long-tail UNKNOWN rate:  {metrics['long_tail_unknown_rate']:.1f}%")
    print(f"  Review rate:             {metrics['review_rate']:.1f}%")
    print(f"  Brier score:             {metrics['brier_score']:.3f}")
    print(f"  Rule coverage:           {metrics['rule_coverage']:.1f}%")


if __name__ == "__main__":
    main()
