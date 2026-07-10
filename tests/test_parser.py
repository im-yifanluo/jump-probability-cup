"""Parser must handle every market in the live snapshot, with correct fields."""

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from questions.parser import MarketSpec, parse
from questions.teams import ALL_TEAM_NAMES, match_teams

SNAPSHOT = json.loads((ROOT / "data" / "snapshot.json").read_text())


def test_all_snapshot_markets_parse():
    failures = []
    for market in SNAPSHOT["markets"]:
        try:
            parse(market["question"], teams=ALL_TEAM_NAMES)
        except Exception as exc:
            failures.append(f"{market['question']}  ({exc.__class__.__name__})")
    assert not failures, f"{len(failures)} unparsed:\n" + "\n".join(failures[:20])


def test_subjects_resolve_to_match_teams_or_players():
    """Team fields must be one of that match's two teams; never a player."""
    matches = {m["id"]: m for m in SNAPSHOT["matches"]}
    for market in SNAPSHOT["markets"]:
        spec = parse(market["question"], teams=ALL_TEAM_NAMES)
        pair = set(match_teams(matches[market["match"]["id"]]["name"]))
        for name in (spec.team, spec.opponent):
            assert name is None or name in pair, (market["question"], name, pair)
        if spec.player is not None:
            assert spec.player not in ALL_TEAM_NAMES, market["question"]


def test_known_specs():
    teams = ALL_TEAM_NAMES
    assert parse("Will South Korea win the match?", teams) == MarketSpec(
        "team_win", team="South Korea")
    assert parse("Will the match have 2 or fewer total goals?", teams) == MarketSpec(
        "total_goals", stat="goals", threshold=2, direction="<=")
    assert parse("Will Haiti have at least 1 shot on target in the second half?", teams
                 ).family == "team_count_ge"
    assert parse("Will Son Heung-min have at least 1 shot on target?", teams
                 ).family == "player_sot_ge"
    assert parse("At halftime, will Czechia have more corner kicks than South Korea?", teams
                 ) == MarketSpec("team_more_count_than", team="Czechia",
                                 opponent="South Korea", stat="corners", period="1h")
    # A trailing "(Team)" disambiguator must be stripped from the player name, or
    # it matches no StatsBomb history / override and silently drops to the prior.
    assert parse("Will Kai Havertz (Germany) score a goal (excluding own goals) in "
                 "regulation (90 minutes + stoppage time)?", teams
                 ) == MarketSpec("player_scores", player="Kai Havertz")
    assert parse("Will Julio Enciso (Paraguay) have 1 or more shots on target in "
                 "regulation (90 minutes + stoppage time)?", teams
                 ).player == "Julio Enciso"


def test_sf_ko_family_templates():
    """The SF/KO families added 2026-07-07 (ARG/EGY + SUI/COL), through the KO
    regulation-suffix normaliser."""
    teams = ["Argentina", "Egypt"]
    reg = " in regulation (90 minutes + stoppage time)"
    assert parse(f"Will the total number of goals{reg} be an odd number?", teams
                 ).family == "total_goals_odd"
    assert parse(f"Will the match be decided by exactly one goal{reg}?", teams
                 ).family == "decided_by_one_goal"
    assert parse("Will the first goal of the match be scored in the second half"
                 f" of regulation (90 minutes + stoppage time)?", teams
                 ).family == "first_goal_in_2h"
    assert parse(f"Will either team win both halves{reg}?", teams
                 ).family == "either_wins_both_halves"
    assert parse("Will at least one card be shown in each half"
                 " of regulation (90 minutes + stoppage time)?", teams
                 ).family == "card_each_half"
    fg = parse("Will the first goal of the match be scored by a player other "
               "than Lionel Messi and Mohamed Salah?", teams)
    assert fg.family == "first_goal_not_players"
    assert fg.params["exclude"] == ["Lionel Messi", "Mohamed Salah"]


def test_family_coverage_summary():
    families = Counter(
        parse(m["question"], teams=ALL_TEAM_NAMES).family for m in SNAPSHOT["markets"]
    )
    print("\nFamily counts:", dict(families.most_common()))
    assert sum(families.values()) == len(SNAPSHOT["markets"])
