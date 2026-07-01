import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from special_count_threshold_audit import analyze, score_thresholds  # noqa: E402
from special_market_backtest import rows as statsbomb_rows  # noqa: E402


def _fixture_rows():
    rows = []
    for tournament, values in {
        "low": [17, 18, 19, 18, 17, 19],
        "high": [23, 24, 25, 24, 23, 25],
    }.items():
        for idx, shots in enumerate(values):
            rows.append(
                {
                    "tournament": tournament,
                    "match_id": idx,
                    "match_date": f"2026-01-{idx + 1:02d}",
                    "kick_off": "12:00:00.000",
                    "shots": shots,
                    "corners_before_30": 3 if shots >= 20 else 1,
                }
            )
    return rows


def test_score_thresholds_scores_every_method_for_fixture():
    scored = score_thresholds(_fixture_rows())

    methods = {row["method"] for row in scored}
    assert "base_bernoulli" in methods
    assert "nb_count_prior" in methods
    assert "adaptive_nb_mean_s64" in methods
    assert {row["key"] for row in scored} == {"total_shots_ge_20", "total_shots_ge_22", "early_corners_ge_2"}


def test_real_statsbomb_count_threshold_audit_stays_proxy_only_positive():
    analysis = analyze(statsbomb_rows())

    assert analysis["decision"] == "proxy_candidate_count_threshold_adaptation_positive_not_live_promoted"
    assert analysis["matches"] == 314
    assert analysis["tournaments"] == 6
    assert analysis["scored_rows"] == 942
    boot = analysis["bootstraps"]["adaptive_nb_mean_s64"]
    assert boot["ci_2.5"] > 0
