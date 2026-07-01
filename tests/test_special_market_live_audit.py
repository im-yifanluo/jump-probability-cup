import sys
import csv
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from questions.parser import parse  # noqa: E402
from special_market_live_audit import (  # noqa: E402
    SpecialEvidence,
    audit_rows,
    count_source_template_rows,
    render,
    risk_state,
    risk_action,
    special_evidence_key,
    write_count_source_template,
)


def test_special_evidence_key_maps_live_question_families():
    teams = {"Brazil", "Japan", "United States", "Bosnia and Herzegovina"}

    assert (
        special_evidence_key(parse("Will a goal be scored before the first hydration break?", teams=teams))
        == "goal_before_30"
    )
    assert (
        special_evidence_key(
            parse(
                "Will there be 22 or more total shots (on and off target) in regulation (90 minutes + stoppage time)?",
                teams=teams,
            )
        )
        == "total_shots_ge_22"
    )
    assert (
        special_evidence_key(parse("Will 2 or more corner kicks be taken before the first hydration break?", teams=teams))
        == "early_corners_ge_2"
    )
    assert (
        special_evidence_key(
            parse(
                "Will an own goal be scored in regulation (90 minutes + stoppage time)?",
                teams=teams,
            )
        )
        == "own_goal"
    )
    assert (
        special_evidence_key(
            parse(
                "Will a goal be scored from outside the penalty area in regulation (90 minutes + stoppage time)?",
                teams=teams,
            )
        )
        == "outside_box_goal"
    )
    assert (
        special_evidence_key(
            parse(
                "Will a header goal be scored in regulation (90 minutes + stoppage time)?",
                teams=teams,
            )
        )
        == "header_goal"
    )
    assert (
        special_evidence_key(
            parse(
                "Will any player score at least 1 goal in regulation (90 minutes + stoppage time)?",
                teams=teams,
            )
        )
        == "any_player_goal"
    )
    assert (
        special_evidence_key(
            parse(
                "Will any player score 2 or more goals in regulation (90 minutes + stoppage time)?",
                teams=teams,
            )
        )
        == "any_player_brace"
    )


def test_risk_state_prioritizes_weak_and_scaled_proxy_labels():
    weak = SpecialEvidence("goal_before_30", "binary_loto_base_prior", "weak_near_coinflip", 314, 0.44, 0.38, 0.50, 0.25, 0.001, "moderate")
    base = SpecialEvidence("card_after_75", "binary_loto_base_prior", "usable_base_prior", 314, 0.66, 0.60, 0.71, 0.23, 0.02, "low")
    high = SpecialEvidence("any_player_brace", "binary_loto_base_prior", "usable_base_prior", 314, 0.16, 0.12, 0.21, 0.14, 0.11, "high")
    threshold = SpecialEvidence("total_shots_ge_22", "count_threshold_loto_base_prior", "usable_base_prior", 314, 0.55, 0.50, 0.60, 0.24, 0.01, "low")
    high_threshold = SpecialEvidence("total_shots_ge_22", "count_threshold_loto_base_prior", "usable_base_prior", 314, 0.55, 0.50, 0.60, 0.24, 0.01, "high")

    assert risk_state(None, model_probability=0.4) == "UNMAPPED_PROXY"
    assert risk_state(weak, model_probability=0.45) == "WEAK_PRIOR"
    assert risk_state(threshold, model_probability=0.55) == "COUNT_THRESHOLD_PROXY"
    assert risk_state(high_threshold, model_probability=0.55) == "COUNT_THRESHOLD_HIGH_HETEROGENEITY"
    assert risk_state(threshold, model_probability=0.30) == "COUNT_THRESHOLD_SCALED_FAR_FROM_BASE"
    assert risk_state(high, model_probability=0.16) == "HIGH_HETEROGENEITY_PROXY"
    assert risk_state(base, model_probability=0.85) == "SCALED_FAR_FROM_BASE"
    assert risk_state(base, model_probability=0.70) == "BASE_PRIOR"
    assert "count-volume" in risk_action("COUNT_THRESHOLD_SCALED_FAR_FROM_BASE")


def test_audit_rows_and_render_attach_evidence():
    evidence = {
        "goal_before_30": SpecialEvidence(
            "goal_before_30",
            "binary_loto_base_prior",
            "weak_near_coinflip",
            314,
            0.439,
            0.386,
            0.495,
            0.2493,
            0.0007,
            "moderate",
        )
    }
    priced = [
        {
            "match_name": "BRA vs JPN",
            "kickoff": datetime(2026, 6, 29, 17, tzinfo=timezone.utc),
            "question": "Will a goal be scored before the first hydration break?",
            "manual_entry": "45%",
            "p_raw": 0.45,
        }
    ]

    rows = audit_rows(priced, evidence)
    text = render(rows, local_date="2026-06-29", all_open=True, include_started=False)

    assert rows[0]["evidence_key"] == "goal_before_30"
    assert rows[0]["risk_state"] == "WEAK_PRIOR"
    assert rows[0]["model_minus_base_pp"] == 1
    assert "coinflip" in rows[0]["risk_action"]
    assert rows[0]["heterogeneity"] == "moderate"
    assert "Special Market Live Audit" in text
    assert "WEAK_PRIOR" in text
    assert "model_minus_base_pp" in text


def test_count_source_template_keeps_only_special_count_threshold_rows(tmp_path):
    path = tmp_path / "special_count_sources.csv"
    rows = [
        {
            "market_id": "m-shots",
            "match_name": "AUS vs EGY",
            "kickoff": datetime(2026, 7, 3, 18, tzinfo=timezone.utc),
            "manual_entry": "33%",
            "risk_state": "COUNT_THRESHOLD_SCALED_FAR_FROM_BASE",
            "evidence_key": "total_shots_ge_20",
            "evidence_type": "count_threshold_loto_base_prior",
            "hit_rate": 0.736,
            "model_minus_base_pp": -40,
            "question": "Will there be 20 or more total shots (on and off target) in regulation?",
        },
        {
            "market_id": "m-goal",
            "match_name": "AUS vs EGY",
            "kickoff": datetime(2026, 7, 3, 18, tzinfo=timezone.utc),
            "manual_entry": "48%",
            "risk_state": "WEAK_PRIOR",
            "evidence_key": "goal_before_30",
            "evidence_type": "binary_loto_base_prior",
            "hit_rate": 0.439,
            "model_minus_base_pp": 4,
            "question": "Will a goal be scored before the first hydration break?",
        },
    ]

    selected = count_source_template_rows(rows)
    write_count_source_template(rows, path)

    with path.open(newline="", encoding="utf-8") as fh:
        out = list(csv.DictReader(fh))

    assert [row["market_id"] for row in selected] == ["m-shots"]
    assert [row["market_id"] for row in out] == ["m-shots"]
    assert out[0]["risk_state"] == "COUNT_THRESHOLD_SCALED_FAR_FROM_BASE"
    assert out[0]["model_entry"] == "33%"
    assert out[0]["base_hit_rate"] == "73.6%"
    assert out[0]["source_1"] == ""
