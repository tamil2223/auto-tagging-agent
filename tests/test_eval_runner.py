from pathlib import Path

from tests.eval.eval_runner import run_eval  # pylint: disable=no-name-in-module


def test_eval_runner_returns_metrics_dictionary() -> None:
    fixture_path = Path(__file__).resolve().parents[1] / "tests" / "eval" / "fixtures" / "edge_cases.json"
    metrics = run_eval(tenant_id="tenant_a", fixture_path=fixture_path)

    assert metrics["total_fixtures"] >= 1
    assert "auto_tag_precision" in metrics
    assert "long_tail_unknown_rate" in metrics
    assert "review_rate" in metrics
    assert "brier_score" in metrics
    assert "rule_coverage" in metrics
