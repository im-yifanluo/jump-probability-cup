import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from current_player_stats_audit import (  # noqa: E402
    OpenPlayerMarket,
    build_sources,
    extract_player_lines,
    render,
)
from questions.parser import parse  # noqa: E402


EVENT = {
    "id": "1",
    "date": "2026-06-20T17:00Z",
    "competitions": [
        {
            "competitors": [
                {"homeAway": "home", "team": {"displayName": "Brazil"}},
                {"homeAway": "away", "team": {"displayName": "Japan"}},
            ]
        }
    ],
    "status": {"type": {"completed": True}},
}


def _stat(name: str, value: float):
    return {"name": name, "value": value, "displayValue": str(value)}


def _player(name: str, *, starter=True, position="Forward", goals=0, assists=0, sot=0, shots=0):
    return {
        "active": True,
        "starter": starter,
        "subbedIn": not starter,
        "position": {"name": position},
        "athlete": {"displayName": name},
        "stats": [
            _stat("appearances", 1),
            _stat("subIns", 0 if starter else 1),
            _stat("totalGoals", goals),
            _stat("goalAssists", assists),
            _stat("shotsOnTarget", sot),
            _stat("totalShots", shots),
        ],
    }


def test_extract_player_lines_uses_roster_stats_and_skips_non_appearances():
    summary = {
        "rosters": [
            {
                "team": {"displayName": "Brazil"},
                "roster": [
                    _player("Matheus Cunha", goals=1, sot=2, shots=3),
                    {
                        **_player("Unused Bench", starter=False),
                        "stats": [_stat("appearances", 0)],
                    },
                ],
            }
        ]
    }

    lines = extract_player_lines(EVENT, summary)

    assert len(lines) == 1
    assert lines[0].player == "Matheus Cunha"
    assert lines[0].team == "Brazil"
    assert lines[0].stats["totalGoals"] == 1
    assert lines[0].stats["shotsOnTarget"] == 2


def test_build_sources_prices_current_player_sot_with_shrinkage():
    summary = {
        "rosters": [
            {
                "team": {"displayName": "Brazil"},
                "roster": [
                    _player("Ayase Ueda", goals=0, sot=2, shots=4),
                    _player("Other Forward", goals=0, sot=0, shots=1),
                ],
            }
        ]
    }
    lines = extract_player_lines(EVENT, summary)
    market = OpenPlayerMarket(
        market_id="m1",
        match="Brazil vs Japan",
        kickoff=datetime(2026, 6, 29, 17, tzinfo=timezone.utc),
        question="Will Ayase Ueda have at least 1 shot on target in regulation (90 minutes + stoppage time)?",
        spec=parse(
            "Will Ayase Ueda have at least 1 shot on target in regulation (90 minutes + stoppage time)?",
            teams={"Brazil", "Japan"},
        ),
    )

    [source], global_means = build_sources([market], lines, {"m1": {"manual_entry": "28%", "raw_model_probability": "37%"}})

    assert source.source_state == "CURRENT_PLAYER_STATS_SOURCE"
    assert source.aggregate is not None
    assert source.aggregate.appearances == 1
    assert source.stat_key == "sot"
    assert source.threshold == 1
    assert source.probability is not None
    assert 0 < global_means["sot"] < 2
    assert source.manual_entry == "28%"
    assert source.raw_model == "37%"


def test_surname_fallback_matches_unambiguous_player():
    summary = {"rosters": [{"team": {"displayName": "Germany"}, "roster": [_player("Kai Havertz", goals=1, sot=1)]}]}
    lines = extract_player_lines(EVENT, summary)
    market = OpenPlayerMarket(
        market_id="m2",
        match="Germany vs Paraguay",
        kickoff=datetime(2026, 6, 29, 20, 30, tzinfo=timezone.utc),
        question="Will Havertz score a goal (excluding own goals) in regulation (90 minutes + stoppage time)?",
        spec=parse(
            "Will Havertz score a goal (excluding own goals) in regulation (90 minutes + stoppage time)?",
            teams={"Germany", "Paraguay"},
        ),
    )

    [source], _ = build_sources([market], lines)

    assert source.source_state == "CURRENT_PLAYER_STATS_SOURCE"
    assert source.aggregate is not None
    assert source.aggregate.player == "Kai Havertz"


def test_render_exposes_partial_status_and_guardrails():
    market = OpenPlayerMarket(
        market_id="m3",
        match="Brazil vs Japan",
        kickoff=datetime(2026, 6, 29, 17, tzinfo=timezone.utc),
        question="Will Unknown Player score a goal (excluding own goals) in regulation (90 minutes + stoppage time)?",
        spec=parse(
            "Will Unknown Player score a goal (excluding own goals) in regulation (90 minutes + stoppage time)?",
            teams={"Brazil", "Japan"},
        ),
    )
    summary = {"rosters": [{"team": {"displayName": "Brazil"}, "roster": [_player("Matheus Cunha", goals=1, sot=2)]}]}
    player_lines = extract_player_lines(EVENT, summary)
    sources, global_means = build_sources([market], player_lines)

    text = render(
        sources,
        lines=player_lines,
        global_means=global_means,
        sports_predict=True,
        now=datetime(2026, 6, 29, 9, tzinfo=timezone.utc),
    )

    assert "current_player_stats_status: NO_MATCHED_CURRENT_PLAYER_STATS" in text
    assert "`/predictions` was not read" in text
    assert "not a promoted replacement" in text
