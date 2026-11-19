import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_grafana_dashboard_is_valid_and_covers_core_slos():
    dashboard = json.loads(
        (PROJECT_ROOT / "ops/grafana/commerce-copilot-dashboard.json").read_text(
            encoding="utf-8"
        )
    )

    assert dashboard["uid"] == "commerce-copilot"
    assert dashboard["timezone"] == "Asia/Shanghai"
    expressions = [
        target["expr"]
        for panel in dashboard["panels"]
        for target in panel.get("targets", [])
    ]
    assert any("commerce_turn_duration_seconds_bucket" in item for item in expressions)
    assert any("commerce_llm_fallbacks_total" in item for item in expressions)
    assert any("commerce_grounding_validations_total" in item for item in expressions)
    assert any("commerce_tool_calls_total" in item for item in expressions)


def test_prometheus_scrapes_the_local_metrics_endpoint():
    config = (PROJECT_ROOT / "ops/prometheus/prometheus.yml").read_text(
        encoding="utf-8"
    )

    assert "job_name: commerce-copilot" in config
    assert "metrics_path: /metrics" in config
    assert "127.0.0.1:8010" in config
