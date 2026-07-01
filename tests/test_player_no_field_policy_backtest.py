import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from backtest.history import HistoryMarket  # noqa: E402
from player_no_field_policy_backtest import (  # noqa: E402
    beta_mean,
    bucket_base_rates,
    bootstrap_policy_diff,
    chronological_predictions,
    family_base_rates,
    player_bucket,
    sensitivity_grid,
    sensitivity_summary,
    source_proxy_diagnostics,
)


def _row(
    order: int,
    outcome: int,
    *,
    family: str = "player_sot_ge",
    stat: str = "sot",
    you: float = 0.20,
    threshold: int = 1,
) -> HistoryMarket:
    return HistoryMarket(
        version=2,
        order=order,
        match=f"M{order}",
        question=(
            "Will Example Player have at least 1 shot on target?"
            if threshold == 1
            else f"Will Example Player have {threshold} or more shots on target in regulation (90 minutes + stoppage time)?"
        ),
        you=you,
        crowd=0.50,
        outcome=outcome,
        official_rbp=0.0,
        family=family,
        stat=stat,
        period="match",
    )


def test_beta_mean_smooths_empirical_hit_rate():
    rows = [_row(1, 1), _row(2, 0), _row(3, 1)]

    assert beta_mean(rows, prior=0.5, strength=2.0) == 0.6


def test_family_base_rates_fallback_to_global_smoothed_prior():
    rows = [
        _row(1, 1, family="player_scores", stat="goals"),
        _row(2, 1, family="player_scores", stat="goals"),
    ]

    rates = family_base_rates(rows, global_base=0.40, strength=2.0)

    assert rates["player_scores/goals"] == 0.70


def test_player_bucket_splits_sot_thresholds():
    assert player_bucket(_row(1, 1, threshold=1)) == "player_sot_ge/sot/ge1"
    assert player_bucket(_row(1, 1, threshold=2)) == "player_sot_ge/sot/ge2"


def test_bucket_base_rates_splits_threshold_history():
    rows = [
        _row(1, 1, threshold=1),
        _row(2, 1, threshold=1),
        _row(3, 0, threshold=2),
    ]

    rates = bucket_base_rates(rows, global_base=0.40, strength=2.0)

    assert rates["player_sot_ge/sot/ge1"] == 0.70
    assert rates["player_sot_ge/sot/ge2"] == 0.26666666666666666


def test_chronological_predictions_use_only_prior_rows():
    rows = [
        _row(1, 1),
        _row(2, 1),
        _row(3, 1),
        _row(4, 0, you=0.10),
    ]

    scored = chronological_predictions(rows, min_train=3, global_strength=2.0, family_strength=2.0)

    assert len(scored) == 1
    assert scored[0].row.order == 4
    assert scored[0].predictions["prior_global_base"] == 0.8
    assert scored[0].predictions["model50_global50"] == 0.45


def test_chronological_predictions_use_prior_bucket_rows_only():
    rows = [
        _row(1, 1, threshold=1),
        _row(2, 1, threshold=1),
        _row(3, 0, threshold=2),
        _row(4, 0, threshold=2, you=0.10),
    ]

    scored = chronological_predictions(rows, min_train=3, global_strength=2.0, family_strength=2.0)

    assert len(scored) == 1
    assert scored[0].row.order == 4
    assert scored[0].predictions["prior_bucket_base"] < scored[0].predictions["prior_family_base"]


def test_bootstrap_policy_diff_scores_fixed_walk_forward_predictions():
    rows = [
        _row(1, 1, you=0.20),
        _row(2, 1, you=0.20),
        _row(3, 1, you=0.20),
        _row(4, 1, you=0.20),
        _row(5, 0, you=0.80),
    ]
    scored = chronological_predictions(rows, min_train=3, global_strength=2.0, family_strength=2.0)

    result = bootstrap_policy_diff(
        scored,
        "prior_global_base",
        "raw_model",
        trials=25,
        seed=3,
    )

    assert result["median"] > 0
    assert result["pr_gt_0"] > 0.5


def test_sensitivity_grid_scores_fixed_policy_across_training_choices():
    rows = [
        _row(1, 1, you=0.20),
        _row(2, 1, you=0.20),
        _row(3, 0, you=0.80),
        _row(4, 0, you=0.80),
        _row(5, 0, you=0.80),
    ]

    grid = sensitivity_grid(
        rows,
        policy="prior_global_base",
        min_trains=[2, 3],
        global_strengths=[0.0, 2.0],
        family_strength=2.0,
    )
    summary = sensitivity_summary(grid, "prior_global_base")

    assert len(grid) == 4
    assert {row["holdout_rows"] for row in grid} == {2, 3}
    assert summary["configs"] == 4
    assert summary["positive_configs"] > 0


def test_source_proxy_diagnostics_groups_fixed_walk_forward_predictions():
    rows = [
        _row(1, 1, you=0.20),
        _row(2, 1, you=0.20),
        _row(3, 0, you=0.80),
        _row(4, 0, you=0.80),
    ]
    scored = chronological_predictions(rows, min_train=2, global_strength=2.0, family_strength=2.0)

    diagnostics = source_proxy_diagnostics(
        scored,
        "prior_global_base",
        source_lookup=lambda row: "prior" if row.order == 3 else "override",
    )
    by_source = {row["source_proxy"]: row for row in diagnostics}

    assert by_source["prior"]["n"] == 1
    assert by_source["override"]["n"] == 1
    assert by_source["prior"]["raw"] == 0.80
