import sys
from datetime import date
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from player_minutes_uncertainty_audit import analyze, appearances_from_events, load_appearances  # noqa: E402


def test_appearances_from_events_reconstructs_minutes_and_events():
    events = [
        {
            "type": {"name": "Starting XI"},
            "team": {"name": "Alpha"},
            "tactics": {
                "lineup": [
                    {"player": {"name": "Alpha Starter"}},
                    {"player": {"name": "Alpha Creator"}},
                ]
            },
        },
        {
            "type": {"name": "Starting XI"},
            "team": {"name": "Beta"},
            "tactics": {"lineup": [{"player": {"name": "Beta Starter"}}]},
        },
        {
            "type": {"name": "Substitution"},
            "team": {"name": "Alpha"},
            "player": {"name": "Alpha Starter"},
            "minute": 60,
            "second": 0,
            "substitution": {"replacement": {"name": "Alpha Substitute"}},
        },
        {
            "type": {"name": "Shot"},
            "team": {"name": "Alpha"},
            "player": {"name": "Alpha Starter"},
            "shot": {"outcome": {"name": "Saved"}},
        },
        {
            "type": {"name": "Shot"},
            "team": {"name": "Alpha"},
            "player": {"name": "Alpha Substitute"},
            "shot": {"outcome": {"name": "Goal"}},
        },
        {
            "type": {"name": "Pass"},
            "team": {"name": "Alpha"},
            "player": {"name": "Alpha Creator"},
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

    assert by_player["Alpha Starter"].role == "starter"
    assert by_player["Alpha Starter"].minutes == 60.0
    assert by_player["Alpha Starter"].sot == 1
    assert by_player["Alpha Starter"].goals == 0
    assert by_player["Alpha Substitute"].role == "substitute"
    assert by_player["Alpha Substitute"].minutes == 30.0
    assert by_player["Alpha Substitute"].sot == 1
    assert by_player["Alpha Substitute"].goals == 1
    assert by_player["Alpha Creator"].assists == 1


def test_real_statsbomb_proxy_replay_keeps_minutes_uncertainty_candidate_positive():
    raw_dir = ROOT / "data_reference" / "raw" / "statsbomb"
    if not raw_dir.exists():
        pytest.skip("StatsBomb raw data is not available in this checkout")

    analysis = analyze(load_appearances(raw_dir))

    assert analysis["decision"] == "proxy_candidate_minutes_uncertainty_positive_not_live_promoted"
    assert analysis["appearances"] == 9318
    assert analysis["matches"] == 314
    families = analysis["families"]
    for family in ("sot1", "scores", "score_or_assist"):
        assert families[family]["no_lineup_bootstrap"]["ci_2.5"] > 0
        assert families[family]["confirmed_role_bootstrap"]["ci_2.5"] > 0
