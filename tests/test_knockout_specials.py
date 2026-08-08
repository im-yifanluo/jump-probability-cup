import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from models.goals import ScorelineModel
from models.pricer import MatchPricer
from models.specials import SpecialBaselines, SpecialPricer
from questions.parser import parse
from special_market_backtest import (
    adaptation_score,
    calibration_bins,
    calibration_summary,
    chronological_adaptation_predictions,
    confidence_tier,
    ex_ante_volume_replay_status,
    heterogeneity_tier,
    loto_threshold,
    tournament_heterogeneity_rows,
    wilson_interval,
)


class DummyCounts:
    def __init__(self, means):
        self.means = means

    def mean(self, stat, side, period="match"):
        return self.means[(stat, side)]


def baselines():
    return SpecialBaselines(
        n_matches=10,
        rates={
            "goal_before_30": 0.40,
            "goal_after_30_1h": 0.35,
            "goal_after_75": 0.30,
            "goal_1h_stoppage": 0.10,
            "goal_2h_stoppage": 0.20,
            "offside_before_30": 0.50,
            "card_after_75": 0.45,
            "card_stoppage": 0.36,
            "substitution_before_halftime": 0.15,
            "substitute_goal": 0.25,
            "own_goal": 0.12,
            "outside_box_goal": 0.24,
            "header_goal": 0.33,
            "any_player_goal": 0.89,
            "any_player_brace": 0.12,
            "any_player_2_sot": 0.70,
        },
        means={
            "goals": 2.5,
            "shots": 23.0,
            "sot": 7.5,
            "offsides": 3.0,
            "cards": 3.5,
            "corners": 9.0,
            "corners_before_30": 3.0,
        },
        shots_per_sot=3.0,
        total_shots_alpha=0.05,
        early_corner_share=1 / 3,
        early_corners_alpha=0.10,
        extra_time_goal_factor=0.80,
        extra_time_card_factor=1.10,
        source="test",
    )


