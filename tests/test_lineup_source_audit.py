import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from full_manual_pricing import MarketRow  # noqa: E402
from lineup_source_audit import audit_lineups, render  # noqa: E402


TZ = ZoneInfo("America/Chicago")


def _row(match: str, kickoff: datetime, market_id: str, question: str) -> MarketRow:
    return MarketRow(
        match_name=match,
        kickoff=kickoff,
        market_id=market_id,
        question=question,
    )


def _player_row(match: str, kickoff: datetime, market_id: str = "p1") -> MarketRow:
    return _row(
        match,
        kickoff,
        market_id,
        "Will Ayase Ueda have at least 1 shot on target in regulation (90 minutes + stoppage time)?",
    )


def test_pending_not_in_window_when_lineup_not_posted_early():
    kickoff = datetime(2026, 6, 29, 17, tzinfo=timezone.utc)
    now = datetime(2026, 6, 29, 11, tzinfo=timezone.utc)

    result = audit_lineups(
        [_player_row("BRA vs JPN", kickoff), _row("BRA vs JPN", kickoff, "g1", "Will Brazil win?")],
        local_date="2026-06-29",
        tz=TZ,
        all_open=True,
        now=now,
        puller=lambda match, kickoff, players: {"status": "not_posted", "minutes": {}, "unmatched": sorted(players), "detail": []},
    )

    assert result.status == "PENDING_NOT_IN_WINDOW"
    assert result.rows[0].risk_state == "NOT_POSTED_PRE_WINDOW"
    assert result.rows[0].player_rows == 1
    assert result.rows[0].in_pull_window is False


def test_not_posted_inside_pull_window_blocks_lineup_lock():
    kickoff = datetime(2026, 6, 29, 17, tzinfo=timezone.utc)
    now = datetime(2026, 6, 29, 15, 45, tzinfo=timezone.utc)

    result = audit_lineups(
        [_player_row("BRA vs JPN", kickoff)],
        local_date="2026-06-29",
        tz=TZ,
        all_open=True,
        now=now,
        puller=lambda match, kickoff, players: {"status": "not_posted", "minutes": {}, "unmatched": sorted(players), "detail": []},
    )
    text = render(result, now=now)

    assert result.status == "LINEUP_NOT_POSTED_IN_PULL_WINDOW"
    assert result.rows[0].risk_state == "NOT_POSTED_IN_PULL_WINDOW"
    assert "- lineup_source_status: LINEUP_NOT_POSTED_IN_PULL_WINDOW" in text
    assert "- in_window_not_posted_matches: 1" in text


def test_complete_when_all_player_matches_have_confirmed_lineups():
    kickoff = datetime(2026, 6, 29, 17, tzinfo=timezone.utc)
    now = datetime(2026, 6, 29, 15, 45, tzinfo=timezone.utc)

    def puller(match, kickoff, players):
        return {
            "status": "pulled",
            "minutes": {"Ayase Ueda": 90},
            "unmatched": [],
            "detail": [("Ayase Ueda", "Ayase Ueda", "START", 90)],
        }

    result = audit_lineups(
        [_player_row("BRA vs JPN", kickoff)],
        local_date="2026-06-29",
        tz=TZ,
        all_open=True,
        now=now,
        puller=puller,
    )

    assert result.status == "COMPLETE"
    assert result.rows[0].risk_state == "PULLED_CONFIRMED"
    assert result.rows[0].starter_players == 1
    assert result.rows[0].unmatched == ()


def test_pulled_with_unmatched_players_is_source_issue():
    kickoff = datetime(2026, 6, 29, 17, tzinfo=timezone.utc)
    now = datetime(2026, 6, 29, 15, 45, tzinfo=timezone.utc)

    def puller(match, kickoff, players):
        return {
            "status": "pulled",
            "minutes": {},
            "unmatched": ["Ayase Ueda"],
            "detail": [],
        }

    result = audit_lineups(
        [_player_row("BRA vs JPN", kickoff)],
        local_date="2026-06-29",
        tz=TZ,
        all_open=True,
        now=now,
        puller=puller,
    )

    assert result.status == "LINEUP_SOURCE_ISSUES"
    assert result.rows[0].risk_state == "PULLED_WITH_UNMATCHED_PLAYERS"
    assert result.rows[0].unmatched == ("Ayase Ueda",)
