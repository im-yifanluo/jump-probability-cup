import sys
import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from live_market_risk_audit import (  # noqa: E402
    audit_priced_rows,
    field_template_rows,
    risk_for_market,
    special_proxy_context,
    write_field_template,
    write_field_template_rows,
)
from questions.parser import parse  # noqa: E402
from questions.teams import ALL_TEAM_NAMES  # noqa: E402


NOW = datetime(2026, 6, 28, 15, tzinfo=timezone.utc)
KICKOFF = NOW + timedelta(hours=5)


def _priced(question: str, **overrides):
    row = {
        "match_name": "RSA vs CAN",
        "kickoff": KICKOFF,
        "question": question,
        "manual_entry": "42%",
        "manual_policy": "global_no_field_model",
        "flags": "",
        "field": None,
    }
    row.update(overrides)
    return row


def _risk(question: str, **overrides):
    spec = parse(question, teams=ALL_TEAM_NAMES)
    return risk_for_market(spec, _priced(question, **overrides), now=NOW)


def test_unresolved_player_prop_is_high_risk():
    risk = _risk(
        "Will Cyle Larin score a goal (excluding own goals) in regulation (90 minutes + stoppage time)?",
        flags="GLOBAL_MINUTES_UNCONFIRMED,LINEUP_NOT_POSTED",
        manual_policy="research_required_no_field",
    )

    assert risk.tier == "HIGH"
    assert "prop/lineup research" in risk.action


def test_role_rotation_risk_is_medium_player_execution_risk():
    risk = _risk(
        "Will Cyle Larin score a goal (excluding own goals) in regulation (90 minutes + stoppage time)?",
        flags="LINEUP_NOT_POSTED",
        player_role_class="rotation_risk",
        player_recent_roles="06-13:bench, 06-19:starter",
    )

    assert risk.tier == "MEDIUM"
    assert "final XI" in risk.action
    assert "rotation_risk" in risk.evidence


def test_card_count_without_field_is_high_risk():
    risk = _risk(
        "Will there be 4 or more total cards shown in regulation (90 minutes + stoppage time)?",
        manual_policy="card_count_no_field_model",
    )

    assert risk.tier == "HIGH"
    assert "unavailable before lock" in risk.action


def test_goal_market_is_low_risk_raw_odds_model():
    risk = _risk(
        "Will regulation (90 minutes + stoppage time) end in a tie?",
        manual_policy="raw_odds_goal_model",
    )

    assert risk.tier == "LOW"
    assert "raw odds-anchored" in risk.action


def test_weak_special_is_medium_risk():
    risk = _risk("Will a goal be scored before the first hydration break?")

    assert risk.tier == "MEDIUM"
    assert "weak prior" in risk.action


def test_far_from_base_count_threshold_special_is_high_risk():
    question = "Will there be 20 or more total shots (on and off target) in regulation (90 minutes + stoppage time)?"
    spec = parse(question, teams=ALL_TEAM_NAMES)
    row = _priced(question, p_raw=0.33, probability=33, manual_policy="raw_special_model")

    risk = risk_for_market(spec, row, now=NOW)
    ctx = special_proxy_context(spec, row)

    assert risk.tier == "HIGH"
    assert "count-volume inputs" in risk.action
    assert "model_minus_base=-41pp" in risk.evidence
    assert ctx is not None
    assert ctx.risk_state == "COUNT_THRESHOLD_SCALED_FAR_FROM_BASE"


def test_audit_rows_sort_high_risk_first():
    rows = [
        _priced("Will regulation (90 minutes + stoppage time) end in a tie?"),
        _priced(
            "Will there be 4 or more total cards shown in regulation (90 minutes + stoppage time)?",
            manual_policy="card_count_no_field_model",
        ),
    ]

    audited = audit_priced_rows(rows, now=NOW)

    assert [row["risk_tier"] for row in audited] == ["HIGH", "LOW"]


