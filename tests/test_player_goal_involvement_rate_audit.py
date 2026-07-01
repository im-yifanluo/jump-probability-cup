import sys
from datetime import date
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from player_goal_involvement_rate_audit import analyze, appearances_from_events, load_appearances  # noqa: E402


def test_appearances_from_events_reconstructs_goal_and_assist_rows():
    events = [
        {
            "type": {"name": "Starting XI"},
            "team": {"name": "Alpha"},
            "tactics": {
                "lineup": [
                    {"player": {"name": "Alpha Forward"}, "position": {"name": "Center Forward"}},
                    {"player": {"name": "Alpha Keeper"}, "position": {"name": "Goalkeeper"}},
                ]
            },
        },
        {
            "type": {"name": "Shot"},
            "team": {"name": "Alpha"},
            "player": {"name": "Alpha Forward"},
            "position": {"name": "Center Forward"},
            "shot": {"outcome": {"name": "Goal"}},
        },
        {
            "type": {"name": "Pass"},
            "team": {"name": "Alpha"},
            "player": {"name": "Alpha Creator"},
            "position": {"name": "Right Wing"},
            "pass": {"goal_assist": True},
        },
    ]

    rows = appearances_from_events(
        tournament="fixture",
        match_id=1,
        match_date=date(2026, 6, 29),
        events=events,
    )
    by_player = {row.player: row for row in rows}

    assert by_player["Alpha Forward"].goals == 1
    assert by_player["Alpha Forward"].assists == 0
    assert by_player["Alpha Forward"].market_like is True
    assert by_player["Alpha Creator"].goals == 0
    assert by_player["Alpha Creator"].assists == 1
    assert by_player["Alpha Creator"].market_like is True
    assert by_player["Alpha Keeper"].market_like is False


def test_real_statsbomb_goal_involvement_shrinkage_is_not_promoted():
    raw_dir = ROOT / "data_reference" / "raw" / "statsbomb"
    if not raw_dir.exists():
        pytest.skip("StatsBomb raw data is not available in this checkout")

    analysis = analyze(load_appearances(raw_dir))
    boot = {
        (row["family"], row["comparison"]): row
        for row in analysis["bootstraps"]
    }

    assert analysis["decision"] == "not_promoted_keep_current_goal_involvement_shrinkage"
    assert analysis["appearances"] == 9318
    assert analysis["matches"] == 314
    assert analysis["market_like_rows"] == 3883
    assert analysis["chronological_scored_rows"] == 3161
    assert boot[("goal", "app_shrink_k8_minus_app_shrink_k6")]["ci_2.5"] < 0
    assert boot[("goal_or_assist", "app_shrink_k8_minus_app_shrink_k6")]["ci_2.5"] < 0
