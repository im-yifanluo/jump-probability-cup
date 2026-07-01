import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from manual_execution_leverage import (  # noqa: E402
    build_leverage,
    category_per_row_lift,
    leverage_score,
    load_coverage_rows,
    render,
    row_category,
)


COVERAGE_MD = """
# Manual Evidence Coverage Audit

## Markets

| kickoff_local | match | entry | risk | evidence_status | coverage_state | evidence_area | question | gate |
| --- | --- | ---: | --- | --- | --- | --- | --- | --- |
| 2026-06-29 12:00 CDT | BRA vs JPN | 31% | HIGH | PROMOTED | FIELD_UNAVAILABLE_PRELOCK | count_field_policy | Will there be 4 or more total cards shown in regulation (90 minutes + stoppage time)? | field averages are unavailable before prediction lock |
| 2026-06-29 12:00 CDT | BRA vs JPN | 28% | HIGH | PROMOTED_LIMITED | PLAYER_RESEARCH_REQUIRED | player_no_field_fallback | Will Ayase Ueda have at least 1 shot on target in regulation (90 minutes + stoppage time)? | clear lineup/role/prop flags |
| 2026-06-29 12:00 CDT | BRA vs JPN | 45% | MEDIUM | PROXY_ONLY | PROXY_ONLY | special_markets | Will a goal be scored before the first hydration break? | use raw proxy prior |
| 2026-06-29 12:00 CDT | BRA vs JPN | 33% | HIGH | PROXY_ONLY | SPECIAL_COUNT_RESEARCH_REQUIRED | special_markets | Will there be 20 or more total shots (on and off target) in regulation (90 minutes + stoppage time)? | verify count-volume inputs |
| 2026-06-29 12:00 CDT | BRA vs JPN | 48% | LOW | CURRENT_SOURCE_RULE | CURRENT_ANCHOR_RECHECK | exact_polymarket_anchors | Will the match have 3 or more total goals in regulation (90 minutes + stoppage time)? | use only as a current-source anchor |
"""


POLICY_MD = """
# Manual Policy Backtest

## Category Contribution

| category | n | field_delta | combined_delta | current_brier | combined_brier | model | field | hit |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| player | 80 | 127.20 | 165.43 | 0.2227 | 0.2020 | 0.319 | 0.410 | 0.275 |
| count_cards | 39 | 157.01 | 157.01 | 0.3009 | 0.2607 | 0.493 | 0.498 | 0.436 |
| odds_goal | 169 | -120.52 | 0.00 | 0.2171 | 0.2171 | 0.487 | 0.495 | 0.503 |
"""


def test_loads_coverage_and_category_lift(tmp_path):
    coverage = tmp_path / "coverage.md"
    policy = tmp_path / "policy.md"
    coverage.write_text(COVERAGE_MD, encoding="utf-8")
    policy.write_text(POLICY_MD, encoding="utf-8")

    rows = load_coverage_rows(coverage)
    lifts = category_per_row_lift(policy)

    assert len(rows) == 5
    assert rows[0].coverage_state == "FIELD_UNAVAILABLE_PRELOCK"
    assert round(lifts["count_cards"], 2) == 4.03
    assert round(lifts["player"], 2) == 2.07


def test_categories_and_scores_prioritize_actionable_player_and_special_research(tmp_path):
    coverage = tmp_path / "coverage.md"
    policy = tmp_path / "policy.md"
    coverage.write_text(COVERAGE_MD, encoding="utf-8")
    policy.write_text(POLICY_MD, encoding="utf-8")

    items = build_leverage(load_coverage_rows(coverage), category_per_row_lift(policy))

    assert row_category(items[0]["row"]) == "player"
    assert items[0]["priority"] == "HIGH"
    assert items[1]["priority"] == "HIGH"
    assert items[1]["row"].coverage_state == "SPECIAL_COUNT_RESEARCH_REQUIRED"
    assert items[1]["category"] == "special_proxy"
    assert items[1]["lift_per_row"] is None
    assert leverage_score(items[1]["row"], None) == 0.5
    assert items[2]["row"].coverage_state == "FIELD_UNAVAILABLE_PRELOCK"
    assert items[2]["priority"] == "MONITOR"
    assert items[3]["category"] == "special_proxy"
    assert items[3]["lift_per_row"] is None
    assert leverage_score(items[3]["row"], None) == 0.2


def test_render_includes_read_only_guardrails(tmp_path):
    coverage = tmp_path / "coverage.md"
    policy = tmp_path / "policy.md"
    coverage.write_text(COVERAGE_MD, encoding="utf-8")
    policy.write_text(POLICY_MD, encoding="utf-8")

    text = render(
        build_leverage(load_coverage_rows(coverage), category_per_row_lift(policy)),
        now=datetime(2026, 6, 28, 18, tzinfo=timezone.utc),
    )

    assert "# Manual Execution Leverage Audit" in text
    assert "bot_predictions: not_read" in text
    assert "sports_predict: not_called" in text
    assert "FIELD_UNAVAILABLE_PRELOCK" in text
    assert "hist_lift_per_row" in text
