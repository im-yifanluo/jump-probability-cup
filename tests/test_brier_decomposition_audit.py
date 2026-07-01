import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from backtest.history import HistoryMarket  # noqa: E402
from brier_decomposition_audit import (  # noqa: E402
    bucket_rows,
    category_decompositions,
    decomposition,
    render,
)


def _row(order: int, p: float, outcome: int, question: str | None = None) -> HistoryMarket:
    return HistoryMarket(
        version=2,
        order=order,
        match=f"M{order}",
        question=question or "Will Brazil win in regulation (90 minutes + stoppage time)?",
        you=p,
        crowd=0.50,
        outcome=outcome,
        official_rbp=0.0,
        family="team_win",
    )


def test_decomposition_returns_murphy_components_for_exact_probability_groups():
    rows = [
        _row(1, 0.2, 0),
        _row(2, 0.2, 1),
        _row(3, 0.8, 1),
        _row(4, 0.8, 1),
    ]
    summary = decomposition(rows, [0.2, 0.2, 0.8, 0.8])

    assert summary["n"] == 4
    assert abs(summary["hit"] - 0.75) < 1e-12
    assert abs(summary["uncertainty"] - 0.1875) < 1e-12
    assert abs(summary["reliability"] - 0.065) < 1e-12
    assert abs(summary["resolution"] - 0.0625) < 1e-12
    assert abs(summary["decomposed_brier"] - 0.19) < 1e-12
    assert abs(summary["brier"] - summary["decomposed_brier"]) < 1e-12


def test_bucket_rows_report_weighted_reliability_and_resolution_components():
    rows = [
        _row(1, 0.1, 0),
        _row(2, 0.3, 1),
        _row(3, 0.3, 1),
    ]
    buckets = bucket_rows(rows, [0.1, 0.3, 0.3])

    assert [row.bucket for row in buckets] == ["0.00-0.20", "0.20-0.35"]
    assert buckets[0].n == 1
    assert buckets[0].mean_p == 0.1
    assert buckets[0].hit == 0.0
    assert abs(buckets[0].reliability_component - ((1 / 3) * 0.01)) < 1e-12
    assert buckets[1].n == 2
    assert buckets[1].hit == 1.0
    assert buckets[1].resolution_component < buckets[0].resolution_component


def test_category_decompositions_filters_small_categories():
    rows = [
        _row(1, 0.60, 1, "Will Brazil win in regulation (90 minutes + stoppage time)?"),
        _row(2, 0.60, 0, "Will Brazil win in regulation (90 minutes + stoppage time)?"),
        _row(3, 0.60, 1, "Will there be 4 or more total cards shown in regulation (90 minutes + stoppage time)?"),
    ]

    cats = category_decompositions(rows, min_rows=2)

    assert [row["category"] for row in cats] == ["odds_goal"]


def test_render_marks_report_as_diagnostic_only():
    rows = [
        _row(1, 0.60, 1),
        _row(2, 0.60, 0),
        _row(3, 0.40, 0),
        _row(4, 0.40, 1),
    ]

    text = render(rows, min_category_rows=1)

    assert "# Brier Decomposition Audit" in text
    assert "decision: diagnostic_only_no_probability_transform" in text
    assert "bot_predictions: not_read" in text
    assert "## Policy Decomposition" in text
    assert "combined_manual_policy" in text