def test_knockout_question_templates_parse():
    specs = {
        "Will Japan advance to the Round of 16?": ("team_advance", "Japan", None),
        "Will Japan advance to the quarterfinals?": ("team_advance", "Japan", None),
        "Will exactly 1 goal be scored in regulation (90 minutes + stoppage time)?": ("total_goals", None, "goals"),
        "Will the match be decided by a penalty shootout?": ("penalty_shootout", None, "goals"),
        "Will the match go to extra time?": ("match_goes_to_extra_time", None, "goals"),
        "Will the match be 0-0 at halftime?": ("ht_score_exact", None, "goals"),
        "Will Japan hold a lead at any point in the match (excluding a penalty shootout)?": ("team_leads_any_time", "Japan", "goals"),
        "Will Belgium hold a lead at any point in regulation (90 minutes + stoppage time, excluding penalty shootout)?": ("team_leads_any_time", "Belgium", "goals"),
        "Will there be more total cards than total goals in regulation (90 minutes + stoppage time)?": ("total_cards_more_than_goals", None, "cards"),
        "Will Christian Pulisic (United States) play the entire match in regulation (90 minutes + stoppage time)?": ("player_full_match", None, "minutes"),
        "Will at least one goal be scored in each half in regulation (90 minutes + stoppage time)?": ("goal_each_half", None, "goals"),
        "Will either team win both halves in regulation (90 minutes + stoppage time)?": ("either_team_wins_both_halves", None, "goals"),
        "Will the match be decided by exactly one goal in regulation (90 minutes + stoppage time)?": ("match_decided_by_exactly_one_goal", None, "goals"),
        "Will at least one card be shown in each half of regulation (90 minutes + stoppage time)?": ("total_count_each_half_ge", None, "cards"),
        "Will there be 4 or more total cards in regulation (90 minutes + stoppage time)?": ("total_count_ge", None, "cards"),
        "Will both halves have the same number of goals in regulation (90 minutes + stoppage time)?": ("halves_same_goals", None, "goals"),
        "Will the first goal of the match be scored in the second half of regulation (90 minutes + stoppage time)?": ("first_goal_game_2h", None, "goals"),
        "Will a goal be scored before the first hydration break?": ("goal_before_hydration", None, "goals"),
        "Will a goal be scored in the first half after the first hydration break?": ("goal_after_first_hydration_1h", None, "goals"),
        "Will a goal be scored after the second hydration break but before the end of regulation?": ("goal_after_second_hydration", None, "goals"),
        "Will either team be ruled offside before the first hydration break?": ("offside_before_hydration", None, "offsides"),
        "Will a card be shown after the second hydration break, including any extra time?": ("card_after_second_hydration", None, "cards"),
        "Will a card be shown during first- or second-half stoppage time in regulation?": ("card_in_stoppage_any_half", None, "cards"),
        "Will the first card of the match be shown before the first goal is scored?": ("first_card_before_first_goal", None, "cards"),
        "Will a substitute score a goal in regulation (90 minutes + stoppage time)?": ("substitute_scores", None, "goals"),
        "Will an own goal be scored in regulation (90 minutes + stoppage time)?": ("own_goal", None, "goals"),
        "Will a goal be scored from outside the penalty area in regulation (90 minutes + stoppage time)?": ("outside_box_goal", None, "goals"),
        "Will a header goal be scored in regulation (90 minutes + stoppage time)?": ("header_goal", None, "goals"),
        "Will any player score more than 1 goal (excluding own goals) in regulation (90 minutes + stoppage time)?": ("any_player_goals_ge", None, "goals"),
        "Will any player score 2 or more goals (excluding own goals) in regulation (90 minutes + stoppage time)?": ("any_player_goals_ge", None, "goals"),
        "Will any player score 2 or more goals in regulation (90 minutes + stoppage time)?": ("any_player_goals_ge", None, "goals"),
        "Will any player score at least 1 goal in regulation (90 minutes + stoppage time)?": ("any_player_goals_ge", None, "goals"),
        "Will any player record 2 or more shots on target in regulation (90 minutes + stoppage time)?": ("any_player_sot_ge", None, "sot"),
        "Will there be 22 or more total shots (on and off target) in regulation (90 minutes + stoppage time)?": ("total_count_ge", None, "shots"),
        "Will there be 9 or more total substitutions (both teams combined) in regulation (90 minutes + stoppage time)?": ("total_count_ge", None, "substitutions"),
        "Will 2 or more corner kicks be taken before the first hydration break?": ("total_count_before_hydration", None, "corners"),
        "Will a substitution be made before halftime?": ("substitution_before_halftime", None, None),
        "Will either team make a substitution at halftime?": ("substitution_before_halftime", None, None),
        "Will a goal be scored in first-half stoppage (added) time?": ("goal_in_stoppage", None, "goals"),
        "Will England have more corner kicks AND more total shots than Norway in regulation (90 minutes + stoppage time)?": ("team_more_corners_and_total_shots", "England", "corners_shots"),
        "Will Argentina keep a clean sheet in regulation (90 minutes + stoppage time)?": ("team_clean_sheet", "Argentina", "goals"),
        "Will the United States have 6 or more shots on target in regulation (90 minutes + stoppage time)?": ("team_count_ge", "United States", "sot"),
    }
    teams = {"Japan", "Brazil", "Argentina", "United States"}
    for question, (family, team, stat) in specs.items():
        spec = parse(question, teams=teams)
        assert spec.family == family
        assert spec.team == team
        assert spec.stat == stat


