"""Parser must handle every market in the live snapshot, with correct fields."""

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from questions.parser import MarketSpec, parse
from questions.teams import ALL_TEAM_NAMES, match_teams

SNAPSHOT_PATH = ROOT / "data" / "snapshot.json"
if not SNAPSHOT_PATH.exists():
    SNAPSHOT_PATH = ROOT / "data_reference" / "snapshot.json"
SNAPSHOT = json.loads(SNAPSHOT_PATH.read_text())


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
    assert parse("Will the match be 0-0 at halftime?", teams) == MarketSpec(
        "ht_score_exact",
        stat="goals",
        period="1h",
        params={"home_goals": 0, "away_goals": 0},
    )
    assert parse("Will exactly 1 goal be scored in regulation (90 minutes + stoppage time)?", teams) == MarketSpec(
        "total_goals", stat="goals", threshold=1, direction="==")
    assert parse("Will the match finish with exactly 2 total goals in regulation (90 minutes + stoppage time)?", teams) == MarketSpec(
        "total_goals", stat="goals", threshold=2, direction="==")
    assert parse(
        "Will the total number of goals in regulation (90 minutes + stoppage time) be an odd number?",
        teams,
    ) == MarketSpec("total_goals_parity", stat="goals", params={"parity": "odd"})
    assert parse("Will Morocco advance to the quarterfinals?", teams) == MarketSpec(
        "team_advance", team="Morocco")
    assert parse("Will the match be decided by a penalty shootout?", teams) == MarketSpec(
        "penalty_shootout", stat="goals")
    assert parse(
        "Will Paraguay hold a lead at any point in the match (excluding a penalty shootout)?",
        teams,
    ) == MarketSpec("team_leads_any_time", team="Paraguay", stat="goals")
    assert parse(
        "Will Belgium hold a lead at any point in regulation (90 minutes + stoppage time, excluding penalty shootout)?",
        teams,
    ) == MarketSpec("team_leads_any_time", team="Belgium", stat="goals")
    assert parse(
        "Will there be more total cards than total goals in regulation (90 minutes + stoppage time)?",
        teams,
    ) == MarketSpec("total_cards_more_than_goals", stat="cards")
    assert parse(
        "Will there be 4 or more total cards in regulation (90 minutes + stoppage time)?",
        teams,
    ) == MarketSpec("total_count_ge", stat="cards", threshold=4)
    assert parse(
        "Will Christian Pulisic (United States) play the entire match in regulation (90 minutes + stoppage time)?",
        teams,
    ) == MarketSpec(
        "player_full_match",
        player="Christian Pulisic",
        stat="minutes",
        threshold=90,
        params={"player_team": "United States"},
    )
    assert parse(
        "Will at least one goal be scored in each half in regulation (90 minutes + stoppage time)?",
        teams,
    ) == MarketSpec("goal_each_half", stat="goals")
    assert parse(
        "Will either team win both halves in regulation (90 minutes + stoppage time)?",
        teams,
    ) == MarketSpec("either_team_wins_both_halves", stat="goals")
    assert parse(
        "Will the match be decided by exactly one goal in regulation (90 minutes + stoppage time)?",
        teams,
    ) == MarketSpec("match_decided_by_exactly_one_goal", stat="goals", threshold=1)
    assert parse(
        "Will at least one card be shown in each half of regulation (90 minutes + stoppage time)?",
        teams,
    ) == MarketSpec("total_count_each_half_ge", stat="cards", threshold=1)
    assert parse(
        "Will a card be shown during first- or second-half stoppage time in regulation?",
        teams,
    ) == MarketSpec("card_in_stoppage_any_half", stat="cards")
    assert parse(
        "Will both halves have the same number of goals in regulation (90 minutes + stoppage time)?",
        teams,
    ) == MarketSpec("halves_same_goals", stat="goals")
    assert parse("Will the match go to extra time?", teams) == MarketSpec(
        "match_goes_to_extra_time", stat="goals")
    assert parse(
        "Will the first card of the match be shown before the first goal is scored?",
        teams,
    ) == MarketSpec("first_card_before_first_goal", stat="cards")
    assert parse(
        "Will the first goal of the match be scored in the second half of regulation (90 minutes + stoppage time)?",
        teams,
    ) == MarketSpec("first_goal_game_2h", stat="goals", period="2h")
    assert parse(
        "Will a goal be scored after the second hydration break but before the end of regulation?",
        teams,
    ) == MarketSpec("goal_after_second_hydration", stat="goals", params={"minute_cut": 75})
    assert parse(
        "Will Diogo Costa (Portugal) make 4 or more saves in regulation (90 minutes + stoppage time)?",
        teams,
    ) == MarketSpec(
        "player_saves_ge",
        player="Diogo Costa",
        stat="saves",
        threshold=4,
        params={"player_team": "Portugal"},
    )
    assert parse(
        "Will there be 9 or more total substitutions (both teams combined) in regulation (90 minutes + stoppage time)?",
        teams,
    ) == MarketSpec("total_count_ge", stat="substitutions", threshold=9)
    assert parse("Will either team make a substitution at halftime?", teams) == MarketSpec(
        "substitution_before_halftime")
    assert parse("Will Haiti have at least 1 shot on target in the second half?", teams
                 ).family == "team_count_ge"
    assert parse("Will Son Heung-min have at least 1 shot on target?", teams
                 ).family == "player_sot_ge"
    assert parse("At halftime, will Czechia have more corner kicks than South Korea?", teams
                 ) == MarketSpec("team_more_count_than", team="Czechia",
                                 opponent="South Korea", stat="corners", period="1h")
    assert parse(
        "Will England have more corner kicks AND more total shots than Norway in regulation (90 minutes + stoppage time)?",
        teams,
    ) == MarketSpec(
        "team_more_corners_and_total_shots",
        team="England",
        opponent="Norway",
        stat="corners_shots",
    )
    assert parse(
        "Will an own goal be scored in regulation (90 minutes + stoppage time)?",
        teams,
    ) == MarketSpec("own_goal", stat="goals")
    assert parse(
        "Will a goal be scored from outside the penalty area in regulation (90 minutes + stoppage time)?",
        teams,
    ) == MarketSpec("outside_box_goal", stat="goals")
    assert parse(
        "Will a header goal be scored in regulation (90 minutes + stoppage time)?",
        teams,
    ) == MarketSpec("header_goal", stat="goals")
    assert parse(
        "Will any player score 2 or more goals in regulation (90 minutes + stoppage time)?",
        teams,
    ) == MarketSpec("any_player_goals_ge", stat="goals", threshold=2)
    assert parse(
        "Will DR Congo score a goal (excluding own goals) in regulation (90 minutes + stoppage time)?",
        teams,
    ) == MarketSpec("team_goals_ge", team="DR Congo", stat="goals", threshold=1)
    assert parse(
        "Will Cyle Larin score a goal (excluding own goals) in regulation (90 minutes + stoppage time)?",
        teams,
    ) == MarketSpec("player_scores", player="Cyle Larin")
    assert parse(
        "Will a substitute score a goal (excluding own goals) in regulation (90 minutes + stoppage time)?",
        teams,
    ) == MarketSpec("substitute_scores", stat="goals")
    first_other = parse(
        "Will the first goal of the match be scored by a player other than Lionel Messi and Mohamed Salah?",
        teams,
    )
    assert first_other.family == "first_goal_other_than_players"
    assert first_other.stat == "goals"
    assert first_other.params == {"excluded_players": ["Lionel Messi", "Mohamed Salah"]}


def test_family_coverage_summary():
    families = Counter(
        parse(m["question"], teams=ALL_TEAM_NAMES).family for m in SNAPSHOT["markets"]
    )
    print("\nFamily counts:", dict(families.most_common()))
    assert sum(families.values()) == len(SNAPSHOT["markets"])
