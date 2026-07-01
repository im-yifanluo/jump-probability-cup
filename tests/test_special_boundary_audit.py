import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from special_boundary_audit import build_boundary, render  # noqa: E402


def _write(path: Path, text: str) -> None:
    path.write_text(textwrap.dedent(text).strip() + "\n", encoding="utf-8")


def test_special_boundary_parses_live_and_policy_logs(tmp_path):
    _write(
        tmp_path / "special_market_live_audit.md",
        """
        # Special Market Live Audit

        - run_utc: 2026-06-29T00:00:00+00:00
        - special_rows: 3

        ## Risk Summary

        | risk_state | rows |
        | --- | ---: |
        | WEAK_PRIOR | 1 |
        | COUNT_THRESHOLD_SCALED_FAR_FROM_BASE | 1 |
        | BASE_PRIOR | 1 |

        | confidence | rows |
        | --- | ---: |
        | weak_near_coinflip | 1 |
        | usable_base_prior | 2 |

        ## Live Special Rows

        | match | entry | risk_state | confidence | heterogeneity | evidence_key | scaled_shift_pp | action | question |
        | --- | ---: | --- | --- | --- | --- | ---: | --- | --- |
        | A vs B | 44% | WEAK_PRIOR | weak_near_coinflip | moderate | goal_before_30 | 4 | compare source | Will a goal be scored before the first hydration break? |
        | C vs D | 33% | COUNT_THRESHOLD_SCALED_FAR_FROM_BASE | usable_base_prior | high | total_shots_ge_20 | 40 | recheck volume | Will there be 20 or more total shots? |
        | E vs F | 18% | BASE_PRIOR | usable_base_prior | low | substitution_before_halftime | 0 | usable base | Will a substitution be made before halftime? |
        """,
    )
    _write(
        tmp_path / "special_market_backtest.md",
        """
        ## Adaptation Bootstrap

        | scope | strength | point_delta | ci_2.5 | median | ci_97.5 | pr_delta_lt_0 | decision |
        | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
        | knockout_only | 128 | 0.0003 | -0.0004 | 0.0003 | 0.0009 | 0.199 | not_promoted |
        """,
    )
    _write(
        tmp_path / "special_prior_shrinkage_backtest.md",
        """
        - promotion_decision: not_promoted

        ## Best-Factor Bootstrap

        | scope | best_factor | n | point_delta_vs_raw | ci_2.5 | median | ci_97.5 | pr_gt_0 | decision |
        | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
        | weak_near_coinflip_binary | 0.75 | 628 | 21.79 | -51.13 | 20.59 | 117.79 | 0.666 | not_promoted |
        """,
    )
    _write(
        tmp_path / "special_official_results_audit.md",
        """
        - settled_special_rows: 5
        - usable_special_rows: 5

        ## By Family

        | family | n | successes | prior_mean | edge_vs_50 |
        | --- | ---: | ---: | ---: | ---: |
        | goal_before_hydration/goals | 1 | 0 | 0.439 | +0.0568 |
        """,
    )

    boundary, live_rows, official_rows = build_boundary(tmp_path)

    assert boundary.current_special_rows == 3
    assert boundary.weak_prior_rows == 1
    assert boundary.count_threshold_scaled_far_rows == 1
    assert boundary.high_heterogeneity_rows == 1
    assert boundary.max_scaled_shift_pp == 40
    assert boundary.official_settled_special_rows == 5
    assert boundary.shrinkage_decision == "not_promoted"
    assert boundary.weak_shrink_ci_2_5 == -51.13
    assert boundary.knockout_adaptation_decision == "not_promoted"
    assert boundary.decision == "raw_proxy_with_guardrails_no_shrink_no_official_promotion"
    assert len(live_rows) == 3
    assert len(official_rows) == 1


def test_special_boundary_render_includes_guardrails():
    boundary, live_rows, official_rows = build_boundary(ROOT / "data" / "logs")
    text = render(
        boundary,
        live_rows=live_rows,
        official_family_rows=official_rows,
        now=datetime(2026, 6, 29, 3, tzinfo=timezone.utc),
    )

    assert "# Special Boundary Audit" in text
    assert "`/predictions` was not read" in text
    assert "raw_proxy_with_guardrails_no_shrink_no_official_promotion" in text
    assert "Weak near-coinflip rows" in text
