import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from count_total_dependence_policy_replay import (  # noqa: E402
    TotalAlpha,
    decision,
    price_candidate,
    replay_rows,
)
from backtest.history import HistoryMarket  # noqa: E402
from questions.parser import parse  # noqa: E402
from questions.teams import ALL_TEAM_NAMES  # noqa: E402


class FakeCounts:
    def mean(self, stat, side, period):
        assert stat == "corners"
        factor = 0.5 if period == "2h" else 1.0
        return (5.0 if side == "home" else 4.0) * factor

    def p_total_count_ge(self, stat, threshold, period):
        return 0.25 if period == "match" else 0.35


def _ctx():
    return SimpleNamespace(counts=FakeCounts())


def _row(question, *, crowd=0.60, outcome=0):
    return HistoryMarket(
        version=2,
        order=1,
        match="A vs B",
        question=question,
        you=0.50,
        crowd=crowd,
        outcome=outcome,
        official_rbp=0.0,
    )


def test_price_candidate_applies_direct_total_only_inside_candidate_scope():
    alphas = {"corners": TotalAlpha("corners", alpha_total=0.20, source_rows=100, mean_total=9.0)}
    match_spec = parse("Will there be 9 or more total corner kicks?", teams=ALL_TEAM_NAMES)
    half_spec = parse("Will there be 9 or more total corner kicks in the second half?", teams=ALL_TEAM_NAMES)

    current, applied = price_candidate(match_spec, _ctx(), alphas, "current_independent")
    match_only, match_applied = price_candidate(match_spec, _ctx(), alphas, "direct_total_nb_match_only")
    half_match_only, half_applied = price_candidate(half_spec, _ctx(), alphas, "direct_total_nb_match_only")
    half_all, half_all_applied = price_candidate(half_spec, _ctx(), alphas, "direct_total_nb_all_periods")

    assert current == 0.25
    assert match_applied is True
    assert match_only != current
    assert half_match_only == 0.35
    assert half_applied is False
    assert half_all_applied is True
    assert half_all != 0.35


def test_replay_rows_applies_manual_field_policy_to_candidate_model():
    row = _row("Will there be 9 or more total corner kicks?", crowd=0.60, outcome=0)
    alphas = {"corners": TotalAlpha("corners", alpha_total=0.20, source_rows=100, mean_total=9.0)}

    replayed, skipped = replay_rows([row], {"A vs B": _ctx()}, alphas, candidate="direct_total_nb_match_only")

    assert skipped == []
    assert len(replayed) == 1
    assert replayed[0].applied_direct_total is True
    assert replayed[0].policy == "global_asym075_cap12"
    assert 0.01 <= replayed[0].manual_probability <= 0.99


def test_decision_requires_enough_rows_matches_and_positive_raw_support():
    boot = {
        "manual_probability": {
            "current_independent": {"point": 0, "ci_2.5": 0, "n": 10, "matches": 10},
            "direct_total_nb_match_only": {"point": 5, "ci_2.5": 1, "n": 10, "matches": 10},
        },
        "model_probability": {
            "current_independent": {"point": 0},
            "direct_total_nb_match_only": {"point": 5},
        },
    }

    assert decision(boot) == "not_promoted_keep_current_independent_total_count_shape"

    boot["manual_probability"]["direct_total_nb_match_only"].update({"n": 40, "matches": 20})
    assert decision(boot) == "promote_direct_total_nb_match_only_for_settled_total_count_thresholds"

    boot["model_probability"]["direct_total_nb_match_only"]["point"] = -0.01
    assert decision(boot) == "not_promoted_keep_current_independent_total_count_shape"