def test_special_pricer_scales_timing_props_with_match_volume():
    goals = ScorelineModel(1.25, 1.25)
    counts = DummyCounts(
        {
            ("sot", "home"): 4.0,
            ("sot", "away"): 3.5,
            ("offsides", "home"): 1.5,
            ("offsides", "away"): 1.5,
            ("cards", "home"): 1.7,
            ("cards", "away"): 1.8,
            ("corners", "home"): 4.5,
            ("corners", "away"): 4.5,
        }
    )
    pricer = SpecialPricer(goals, counts, baselines())
    assert round(pricer.p_goal_before_hydration(), 3) == 0.400
    assert round(pricer.p_goal_after_first_hydration_1h(), 3) == 0.350
    assert round(pricer.p_offside_before_hydration(), 3) == 0.500
    assert round(pricer.p_card_in_stoppage_any_half(), 3) == 0.360
    assert round(pricer.p_substitution_before_halftime(), 3) == 0.150
    assert round(pricer.p_own_goal(), 3) == 0.120
    assert round(pricer.p_outside_box_goal(), 3) == 0.240
    assert round(pricer.p_header_goal(), 3) == 0.330
    assert round(pricer.p_any_player_goals_ge(1), 3) == 0.890
    assert 0 < pricer.p_total_shots_ge(22) < 1
    assert 0 < pricer.p_advance("home") < 1


def test_parenthetical_team_is_not_part_of_player_name():
    spec = parse(
        "Will Kylian Mbappé (France) have 2 or more shots on target in regulation (90 minutes + stoppage time)?",
        teams={"France", "Sweden"},
    )
    assert spec.family == "player_sot_ge"
    assert spec.player == "Kylian Mbappé"
    assert spec.params["player_team"] == "France"

    scorer = parse(
        "Will Kai Havertz (Germany) score a goal (excluding own goals) in regulation (90 minutes + stoppage time)?",
        teams={"Germany", "Paraguay"},
    )
    assert scorer.family == "player_scores"
    assert scorer.player == "Kai Havertz"
    assert scorer.params["player_team"] == "Germany"


class DummyPlayers:
    def team_of(self, player):
        return None


def test_player_side_uses_parenthetical_team_hint_when_history_is_missing():
    pricer = MatchPricer("Ivory Coast", "Norway", None, None, DummyPlayers())

    assert pricer._player_side("Erling Haaland", "Norway") == "away"
    assert pricer._player_side("Amad Diallo", "Ivory Coast") == "home"
    assert pricer._player_side("Ermedin Demirović") is None


def test_exact_halftime_score_routes_to_scoreline_model():
    goals = ScorelineModel(1.8, 0.77)
    pricer = MatchPricer("France", "Morocco", goals, None, DummyPlayers())
    spec = parse("Will the match be 0-0 at halftime?", teams={"France", "Morocco"})

    assert pricer.price(spec) == goals.p_ht_score_exact(0, 0)


def test_special_backtest_uncertainty_tiers_mark_near_coinflip_edges_weak():
    low, high = wilson_interval(138, 314)

    assert 0.38 < low < 0.40
    assert 0.49 < high < 0.50
    assert confidence_tier(0.0007, low, high) == "weak_near_coinflip"
    assert confidence_tier(0.05, 0.20, 0.35) == "usable_base_prior"


def test_special_backtest_calibration_bins_and_summary():
    scored = [(0.10, False), (0.15, True), (0.45, True), (0.95, True)]

    bins = calibration_bins(scored)
    summary = calibration_summary(scored)

    assert bins[0]["bin"] == "0.0-0.2"
    assert bins[0]["n"] == 2
    assert bins[0]["mean_pred"] == 0.125
    assert bins[0]["hit_rate"] == 0.5
    assert bins[1]["bin"] == "0.4-0.6"
    assert bins[1]["hit_rate"] == 1.0
    assert summary["n"] == 4
    assert abs(summary["mean_pred"] - 0.4125) < 1e-12
    assert summary["hit_rate"] == 0.75
    assert abs(summary["ece"] - 0.3375) < 1e-12


