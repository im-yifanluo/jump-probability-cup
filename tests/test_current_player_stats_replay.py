import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from backtest.history import HistoryMarket  # noqa: E402
from current_player_stats_audit import PlayerLine  # noqa: E402
from current_player_stats_replay import (  # noqa: E402
    build_replay,
    event_refs_from_lines,
    promotion_decision,
    same_match,
    score_policy,
)


def _line(
    event_id: str,
    kickoff: datetime,
    match: str,
    player: str,
    *,
    team: str = "Japan",
    sot: int = 0,
    goals: int = 0,
    assists: int = 0,
    starter: bool = True,
) -> PlayerLine:
    return PlayerLine(
        event_id=event_id,
        kickoff=kickoff,
        match=match,
        team=team,
        player=player,
        position="Forward",
        starter=starter,
        subbed_in=not starter,
        stats={
            "appearances": 1.0,
            "subIns": 0.0 if starter else 1.0,
            "shotsOnTarget": float(sot),
            "totalGoals": float(goals),
            "goalAssists": float(assists),
        },
    )


def _row(match: str, *, outcome: int = 1, you: float = 0.20) -> HistoryMarket:
    return HistoryMarket(
        version=2,
        order=2,
        match=match,
        question="Will Ayase Ueda have at least 1 shot on target in regulation (90 minutes + stoppage time)?",
        you=you,
        crowd=0.50,
        outcome=outcome,
        official_rbp=0.0,
        family="player_sot_ge",
        stat="sot",
        period="match",
    )


def test_same_match_allows_accents_and_reversed_order():
    assert same_match("Brazil vs Japan", "Japan vs Brazil")
    assert same_match("Côte d'Ivoire vs Ecuador", "Ivory Coast vs Ecuador")


def test_event_refs_from_lines_deduplicates_events():
    kickoff = datetime(2026, 6, 20, tzinfo=timezone.utc)
    refs = event_refs_from_lines(
        [
            _line("e1", kickoff, "Brazil vs Japan", "Ayase Ueda"),
            _line("e1", kickoff, "Brazil vs Japan", "Other Player"),
        ]
    )

    assert len(refs) == 1
    assert refs[0].match == "Brazil vs Japan"


def test_replay_uses_only_prior_player_stats_for_scored_match():
    first = datetime(2026, 6, 20, tzinfo=timezone.utc)
    second = datetime(2026, 6, 29, tzinfo=timezone.utc)
    replay = build_replay(
        [_row("Brazil vs Japan", outcome=1, you=0.20)],
        [
            _line("e1", first, "Japan vs Spain", "Ayase Ueda", sot=2),
            _line("e2", second, "Brazil vs Japan", "Ayase Ueda", sot=0),
        ],
    )

    [item] = replay
    assert item.source_state == "CURRENT_PLAYER_STATS_SOURCE"
    assert item.player_apps == 1
    assert item.current_probability is not None
    assert item.current_probability > item.row.you
    assert item.predictions["raw50_current50"] > item.row.you


def test_replay_does_not_leak_same_match_stats():
    kickoff = datetime(2026, 6, 29, tzinfo=timezone.utc)
    replay = build_replay(
        [_row("Brazil vs Japan", outcome=1, you=0.20)],
        [_line("e2", kickoff, "Brazil vs Japan", "Ayase Ueda", sot=5)],
    )

    [item] = replay
    assert item.source_state == "NO_PRIOR_PLAYER_STATS"
    assert item.current_probability is None
    assert item.predictions == {}


def test_score_policy_and_promotion_decision_require_bootstrap_support():
    first = datetime(2026, 6, 20, tzinfo=timezone.utc)
    second = datetime(2026, 6, 29, tzinfo=timezone.utc)
    replay = build_replay(
        [_row("Brazil vs Japan", outcome=1, you=0.20)],
        [
            _line("e1", first, "Japan vs Spain", "Ayase Ueda", sot=2),
            _line("e2", second, "Brazil vs Japan", "Ayase Ueda", sot=0),
        ],
    )
    scored = [item for item in replay if item.current_probability is not None]

    assert score_policy(scored, "current_player_stats")["delta_vs_raw"] > 0
    assert promotion_decision(scored, "current_player_stats", {"ci_2.5": 1.0, "pr_gt_0": 1.0}) == "NOT_PROMOTED_TOO_SPARSE"
