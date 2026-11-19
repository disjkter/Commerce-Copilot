from datetime import datetime, timezone

from commerce_copilot.agents import (
    _format_logistics_message,
    _format_order_message,
)
from commerce_copilot.answering import _localize_answer_datetimes


def test_order_time_is_displayed_in_china_timezone():
    answer = _format_order_message(
        "ORD-2026-1001",
        {"placed_at": datetime(2026, 10, 16, 19, 25, tzinfo=timezone.utc)},
    )

    assert "2026-10-17 03:25（北京时间）" in answer
    assert "+00:00" not in answer


def test_logistics_time_is_displayed_in_china_timezone():
    answer = _format_logistics_message(
        {
            "order_id": "ORD-2026-1001",
            "status_label": "运输中",
            "latest_event_description": "快件正在发往下一站",
            "latest_event_location": "上海转运中心",
            "latest_event_at": "2026-10-16T19:25:00+00:00",
            "estimated_delivery_at": None,
        }
    )

    assert "2026-10-17 03:25（北京时间）" in answer
    assert "+00:00" not in answer


def test_llm_answer_iso_time_is_localized_after_grounding():
    answer = _localize_answer_datetimes(
        "物流更新时间为 2026-10-16T19:25:00+00:00。"
    )

    assert answer == "物流更新时间为 2026-10-17 03:25（北京时间）。"

