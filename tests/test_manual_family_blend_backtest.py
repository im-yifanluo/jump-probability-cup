import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from backtest.history import HistoryMarket  # noqa: E402
from manual_family_blend_backtest import (  # noqa: E402
    best_weight,
    blend_model_field,
    paired_delta,
    render,
    score_predictions,
    walk_forward_predictions,
)


def _row(
    order: int,
    model: float,
    field: float,
    outcome: int,
    *,
    match: str | None = None,
    question: str = "Will Brazil win in regulation (90 minutes + stoppage time)?",
) -> HistoryMarket:
    return HistoryMarket(
        version=2,
        order=order,
        match=match or f"M{order}",
        question=question,
        you=model,
        crowd=field,
        outcome=outcome,
        official_rbp=0.0,
        family="team_win",
    )


def test_blend_model_field_uses_model_weight():
    row = _row(1, 0.80, 0.40, 1)

    assert blend_model_field(row, 1.0) == 0.80
    assert blend_model_field(row, 0.0) == 0.40
    assert blend_model_field(row, 0.25) == 0.50


def test_best_weight_ties_toward_equal_blend():
    rows = [
        _row(1, 0.60, 0.60, 1),
        _row(2, 0.40, 0.40, 0),
    ]

    assert best_weight(rows, (0.0, 0.50, 1.0)) == 0.50


def test_walk_forward_uses_only_prior_orders_for_category_weight():
    rows = [
        _row(1, 0.90, 0.10, 1),
        _row(1, 0.80, 0.20, 1),
        _row(2, 0.85, 0.15, 1),
        _row(3, 0.20, 0.80, 1),
    ]

    preds = walk_forward_predictions(
        rows,
        min_train=3,
        min_category_train=3,
        weights=(0.0, 1.0),
    )

    assert len(preds) == 1
    assert preds[0].row.order == 3
    assert preds[0].weight_on_model == 1.0
    assert preds[0].source == "category"


def test_walk_forward_falls_back_to_global_when_category_is_sparse():
    sparse_question = "Will there be 4 or more total cards shown in regulation (90 minutes + stoppage time)?"
    rows = [
        _row(1, 0.90, 0.10, 1),
        _row(1, 0.80, 0.20, 1),
        _row(2, 0.85, 0.15, 1),
        _row(3, 0.20, 0.80, 1, question=sparse_question),
    ]

    preds = walk_forward_predictions(
        rows,
        min_train=3,
        min_category_train=3,
        weights=(0.0, 1.0),
    )

    assert len(preds) == 1
    assert preds[0].source == "global"
    assert preds[0].weight_on_model == 1.0


def test_paired_delta_scores_linear_pool_against_combined_policy(monkeypatch):
    rows = [
        _row(1, 0.10, 0.90, 1),
        _row(1, 0.20, 0.80, 1),
        _row(2, 0.15, 0.85, 1),
        _row(3, 0.20, 0.80, 1),
    ]

    monkeypatch.setattr("manual_family_blend_backtest.combined_prob", lambda row: 0.50)
    preds = walk_forward_predictions(
        rows,
        min_train=3,
        min_category_train=3,
        weights=(0.0, 1.0),
    )

    assert paired_delta(preds) > 0
    combined = score_predictions([pred.row for pred in preds], [pred.base for pred in preds])
    pooled = score_predictions([pred.row for pred in preds], [pred.blended for pred in preds])
    assert pooled["brier"] < combined["brier"]


def test_render_includes_read_only_and_promotion_gate(monkeypatch):
    rows = [
        _row(1, 0.90, 0.10, 1),
        _row(1, 0.80, 0.20, 1),
        _row(2, 0.85, 0.15, 1),
        _row(3, 0.20, 0.80, 1),
    ]
    monkeypatch.setattr("manual_family_blend_backtest.combined_prob", lambda row: 0.50)

    text = render(rows, min_train=3, min_category_train=3, trials=10, seed=1)

    assert "# Manual Family Blend Backtest" in text
    assert "`/predictions` was not read" in text
    assert "promotion_gate" in text
    assert "walkforward_category_linear_pool" in text
