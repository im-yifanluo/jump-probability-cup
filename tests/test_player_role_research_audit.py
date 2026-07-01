from datetime import datetime, timezone
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from player_role_research_audit import (
    ESPN_SCOREBOARD,
    ESPN_SUMMARY,
    PlayerTarget,
    RoleObservation,
    _role_from_roster,
    classify_role,
    fetch_recent_role_observations,
    load_role_summary,
    role_action,
)


def _roster(team="Canada"):
    return {
        "team": {"displayName": team},
        "roster": [
            {"starter": True, "athlete": {"displayName": "Cyle Larin"}},
            {"starter": False, "athlete": {"displayName": "Promise David"}},
        ],
    }


def _obs(role):
    return RoleObservation(
        kickoff=datetime(2026, 6, 1, tzinfo=timezone.utc),
        event_name="Test Match",
        team="Canada",
        role=role,
        matched_name="Cyle Larin" if role != "not_listed" else "",
        espn_event_id="1",
    )


def test_role_from_roster_matches_starter_and_bench():
    assert _role_from_roster("Cyle Larin", _roster()) == ("starter", "Cyle Larin")
    assert _role_from_roster("Promise David", _roster()) == ("bench", "Promise David")
    assert _role_from_roster("Missing Player", _roster()) == ("not_listed", "")
    assert _role_from_roster("Missing Player", None) == ("no_roster", "")


def test_classify_role_recent_patterns():
    assert classify_role([]) == "unknown"
    assert classify_role([_obs("starter"), _obs("starter"), _obs("starter")]) == "likely_starter"
    assert classify_role([_obs("starter"), _obs("bench")]) == "bench_or_rotation_risk"
    assert classify_role([_obs("bench"), _obs("starter")]) == "rotation_risk"


def test_role_action_keeps_risk_without_promoting_override():
    target = PlayerTarget(
        match="RSA vs CAN",
        kickoff=datetime(2026, 6, 28, tzinfo=timezone.utc),
        market_id="m1",
        player="Iqraam Rayners",
        team="South Africa",
        question="Will Iqraam Rayners have at least 1 shot on target?",
        flags="SPARSE_HISTORY,LINEUP_NOT_POSTED",
        model_probability=35,
    )

    assert "final XI" in role_action(target, "bench_or_rotation_risk")
    assert "starter minutes" in role_action(target, "likely_starter")


def test_load_role_summary_uses_exact_market_id(tmp_path):
    log = tmp_path / "roles.md"
    log.write_text(
        """
# Player Role Research Audit

## Current Open Player Role Summary

| kickoff_local | match | market_id | player | team | model | role_class | recent_roles | action | question |
| --- | --- | --- | --- | --- | ---: | --- | --- | --- | --- |
| 2026-06-29 12:00 CDT | BRA vs JPN | m-role | Matheus Cunha | Brazil | 41% | rotation_risk | 06-13:bench, 06-19:starter | reprice after final XI | Will Matheus Cunha score? |
        """,
        encoding="utf-8",
    )

    summary = load_role_summary(log)

    assert summary["m-role"]["role_class"] == "rotation_risk"
    assert "06-13:bench" in summary["m-role"]["recent_roles"]


def test_recent_role_fetch_uses_shared_espn_caches(monkeypatch):
    calls = []
    board = {
        "events": [
            {
                "id": "event-1",
                "date": "2026-06-01T20:00Z",
                "name": "Canada at Test",
                "competitions": [
                    {
                        "competitors": [
                            {"team": {"displayName": "Canada"}},
                            {"team": {"displayName": "Test"}},
                        ]
                    }
                ],
            }
        ]
    }
    summary = {"rosters": [_roster("Canada")]}

    def fake_get_json(url, params):
        calls.append((url, tuple(sorted(params.items()))))
        if url == ESPN_SCOREBOARD:
            return board
        if url == ESPN_SUMMARY:
            return summary
        raise AssertionError(url)

    monkeypatch.setattr("player_role_research_audit.get_json", fake_get_json)
    scoreboard_cache = {}
    summary_cache = {}
    before = datetime(2026, 6, 2, tzinfo=timezone.utc)

    fetch_recent_role_observations(
        "Canada",
        "Cyle Larin",
        before=before,
        lookback_days=1,
        scoreboard_cache=scoreboard_cache,
        summary_cache=summary_cache,
    )
    fetch_recent_role_observations(
        "Canada",
        "Promise David",
        before=before,
        lookback_days=1,
        scoreboard_cache=scoreboard_cache,
        summary_cache=summary_cache,
    )

    assert calls.count((ESPN_SCOREBOARD, (("dates", "20260601"),))) == 1
    assert calls.count((ESPN_SCOREBOARD, (("dates", "20260602"),))) == 1
    assert calls.count((ESPN_SUMMARY, (("event", "event-1"),))) == 1