def test_audit_rows_attach_role_context_by_market_id():
    row = _priced(
        "Will Cyle Larin score a goal (excluding own goals) in regulation (90 minutes + stoppage time)?",
        market_id="m-role",
        flags="LINEUP_NOT_POSTED",
    )

    audited = audit_priced_rows(
        [row],
        now=NOW,
        role_summary={
            "m-role": {
                "role_class": "bench_or_rotation_risk",
                "recent_roles": "06-13:bench, 06-19:starter",
                "action": "reprice after final XI",
            }
        },
    )

    assert audited[0]["player_role_class"] == "bench_or_rotation_risk"
    assert audited[0]["risk_tier"] == "MEDIUM"
    assert "recent ESPN role audit" in audited[0]["risk_evidence"]


def test_write_field_template_defaults_to_actionable_policy_rows(tmp_path):
    path = tmp_path / "field.csv"
    rows = [
        {
            "market_id": "m-card",
            "match_name": "RSA vs CAN",
            "kickoff": KICKOFF,
            "risk_tier": "HIGH",
            "probability": 32,
            "manual_entry": "31%",
            "manual_policy": "card_count_no_field_model",
            "question": "Will there be 4 or more total cards shown in regulation (90 minutes + stoppage time)?",
        },
        {
            "market_id": "m-player",
            "match_name": "RSA vs CAN",
            "kickoff": KICKOFF,
            "risk_tier": "HIGH",
            "probability": 15,
            "manual_entry": "15%",
            "manual_policy": "player_no_field_confirmed_bench_model",
            "question": "Will Cyle Larin score a goal (excluding own goals) in regulation (90 minutes + stoppage time)?",
        },
        {
            "market_id": "m-goal",
            "match_name": "RSA vs CAN",
            "kickoff": KICKOFF,
            "risk_tier": "LOW",
            "probability": 46,
            "manual_entry": "46%",
            "manual_policy": "raw_odds_goal_model",
            "question": "Will both teams score in regulation (90 minutes + stoppage time)?",
        },
    ]

    write_field_template(rows, path)

    with path.open(newline="", encoding="utf-8") as fh:
        out = list(csv.DictReader(fh))
    assert [row["market_id"] for row in out] == ["m-card", "m-player"]
    assert out[0]["risk_tier"] == "HIGH"
    assert out[0]["model_probability"] == "32%"
    assert out[0]["manual_entry"] == "31%"
    assert out[0]["field"] == ""
    assert out[0]["kickoff_utc"] == "2026-06-28T20:00+00:00"


def test_write_field_template_all_mode_preserves_diagnostic_full_export(tmp_path):
    path = tmp_path / "field.csv"
    rows = [
        {
            "market_id": "m-card",
            "match_name": "RSA vs CAN",
            "kickoff": KICKOFF,
            "risk_tier": "HIGH",
            "probability": 32,
            "manual_entry": "31%",
            "manual_policy": "card_count_no_field_model",
            "question": "Will there be 4 or more total cards shown in regulation (90 minutes + stoppage time)?",
        },
        {
            "market_id": "m-goal",
            "match_name": "RSA vs CAN",
            "kickoff": KICKOFF,
            "risk_tier": "LOW",
            "probability": 46,
            "manual_entry": "46%",
            "manual_policy": "raw_odds_goal_model",
            "question": "Will both teams score in regulation (90 minutes + stoppage time)?",
        },
    ]

    write_field_template_rows(rows, path, mode="all")

    with path.open(newline="", encoding="utf-8") as fh:
        out = list(csv.DictReader(fh))
    assert [row["market_id"] for row in out] == ["m-card", "m-goal"]


def test_field_template_rows_excludes_specials_and_strong_sot_count():
    rows = [
        _priced(
            "Will Germany have 7 or more shots on target in regulation (90 minutes + stoppage time)?",
            market_id="m-sot",
            probability=27,
            manual_policy="strong_sot_count_model",
        ),
        _priced(
            "Will a goal be scored before the first hydration break?",
            market_id="m-special",
            probability=43,
            manual_policy="raw_special_model",
        ),
        _priced(
            "Will there be 4 or more total cards shown in regulation (90 minutes + stoppage time)?",
            market_id="m-card",
            probability=32,
            manual_policy="card_count_no_field_model",
        ),
    ]

    selected = field_template_rows(rows)

    assert [row["market_id"] for row in selected] == ["m-card"]
