import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from backtest.history import HistoryMarket  # noqa: E402
from question_family_coverage_audit import (  # noqa: E402
    coverage_status,
    merge_records,
    parser_family_area_status,
    records_from_history,
    records_from_open_questions,
    render,
    strategy_area_for_spec,
)
from questions.parser import MarketSpec  # noqa: E402


STATUSES = {
    "combined_manual_policy": "PROMOTED",
    "count_field_policy": "PROMOTED",
    "odds_goal_rows": "PROMOTED",
    "penalty_red": "GUARDRAIL_ONLY",
    "researched_player_props": "PROMOTED_LIMITED",
    "special_markets": "PROXY_ONLY",
}


def _history(question: str, family: str, stat: str = "", period: str = "match") -> HistoryMarket:
    return HistoryMarket(
        version=2,
        order=1,
        match="BRA vs JPN",
        question=question,
        you=0.5,
        crowd=0.5,
        outcome=1,
        official_rbp=0.0,
        family=family,
        stat=stat,
        period=period,
    )


def test_strategy_area_routes_history_player_family_without_player_name():
    spec = MarketSpec("player_sot_ge", stat="sot")

    assert strategy_area_for_spec(spec) == "researched_player_props"


def test_records_merge_history_and_open_observed_families():
    history = records_from_history(
        [
            _history("Will Brazil win the match?", "team_win"),
            _history("Will there be 8 or more total shots on target?", "total_count_ge", "sot"),
            _history("Will Duckens Nazon have at least 1 shot on target?", "player_sot_ge", "sot"),
        ],
        STATUSES,
    )
    open_records = records_from_open_questions(
        [
            "Will there be 22 or more total shots (on and off target) in regulation (90 minutes + stoppage time)?",
            "Will a penalty kick be awarded during regulation (90 minutes + stoppage time)?",
        ],
        STATUSES,
    )

    rows = {row.family_key: row for row in merge_records(history, open_records)}

    assert rows["team_win"].strategy_area == "odds_goal_rows"
    assert rows["total_count_ge/sot"].strategy_area == "count_field_policy"
    assert rows["player_sot_ge/sot"].strategy_area == "researched_player_props"
    assert rows["total_count_ge/shots"].strategy_area == "special_markets"
    assert rows["penalty_awarded"].strategy_area == "penalty_red"


def test_coverage_status_is_complete_but_explicit_about_proxy_and_limited_rows():
    records = list(
        records_from_open_questions(
            [
                "Will Brazil win in regulation (90 minutes + stoppage time)?",
                "Will a goal be scored before the first hydration break?",
                "Will Cyle Larin score a goal (excluding own goals) in regulation (90 minutes + stoppage time)?",
            ],
            STATUSES,
        ).values()
    )
    parser_rows = parser_family_area_status(STATUSES)

    assert coverage_status(records, parser_rows) == "COMPLETE_WITH_LIMITED_PROXY_OR_GUARDRAIL_ROWS"


def test_render_includes_guardrails_and_parser_owner_table():
    records = list(
        records_from_open_questions(
            [
                "Will Brazil win in regulation (90 minutes + stoppage time)?",
                "Will there be 20 or more total shots (on and off target) in regulation (90 minutes + stoppage time)?",
            ],
            STATUSES,
        ).values()
    )

    text = render(
        records,
        parser_family_area_status(STATUSES),
        now=datetime(2026, 6, 29, 9, tzinfo=timezone.utc),
    )

    assert "# Question Family Strategy Coverage Audit" in text
    assert "`/predictions` was not read" in text
    assert "question_family_coverage_status" in text
    assert "Parser Family Owners" in text
    assert "special_markets" in text


def test_combined_manual_policy_live_rule_does_not_wait_for_field():
    records = list(
        records_from_open_questions(
            [
                "Will a header goal be scored in regulation (90 minutes + stoppage time)?",
            ],
            STATUSES,
        ).values()
    )

    text = render(
        records,
        parser_family_area_status(STATUSES),
        now=datetime(2026, 6, 29, 9, tzinfo=timezone.utc),
    )

    assert "Use the no-field current-source/model value before lock" in text
    assert "Use the family-specific manual policy when field is visible" not in text
