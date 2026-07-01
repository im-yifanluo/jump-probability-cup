import sys
from pathlib import Path

import numpy as np
from scipy import stats


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from count_total_dependence_audit import (  # noqa: E402
    ScoredRow,
    analyze,
    bootstrap_comparison,
    p_independent_sum_ge,
)


def test_independent_sum_poisson_matches_total_poisson_survival():
    home = np.array([1.0, 2.5, 4.0])
    away = np.array([0.7, 1.5, 2.0])

    independent = p_independent_sum_ge(home, away, alpha=0.0, threshold=4)
    direct = stats.poisson.sf(3, home + away)

    assert np.allclose(independent, direct, atol=1e-10)


def test_bootstrap_comparison_uses_match_blocks():
    scored = []
    for match_idx in range(6):
        outcome = int(match_idx >= 3)
        current_p = 0.55 if outcome else 0.45
        candidate_p = 0.85 if outcome else 0.15
        for method, probability in {
            "current_independent_team_nb": current_p,
            "direct_total_nb": candidate_p,
        }.items():
            scored.append(
                ScoredRow(
                    season="2526",
                    match_key=f"m{match_idx}",
                    stat="corners",
                    threshold=9,
                    method=method,
                    probability=probability,
                    outcome=outcome,
                    brier=(probability - outcome) ** 2,
                )
            )

    boot = bootstrap_comparison(scored, "direct_total_nb", n=200, seed=1)

    assert boot["point_delta_x100"] > 0
    assert boot["pr_gt_0"] == 1.0


def test_analyze_gate_keeps_non_best_direct_total_nb_unpromoted(monkeypatch):
    def fake_replay(_matches):
        scored = []
        for match_idx, outcome in enumerate([0, 1, 0, 1]):
            for method, probability in {
                "current_independent_team_nb": 0.25 if outcome == 0 else 0.75,
                "direct_total_poisson": 0.30 if outcome == 0 else 0.70,
                "direct_total_nb": 0.35 if outcome == 0 else 0.65,
                "empirical_threshold_prior": 0.50,
            }.items():
                scored.append(
                    ScoredRow(
                        season="2324",
                        match_key=f"m{match_idx}",
                        stat="cards",
                        threshold=4,
                        method=method,
                        probability=probability,
                        outcome=outcome,
                        brier=(probability - outcome) ** 2,
                    )
                )
        return scored

    monkeypatch.setattr("count_total_dependence_audit.replay", fake_replay)
    monkeypatch.setattr("count_total_dependence_audit.add_implied_lambdas", lambda _matches: [1, 2, 3, 4])
    analysis = analyze(matches=object(), bootstrap_n=100)

    assert analysis["decision"] == "not_promoted_keep_independent_team_convolution"
