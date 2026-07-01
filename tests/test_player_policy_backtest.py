import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from backtest.history import HistoryMarket  # noqa: E402
from player_policy_backtest import bootstrap_strategy_diff  # noqa: E402


def _row(order: int, outcome: int) -> HistoryMarket:
    return HistoryMarket(
        version=2,
        order=order,
        match=f"M{order}",
        question="Will Example Player have at least 1 shot on target?",
        you=0.40,
        crowd=0.50,
        outcome=outcome,
        official_rbp=0.0,
        family="player_sot_ge",
        stat="sot",
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
