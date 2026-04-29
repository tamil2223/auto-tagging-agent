from scripts.demo_scenario import run_demo_scenario  # pylint: disable=no-name-in-module


def test_demo_scenario_emits_core_lifecycle_events() -> None:
    lines = run_demo_scenario()
    text = "\n".join(lines)

    assert "AUTO_TAG" in text
    assert "REVIEW_QUEUE" in text
    assert "UNKNOWN" in text
    assert "rule_created=True" in text
