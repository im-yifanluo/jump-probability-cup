import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from promotion_strength_audit import build_rows, render  # noqa: E402


def _by_area(rows):
    return {row.area: row for row in rows}


def test_promotion_strength_classifies_soft_and_limited_warnings():
    rows = _by_area(build_rows(ROOT / "data" / "logs"))

    assert rows["combined_manual_policy"].strength == "PROMOTED_STRONG"
    assert rows["odds_goal_rows"].strength == "PROMOTED_SOFT"
    assert rows["odds_conversion_power"].strength == "NOT_PROMOTED_TOTALS_GATE"
    assert rows["count_field_policy"].strength == "PROMOTED_STRONG_COMPONENT_WARNING"
    assert rows["researched_player_props"].strength == "PROMOTED_LIMITED_STRONG_FIELD_WARNING"
    assert rows["player_no_field_fallback"].strength == "PROMOTED_LIMITED_STRONG"
    assert rows["exact_polymarket_anchors"].strength == "CURRENT_SOURCE_ONLY_NEGATIVE_REPLAY"
    assert rows["special_markets"].strength == "PROXY_ONLY_OFFICIAL_SAMPLE_TOO_SMALL"


def test_promotion_strength_render_includes_read_only_guardrails():
    text = render(
        build_rows(ROOT / "data" / "logs"),
        now=datetime(2026, 6, 29, 3, tzinfo=timezone.utc),
    )

    assert "# Promotion Strength Audit" in text
    assert "`/predictions` was not read" in text
    assert "classification_only_no_probability_transform" in text
    assert "PROMOTED_SOFT" in text
    assert "NOT_PROMOTED_TOTALS_GATE" in text
    assert "CURRENT_SOURCE_ONLY_NEGATIVE_REPLAY" in text
