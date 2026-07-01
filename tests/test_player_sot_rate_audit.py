import sys
from datetime import date
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from player_sot_rate_audit import analyze, appearances_from_events, load_appearances  # noqa: E402


def test_appearances_from_events_reconstructs_market_like_sot_rows():
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
            "type": {"name": "Substitution"},
            "team": {"name": "Alpha"},
            "player": {"name": "Alpha Forward"},
            "position": {"name": "Center Forward"},
            "minute": 60,
            "second": 0,
            "substitution": {"replacement": {"name": "Alpha Sub"}},
        },
        {
            "type": {"name": "Shot"},
            "team": {"name": "Alpha"},
            "player": {"name": "Alpha Forward"},
            "position": {"name": "Center Forward"},
            "shot": {"outcome": {"name": "Saved"}},
        },
        {
            "type": {"name": "Shot"},
            "team": {"name": "Alpha"},
            "player": {"name": "Alpha Sub"},
            "position": {"name": "Center Forward"},
            "shot": {"outcome": {"name": "Goal"}},
        },
    ]

    rows = appearances_from_events(
        tournament="fixture",
        match_id=1,
        match_date=date(2026, 6, 29),
        events=events,
    )
    by_player = {row.player: row for row in rows}

    assert by_player["Alpha Forward"].minutes == 60.0
    assert by_player["Alpha Forward"].sot == 1
    assert by_player["Alpha Forward"].market_like is True
    assert by_player["Alpha Sub"].minutes == 30.0
    assert by_player["Alpha Sub"].sot == 1
    assert by_player["Alpha Sub"].market_like is True
    assert by_player["Alpha Keeper"].market_like is False


def test_real_statsbomb_sot_shrinkage_audit_is_1plus_only_proxy_positive():
    raw_dir = ROOT / "data_reference" / "raw" / "statsbomb"
    if not raw_dir.exists():
        pytest.skip("StatsBomb raw data is not available in this checkout")

    analysis = analyze(load_appearances(raw_dir))
    boot = {
        (row["threshold"], row["comparison"]): row
        for row in analysis["bootstraps"]
    }

    assert analysis["decision"] == "proxy_candidate_app_shrink_k8_positive_for_1plus_only_not_live_promoted"
    assert analysis["appearances"] == 9318
    assert analysis["matches"] == 314
    assert analysis["market_like_rows"] == 3883
    assert analysis["chronological_scored_rows"] == 3161
    assert boot[(1, "app_shrink_k8_minus_app_shrink_k1")]["ci_2.5"] > 0
    assert boot[(2, "app_shrink_k8_minus_app_shrink_k1")]["ci_2.5"] < 0
