import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from player_lineup_status_backtest import classify_player, split_match  # noqa: E402


def _player(name, starter):
    return {"starter": starter, "athlete": {"displayName": name}}


def _rosters():
    return [
        {
            "team": {"displayName": "Germany"},
            "roster": [
                _player("Kai Havertz", True),
                _player("Florian Wirtz", True),
                _player("Jamal Musiala", False),
            ],
        },
        {
            "team": {"displayName": "Paraguay"},
            "roster": [
                _player("Julio Enciso", True),
                _player("Diego Gomez", False),
                _player("Carlos Smith", False),
            ],
        },
    ]


def test_classify_player_detects_starter_and_bench():
    assert classify_player("Kai Havertz", _rosters()).status == "starter"

    status = classify_player("Jamal Musiala", _rosters())
    assert status.status == "bench"
    assert status.espn_player == "Jamal Musiala"
    assert status.espn_team == "Germany"


def test_classify_player_uses_team_hint_to_avoid_cross_team_ambiguity():
    rosters = _rosters()
    rosters[0]["roster"].append(_player("Jonas Smith", True))

    ambiguous = classify_player("Smith", rosters)
    assert ambiguous.status == "ambiguous"

    germany = classify_player("Smith", rosters, team_hint="Germany")
    assert germany.status == "starter"
    assert germany.espn_player == "Jonas Smith"

    paraguay = classify_player("Smith", rosters, team_hint="Paraguay")
    assert paraguay.status == "bench"
    assert paraguay.espn_player == "Carlos Smith"


def test_classify_player_not_listed_and_team_mismatch_are_explicit():
    assert classify_player("Thomas Muller", _rosters(), team_hint="Germany").status == "not_listed"

    status = classify_player("Kai Havertz", _rosters(), team_hint="Brazil")
    assert status.status == "team_mismatch"
    assert "Brazil" in status.reason


def test_split_match_handles_probability_cup_codes_and_history_names():
    assert split_match("RSA vs CAN") == ("South Africa", "Canada")
    assert split_match("Turkey vs United States") == ("Türkiye", "United States")