def test_special_threshold_loto_scores_count_questions_without_match_leakage():
    rows = [
        {"tournament": "a", "shots": 18},
        {"tournament": "a", "shots": 24},
        {"tournament": "b", "shots": 23},
        {"tournament": "b", "shots": 25},
    ]

    out = loto_threshold(rows, "shots", 22)

    assert out["n"] == 4
    assert out["successes"] == 3
    assert out["hit"] == 0.75
    assert out["brier_loto"] > 0


def test_special_ex_ante_volume_replay_reports_missing_projection_data():
    status = ex_ante_volume_replay_status([{"goals": 2}, {"goals": 3}])

    assert status["status"] == "not_available"
    assert status["matches"] == 2
    assert "no historical pre-match odds" in status["reason"]


def test_tournament_heterogeneity_rows_flag_rate_spread():
    rows = [
        {"tournament": "a", "goal_before_30": True, "shots": 25, "corners_before_30": 2},
        {"tournament": "a", "goal_before_30": True, "shots": 24, "corners_before_30": 1},
        {"tournament": "b", "goal_before_30": False, "shots": 18, "corners_before_30": 0},
        {"tournament": "b", "goal_before_30": False, "shots": 19, "corners_before_30": 0},
    ]
    for key in [
        "goal_after_30_1h",
        "goal_after_75",
        "goal_1h_stoppage",
        "goal_2h_stoppage",
        "offside_before_30",
        "card_after_75",
        "card_stoppage",
        "first_card_before_first_goal",
        "substitution_before_halftime",
        "substitute_goal",
        "own_goal",
        "outside_box_goal",
        "header_goal",
        "any_player_goal",
        "any_player_brace",
        "any_player_2_sot",
    ]:
        for row in rows:
            row[key] = False
    for row in rows:
        row["substitutions"] = 8

    out = {row["key"]: row for row in tournament_heterogeneity_rows(rows)}

    assert out["goal_before_30"]["range"] == 1.0
    assert out["goal_before_30"]["heterogeneity"] == "high"
    assert out["total_shots_ge_20"]["heterogeneity"] == "high"
    assert heterogeneity_tier(0.14) == "low"
    assert heterogeneity_tier(0.15) == "moderate"
    assert heterogeneity_tier(0.25) == "high"


def _adapt_row(tournament, match_id, kickoff, outcome, *, stage="Group Stage"):
    return {
        "tournament": tournament,
        "match_id": match_id,
        "match_date": "2024-01-01",
        "kick_off": kickoff,
        "stage": stage,
        "is_knockout": stage != "Group Stage",
        "goal_before_30": outcome,
    }


def test_chronological_adaptation_does_not_leak_simultaneous_matches():
    rows = [
        _adapt_row("heldout", 1, "10:00:00.000", True),
        _adapt_row("heldout", 2, "10:00:00.000", True),
        _adapt_row("heldout", 3, "12:00:00.000", False),
        _adapt_row("train", 4, "10:00:00.000", True),
        _adapt_row("train", 5, "12:00:00.000", False),
    ]

    preds = chronological_adaptation_predictions(rows, ["goal_before_30"], strength=2)
    heldout = [row for row in preds if row["tournament"] == "heldout"]

    assert [row["adaptive"] for row in heldout] == [0.5, 0.5, 0.75]
    assert adaptation_score(heldout)["n"] == 3


def test_chronological_adaptation_scores_only_knockout_when_requested():
    rows = [
        _adapt_row("heldout", 1, "10:00:00.000", True),
        _adapt_row("heldout", 2, "12:00:00.000", True),
        _adapt_row("heldout", 3, "14:00:00.000", False, stage="Round of 16"),
        _adapt_row("train", 4, "10:00:00.000", True),
        _adapt_row("train", 5, "12:00:00.000", False),
    ]

    preds = chronological_adaptation_predictions(
        rows,
        ["goal_before_30"],
        strength=2,
        knockout_only=True,
    )
    heldout = [row for row in preds if row["tournament"] == "heldout"]

    assert len(heldout) == 1
    assert heldout[0]["is_knockout"] is True
    assert heldout[0]["adaptive"] == 0.75
