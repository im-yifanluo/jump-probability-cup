import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from backtest.history import HistoryMarket  # noqa: E402
from goal_policy_backtest import bootstrap_strategy_diff, sensitivity_grid, sensitivity_summary  # noqa: E402


def _row(order: int, outcome: int) -> HistoryMarket:
    return HistoryMarket(
        version=2,
        order=order,
        match=f"M{order}",
        question="Will regulation (90 minutes + stoppage time) end in a tie?",
        you=0.40,
        crowd=0.50,
        outcome=outcome,
        official_rbp=0.0,
        family="match_drawn",
        stat="goals",
        period="match",
    )


def test_bootstrap_strategy_diff_scores_left_minus_right():
    rows = [_row(1, 1), _row(2, 1), _row(3, 0)]

    result = bootstrap_strategy_diff(
        rows,
        lambda _row: 0.80,
        lambda _row: 0.20,
        trials=25,
        seed=1,
    )

    assert result["median"] > 0
    assert result["pr_gt_0"] > 0.5


def test_sensitivity_grid_scores_raw_goal_policy_across_cutoffs():
    rows = [
        _row(1, 1),
        _row(2, 1),
        _row(3, 0),
        _row(4, 1),
        _row(5, 0),
    ]

    grid = sensitivity_grid(rows, min_trains=[2, 3])
    summary = sensitivity_summary(grid)

    assert len(grid) == 2
    assert {row["holdout_rows"] for row in grid} == {2, 3}
    assert summary["configs"] == 2
    assert summary["raw_beats_field_configs"] <= summary["configs"]
