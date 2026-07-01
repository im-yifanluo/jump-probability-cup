import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from backtest.history import HistoryMarket  # noqa: E402
from manual_calibration_backtest import (  # noqa: E402
    best_factor,
    calibrate_probability,
    paired_delta,
    reliability_rows,
    reliability_summary,
    score_predictions,
    walk_forward_predictions,
)


def _row(order: int, p: float, outcome: int, match: str | None = None) -> HistoryMarket:
    return HistoryMarket(
        version=2,
        order=order,
        match=match or f"M{order}",
        question="Will Brazil win in regulation (90 minutes + stoppage time)?",
        you=p,
        crowd=0.50,
        outcome=outcome,
        official_rbp=0.0,
        family="team_win",
    )


def test_calibrate_probability_scales_distance_from_half():
    assert calibrate_probability(0.70, 0.50) == 0.60
    assert calibrate_probability(0.30, 0.50) == 0.40
    assert calibrate_probability(0.99, 1.20) <= 0.99


def test_best_factor_prefers_shrinkage_for_overconfident_training_rows():
    rows = [
        _row(1, 0.90, 0),
        _row(1, 0.85, 0),
        _row(2, 0.80, 0),
    ]

    assert best_factor(rows, (0.70, 1.00, 1.20)) == 0.70


def test_walk_forward_predictions_use_only_prior_orders():
    rows = [
        _row(1, 0.90, 0),
        _row(1, 0.85, 0),
        _row(2, 0.80, 0),
        _row(3, 0.80, 1),
    ]

    preds = walk_forward_predictions(rows, min_train=3, factors=(0.70, 1.00))

    assert len(preds) == 1
    assert preds[0].row.order == 3
    assert preds[0].factor == 0.70
    assert preds[0].base == 0.80
    assert preds[0].calibrated == 0.71


def test_paired_delta_scores_calibrated_against_base():
    rows = [
        _row(1, 0.90, 0),
        _row(1, 0.85, 0),
        _row(2, 0.80, 0),
        _row(3, 0.80, 0),
    ]
    preds = walk_forward_predictions(rows, min_train=3, factors=(0.70, 1.00))

    assert paired_delta(preds) > 0
    base = score_predictions([pred.row for pred in preds], [pred.base for pred in preds])
    calibrated = score_predictions([pred.row for pred in preds], [pred.calibrated for pred in preds])
    assert calibrated["brier"] < base["brier"]


def test_reliability_rows_bucket_supplied_probabilities():
    rows = [
        _row(1, 0.90, 0),
        _row(2, 0.90, 1),
        _row(3, 0.90, 1),
    ]

    buckets = reliability_rows(rows, [0.10, 0.30, 0.31])
    summary = reliability_summary(rows, [0.10, 0.30, 0.31])

    assert buckets[0]["bucket"] == "0.00-0.20"
    assert buckets[0]["n"] == 1
    assert buckets[0]["mean_p"] == 0.10
    assert buckets[0]["hit"] == 0.0
    assert buckets[1]["bucket"] == "0.20-0.35"
    assert buckets[1]["n"] == 2
    assert abs(buckets[1]["mean_p"] - 0.305) < 1e-12
    assert buckets[1]["hit"] == 1.0
    assert summary["n"] == 3
    assert abs(summary["ece"] - ((0.1 * 1) + (0.695 * 2)) / 3) < 1e-12
