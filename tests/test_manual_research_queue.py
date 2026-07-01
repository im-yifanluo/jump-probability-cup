import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from manual_research_queue import build_queue, priority_score, render, research_bucket  # noqa: E402


NOW = datetime(2026, 6, 28, 15, tzinfo=timezone.utc)


def _row(question: str, *, tier: str = "MEDIUM", action: str = "", evidence: str = "", hours: int = 10):
    return {
        "match_name": "RSA vs CAN",
        "kickoff": NOW + timedelta(hours=hours),
        "question": question,
        "risk_tier": tier,
        "risk_action": action,
        "risk_evidence": evidence,
        "probability": 42,
        "manual_entry": "42%",
        "manual_policy": "test_policy",
        "flags": "",
    }


def test_far_from_base_special_count_bucket_is_promoted():
    row = _row(
        "Will there be 20 or more total shots (on and off target) in regulation (90 minutes + stoppage time)?",
        tier="HIGH",
        action="verify count-volume inputs and current count sources before trusting raw special model",
        evidence="total_shots_ge_20 base=73.6%, model_minus_base=-40pp",
    )
    row["special_risk_state"] = "COUNT_THRESHOLD_SCALED_FAR_FROM_BASE"

    bucket, reason = research_bucket(row)

    assert bucket == "special_count_far_from_base"
    assert "far from" in reason
    assert priority_score(row, now=NOW) >= 135


def test_high_heterogeneity_special_count_bucket_is_distinct():
    row = _row(
        "Will there be 22 or more total shots (on and off target) in regulation (90 minutes + stoppage time)?",
        action="verify count-volume inputs and current total-shots sources before lock",
    )
    row["special_risk_state"] = "COUNT_THRESHOLD_HIGH_HETEROGENEITY"

    bucket, reason = research_bucket(row)

    assert bucket == "special_count_heterogeneity"
    assert "high cross-tournament spread" in reason


def test_player_hard_flag_bucket_gets_highest_operational_weight():
    row = _row(
        "Will Cyle Larin score a goal (excluding own goals) in regulation (90 minutes + stoppage time)?",
        tier="HIGH",
        action="clear current prop/lineup research; field is unavailable before lock",
        evidence="player-prop backtests show this is the largest leak",
    )

    bucket, reason = research_bucket(row)

    assert bucket == "player_hard_flag"
    assert "player-policy leak" in reason
    assert priority_score(row, now=NOW) >= 135


def test_player_role_risk_bucket_is_distinct_from_generic_recheck():
    row = _row(
        "Will Cyle Larin score a goal (excluding own goals) in regulation (90 minutes + stoppage time)?",
        tier="MEDIUM",
        action="use exact prop odds unless final XI confirms starter role",
    )
    row["player_role_class"] = "rotation_risk"
    row["player_recent_roles"] = "06-13:bench, 06-19:starter"

    bucket, reason = research_bucket(row)

    assert bucket == "player_role_risk"
    assert "rotation risk" in reason
    assert priority_score(row, now=NOW) > 70


def test_card_field_bucket_records_prelock_unavailability():
    row = _row(
        "Will there be 4 or more total cards shown in regulation (90 minutes + stoppage time)?",
        tier="HIGH",
        action="field average unavailable before lock; use no-field card value and carry lower confidence",
        evidence="safe-settlement count backtest",
    )

    bucket, reason = research_bucket(row)

    assert bucket == "field_unavailable_prelock"
    assert "unavailable before lock" in reason


def test_build_queue_filters_low_by_default_and_sorts_by_score():
    player = _row(
        "Will Cyle Larin score a goal (excluding own goals) in regulation (90 minutes + stoppage time)?",
        tier="HIGH",
        action="clear current prop/lineup research; field is unavailable before lock",
    )
    weak_special = _row(
        "Will a goal be scored before the first hydration break?",
        tier="MEDIUM",
        action="use raw weak prior; treat large field disagreement as a research flag",
    )
    low = _row(
        "Will regulation (90 minutes + stoppage time) end in a tie?",
        tier="LOW",
        action="use raw odds-anchored model",
    )

    queue = build_queue([weak_special, low, player], now=NOW)

    assert [row["research_bucket"] for row in queue] == ["player_hard_flag", "weak_special_check"]


def test_build_queue_skips_researched_unresolved_rows_by_default():
    researched = _row(
        "Will Cyle Larin score a goal (excluding own goals) in regulation (90 minutes + stoppage time)?",
        tier="HIGH",
        action="clear current prop/lineup research; field is unavailable before lock",
    )
    researched["market_id"] = "m1"
    fresh = _row(
        "Will there be 22 or more total shots (on and off target) in regulation (90 minutes + stoppage time)?",
        tier="HIGH",
        action="verify count-volume inputs and current count sources before lock",
    )
    fresh["special_risk_state"] = "COUNT_THRESHOLD_HIGH_HETEROGENEITY"
    resolutions = {
        ("id", "m1"): {
            "status": "researched_no_usable_current_source",
            "source": "public_web",
        }
    }

    queue = build_queue([researched, fresh], now=NOW, resolutions=resolutions)

    assert [row["question"] for row in queue] == [fresh["question"]]


def test_build_queue_can_include_researched_unresolved_rows_for_audit_context():
    researched = _row(
        "Will Cyle Larin score a goal (excluding own goals) in regulation (90 minutes + stoppage time)?",
        tier="HIGH",
        action="clear current prop/lineup research; field is unavailable before lock",
    )
    researched["market_id"] = "m1"
    resolutions = {
        ("id", "m1"): {
            "status": "researched_no_usable_current_source",
            "source": "public_web",
        }
    }

    queue = build_queue([researched], now=NOW, include_low=True, resolutions=resolutions)

    assert queue[0]["research_bucket"] == "researched_unresolved_recheck"
    assert queue[0]["priority_score"] < 50


def test_render_includes_summary_and_priority_rows():
    row = _row(
        "Will there be 4 or more total cards shown in regulation (90 minutes + stoppage time)?",
        tier="HIGH",
        action="field average unavailable before lock; use no-field card value and carry lower confidence",
    )
    queue = build_queue([row], now=NOW, include_low=True)

    text = render(
        queue,
        local_date="2026-06-28",
        tz=ZoneInfo("America/Chicago"),
        now=NOW,
        include_low=True,
    )

    assert "# Manual Research Queue" in text
    assert "field_unavailable_prelock" in text
    assert "bot_predictions: not_read" in text


def test_render_includes_role_context_for_player_rows():
    row = _row(
        "Will Cyle Larin score a goal (excluding own goals) in regulation (90 minutes + stoppage time)?",
        tier="MEDIUM",
        action="use exact prop odds unless final XI confirms starter role",
    )
    row["player_role_class"] = "rotation_risk"
    row["player_recent_roles"] = "06-13:bench"
    queue = build_queue([row], now=NOW)

    text = render(
        queue,
        local_date="2026-06-28",
        tz=ZoneInfo("America/Chicago"),
        now=NOW,
        include_low=False,
    )

    assert "player_role_risk" in text
    assert "rotation_risk (06-13:bench)" in text
