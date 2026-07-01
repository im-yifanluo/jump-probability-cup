import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from backtest.history import HistoryMarket  # noqa: E402
from penalty_red_policy_backtest import (  # noqa: E402
    bootstrap_strategy_diff,
    sensitivity_grid,
    sensitivity_summary,
)


def _row(order: int, outcome: int) -> HistoryMarket:
    return HistoryMarket(
        version=2,
        order=order,
        match=f"M{order}",
        question="Will a penalty kick be awarded OR a red card be shown in the match?",
        you=0.37,
        crowd=0.44,
        outcome=outcome,
        official_rbp=0.0,
        family="penalty_or_red",
        stat="",
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


def test_sensitivity_grid_scores_policy_across_cutoffs():
    rows = [
        _row(1, 0),
        _row(2, 0),
        _row(3, 0),
        _row(4, 0),
        _row(5, 1),
        _row(6, 0),
    ]

    grid = sensitivity_grid(
        rows,
        policy="global_asym075_cap12",
        min_trains=[2, 4],
    )
    summary = sensitivity_summary(grid, "global_asym075_cap12")

    assert len(grid) == 2
    assert {row["holdout_rows"] for row in grid} == {2, 4}
    assert summary["configs"] == 2
    assert summary["positive_configs"] <= summary["configs"]
