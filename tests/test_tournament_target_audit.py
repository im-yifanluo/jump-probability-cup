import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import tournament_target_audit as audit  # noqa: E402


def test_extract_category_edges_from_manual_policy_log(tmp_path, monkeypatch):
    log = tmp_path / "manual_policy_backtest.md"
    log.write_text(
        """# Manual Policy Backtest

## Category Contribution

| category | n | field_delta | combined_delta | current_brier |
| --- | --- | --- | --- | --- |
| player | 80 | 127.20 | 165.43 | 0.2227 |
| count_cards | 39 | 157.01 | 157.01 | 0.3009 |

## Chronological Holdout
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(audit, "MANUAL_POLICY_LOG", log)

    edges = audit._extract_category_edges()

    assert round(edges["player"], 4) == round(165.43 / 80, 4)
    assert round(edges["count_cards"], 4) == round(157.01 / 39, 4)
    assert "special" in edges


def test_extract_tail_risk_summary_from_leaderboard_log(tmp_path, monkeypatch):
    log = tmp_path / "leaderboard_tail_risk.md"
    log.write_text(
        """# Leaderboard Tail-Risk Diagnostic

- run_utc: 2026-06-28T09:11:27+00:00
- special_proxy: statsbomb
- special_proxy_model: LOTO empirical for timing/player-aggregate specials; LOTO negative-binomial for shots and early-corner count thresholds
- special_proxy_rows: 7850
- trials: 20000
- stage_weight_multiplier: 2.00
- caveat: proxy rows, not official special history

## Strategy Tail Distribution

| strategy | mean | sd | p05 | p50 | p95 | p99 | Pr >= 728.16 | Pr >= 871.09 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| combined_manual_policy | 899.97 | 209.25 | 545.20 | 904.22 | 1231.70 | 1356.32 | 79.66% | 56.73% |
| current | 685.54 | 258.63 | 250.50 | 691.14 | 1096.41 | 1255.47 | 44.50% | 24.16% |

## Fallback Categories
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(audit, "LEADERBOARD_TAIL_RISK_LOG", log)

    summary = audit._extract_tail_risk_summary()

    assert summary is not None
    assert summary.special_proxy == "statsbomb"
    assert summary.special_proxy_model.startswith("LOTO empirical")
    assert summary.special_proxy_rows == "7850"
    assert summary.strategies[0].strategy == "combined_manual_policy"
    assert summary.strategies[0].target_probabilities == (
        ("Pr >= 728.16", "79.66%"),
        ("Pr >= 871.09", "56.73%"),
    )


def test_render_includes_tail_risk_proxy_without_changing_rough_edge(tmp_path, monkeypatch):
    policy_log = tmp_path / "manual_policy_backtest.md"
    policy_log.write_text(
        """# Manual Policy Backtest

## Category Contribution

| category | n | field_delta | combined_delta | current_brier |
| --- | --- | --- | --- | --- |
| special | 5 | 0.00 | 0.00 | 0.2000 |

## Chronological Holdout
""",
        encoding="utf-8",
    )
    tail_log = tmp_path / "leaderboard_tail_risk.md"
    tail_log.write_text(
        """# Leaderboard Tail-Risk Diagnostic

- run_utc: 2026-06-28T09:11:27+00:00
- special_proxy: statsbomb
- special_proxy_model: LOTO empirical for timing/player-aggregate specials; LOTO negative-binomial for shots and early-corner count thresholds
- special_proxy_rows: 7850
- trials: 20000
- stage_weight_multiplier: 2.00
- caveat: proxy rows, not official special history

## Strategy Tail Distribution

| strategy | mean | sd | p05 | p50 | p95 | p99 | Pr >= 728.16 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| combined_manual_policy | 899.97 | 209.25 | 545.20 | 904.22 | 1231.70 | 1356.32 | 79.66% |
| current | 685.54 | 258.63 | 250.50 | 691.14 | 1096.41 | 1255.47 | 44.50% |

## Fallback Categories
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(audit, "MANUAL_POLICY_LOG", policy_log)
    monkeypatch.setattr(audit, "LEADERBOARD_TAIL_RISK_LOG", tail_log)

    text = audit.render(
        [
            audit.OpenMarket(
                match_name="A vs B",
                kickoff=audit._dt("2026-06-28T19:00:00Z"),
                market_id="m1",
                question="Will a goal be scored before the first hydration break?",
                category="special",
            )
        ],
        current_score=100.0,
        small_prize_target=110.0,
        first_target=120.0,
        assumed_weight=2.0,
        now=audit._dt("2026-06-28T10:00:00Z"),
    )

    assert "| special | 1 | 0.00 | 0.00 |" in text
    assert "## Special-Aware Tail-Risk Proxy" in text
    assert "special_proxy_model: LOTO empirical" in text
    assert "| combined_manual_policy | 899.97 | 1231.70 | 1356.32 | 79.66% |" in text
    assert "not folded into `rough_mix_edge`" in text
