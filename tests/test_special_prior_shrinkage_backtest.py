import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from special_prior_shrinkage_backtest import (  # noqa: E402
    ScoredPrediction,
    RAW_FACTOR,
    best_factor,
    bootstrap_delta,
    render,
    score,
    shrink_probability,
)


def _pred(family: str, p: float, outcome: bool, *, tournament: str = "a") -> ScoredPrediction:
    return ScoredPrediction(
        family=family,
        kind="binary",
        confidence="weak_near_coinflip",
        heterogeneity="moderate",
        tournament=tournament,
        base_probability=p,
        outcome=outcome,
    )


def test_shrink_probability_moves_prior_toward_coinflip():
    assert shrink_probability(0.80, 1.00) == 0.80
    assert shrink_probability(0.80, 0.50) == 0.65
    assert shrink_probability(0.20, 0.25) == 0.425
    assert shrink_probability(0.80, 0.00) == 0.50


def test_score_and_best_factor_prefer_raw_when_prior_is_informative():
    predictions = [
        _pred("late_card", 0.80, True),
        _pred("late_card", 0.75, True),
        _pred("late_card", 0.20, False, tournament="b"),
        _pred("late_card", 0.25, False, tournament="b"),
    ]

    assert score(predictions, RAW_FACTOR)["rbp"] > score(predictions, 0.50)["rbp"]
    assert best_factor(predictions) == RAW_FACTOR


def test_bootstrap_delta_keeps_uncertain_shrinkage_from_promotion():
    predictions = [
        _pred("weak_goal", 0.45, True, tournament="a"),
        _pred("weak_goal", 0.45, False, tournament="b"),
        _pred("weak_goal", 0.55, True, tournament="c"),
        _pred("weak_goal", 0.55, False, tournament="d"),
    ]

    boot = bootstrap_delta(predictions, factor=0.75, trials=200, seed=1)

    assert boot["ci_2.5"] <= 0


def test_render_documents_read_only_not_promoted_decision():
    predictions = [
        _pred("goal_before_30", 0.45, True, tournament="a"),
        _pred("goal_before_30", 0.45, False, tournament="b"),
        _pred("goal_after_75", 0.55, True, tournament="c"),
        _pred("goal_after_75", 0.55, False, tournament="d"),
    ]

    text = render(predictions, now=datetime(2026, 6, 29, 1, tzinfo=timezone.utc))

    assert "`/predictions` was not read" in text
    assert "promotion_decision: not_promoted" in text
    assert "Best-Factor Bootstrap" in text
    assert "Current action: do not apply automatic shrinkage" in text
