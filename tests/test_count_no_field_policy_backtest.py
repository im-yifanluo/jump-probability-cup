import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from backtest.history import HistoryMarket  # noqa: E402
from count_no_field_policy_backtest import (  # noqa: E402
    beta_mean,
    bootstrap_policy_diff,
    chronological_predictions,
    grouped_bases,
    is_card_threshold,
    is_strong_sot,
    policy_predictions,
    sensitivity_grid,
    sensitivity_summary,
)


def _row(
    order: int,
    outcome: int,
    *,
    family: str = "team_count_ge",
    stat: str = "sot",
    you: float = 0.20,
) -> HistoryMarket:
    return HistoryMarket(
        version=2,
        order=order,
        match=f"M{order}",
        question="Will Example Team have 5 or more shots on target?",
        you=you,
        crowd=0.50,
        outcome=outcome,
        official_rbp=0.0,
        family=family,
        stat=stat,
        period="match",
    )


def test_beta_mean_smooths_count_hit_rate():
    rows = [_row(1, 1), _row(2, 0), _row(3, 1)]

    assert beta_mean(rows, prior=0.5, strength=2.0) == 0.6


def test_grouped_bases_use_supplied_key_and_fallback():
    rows = [
        _row(1, 1, stat="cards"),
        _row(2, 1, stat="cards"),
        _row(3, 0, stat="sot"),
    ]

    rates = grouped_bases(rows, lambda row: row.stat, fallback=0.40, strength=2.0)

    assert rates["cards"] == 0.70
    assert rates["sot"] == 0.26666666666666666


def test_chronological_predictions_keep_strong_sot_raw_for_mixed_policy():
    rows = [
        _row(1, 1, stat="cards", family="total_count_ge"),
        _row(2, 0, stat="cards", family="total_count_ge"),
        _row(3, 1, stat="sot", family="team_count_ge"),
        _row(4, 1, stat="sot", family="team_count_ge", you=0.61),
    ]

    scored = chronological_predictions(
        rows,
        min_train=3,
        global_strength=2.0,
        stat_strength=2.0,
        family_strength=2.0,
    )

    assert len(scored) == 1
    assert is_strong_sot(scored[0].row)
    assert scored[0].predictions["raw_strong_sot_else_stat_base"] == 0.61


def test_card_threshold_split_excludes_card_head_to_head_rows():
    assert is_card_threshold(_row(1, 1, stat="cards", family="total_count_ge"))
    assert is_card_threshold(_row(1, 1, stat="cards", family="team_count_ge"))
    assert is_card_threshold(_row(1, 1, stat="cards", family="both_teams_count_ge"))
    assert not is_card_threshold(_row(1, 1, stat="cards", family="team_more_count_than"))
    assert not is_card_threshold(_row(1, 1, stat="corners", family="total_count_ge"))


def test_policy_predictions_apply_card_threshold_base_only_to_threshold_rows():
    threshold = _row(1, 1, stat="cards", family="total_count_ge", you=0.22)
    head_to_head = _row(2, 1, stat="cards", family="team_more_count_than", you=0.22)
    non_card = _row(3, 1, stat="corners", family="total_count_ge", you=0.22)

    threshold_predictions = policy_predictions(
        threshold,
        global_base=0.50,
        stat_base=0.31,
        family_base=0.67,
    )
    head_to_head_predictions = policy_predictions(
        head_to_head,
        global_base=0.50,
        stat_base=0.31,
        family_base=0.67,
    )
    non_card_predictions = policy_predictions(
        non_card,
        global_base=0.50,
        stat_base=0.31,
        family_base=0.67,
    )

    assert threshold_predictions["card_threshold_family_base_else_raw"] == 0.67
    assert threshold_predictions["card_threshold_stat_base_else_raw"] == 0.31
    assert head_to_head_predictions["card_threshold_family_base_else_raw"] == 0.22
    assert head_to_head_predictions["card_threshold_stat_base_else_raw"] == 0.22
    assert non_card_predictions["card_threshold_family_base_else_raw"] == 0.22
    assert non_card_predictions["card_threshold_stat_base_else_raw"] == 0.22


def test_bootstrap_policy_diff_scores_left_minus_right():
    rows = [
        _row(1, 1, stat="cards", family="total_count_ge", you=0.20),
        _row(2, 1, stat="cards", family="total_count_ge", you=0.20),
        _row(3, 1, stat="cards", family="total_count_ge", you=0.20),
        _row(4, 1, stat="cards", family="total_count_ge", you=0.20),
        _row(5, 0, stat="cards", family="total_count_ge", you=0.80),
    ]
    scored = chronological_predictions(
        rows,
        min_train=3,
        global_strength=2.0,
        stat_strength=2.0,
        family_strength=2.0,
    )

    result = bootstrap_policy_diff(
        scored,
        "prior_stat_base",
        "raw_model",
        trials=25,
        seed=3,
    )

    assert result["median"] > 0
    assert result["pr_gt_0"] > 0.5


def test_sensitivity_grid_scores_fixed_count_policy_across_choices():
    rows = [
        _row(1, 1, stat="cards", family="total_count_ge", you=0.20),
        _row(2, 1, stat="cards", family="total_count_ge", you=0.20),
        _row(3, 0, stat="cards", family="total_count_ge", you=0.80),
        _row(4, 0, stat="cards", family="total_count_ge", you=0.80),
        _row(5, 1, stat="sot", family="team_count_ge", you=0.45),
    ]

    grid = sensitivity_grid(
        rows,
        policy="card_threshold_stat_base_else_raw",
        min_trains=[2, 3],
        stat_strengths=[0.0, 2.0],
        global_strength=2.0,
        family_strength=2.0,
    )
    summary = sensitivity_summary(grid, "card_threshold_stat_base_else_raw")

    assert len(grid) == 4
    assert {row["holdout_rows"] for row in grid} == {2, 3}
    assert summary["configs"] == 4
    assert summary["positive_configs"] <= summary["configs"]
