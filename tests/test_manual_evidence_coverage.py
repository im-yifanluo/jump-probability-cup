import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from manual_evidence_coverage import build_coverage, evidence_area, load_direct_anchor_risks, render  # noqa: E402


NOW = datetime(2026, 6, 28, 15, tzinfo=timezone.utc)
KICKOFF = NOW + timedelta(hours=5)

STATUSES = {
    "combined_manual_policy": "PROMOTED",
    "count_field_policy": "PROMOTED",
    "exact_polymarket_anchors": "CURRENT_SOURCE_RULE",
    "odds_goal_rows": "PROMOTED",
    "penalty_red": "GUARDRAIL_ONLY",
    "player_no_field_fallback": "PROMOTED_LIMITED",
    "researched_player_props": "PROMOTED_LIMITED",
    "special_markets": "PROXY_ONLY",
}


def _row(question, **overrides):
    row = {
        "match_name": "RSA vs CAN",
        "kickoff": KICKOFF,
        "question": question,
        "manual_entry": "42%",
        "manual_policy": "raw_odds_goal_model",
        "model": "",
        "flags": "",
        "risk_tier": "LOW",
        "risk_action": "",
    }
    row.update(overrides)
    return row


def _coverage(row):
    return build_coverage([row], STATUSES)[0]


def test_exact_polymarket_anchor_maps_to_current_source_rule():
    row = _row(
        "Will both teams score in regulation (90 minutes + stoppage time)?",
        model="current_polymarket_direct [Polymarket direct vol=$10,000 outcome=Yes]",
    )

    covered = _coverage(row)

    assert evidence_area(row) == "exact_polymarket_anchors"
    assert covered["evidence_status"] == "CURRENT_SOURCE_RULE"
    assert covered["coverage_state"] == "CURRENT_ANCHOR_RECHECK"
    assert "not historically promoted" in covered["coverage_gate"]


def test_exact_polymarket_anchor_appends_direct_risk_gate():
    question = "Will Matheus Cunha score a goal (excluding own goals) in regulation (90 minutes + stoppage time)?"
    row = _row(
        question,
        match_name="BRA vs JPN",
        model="current_player_prop_anchor [Polymarket direct vol=$3,907 outcome=Yes]",
        manual_policy="player_no_field_researched_model",
        risk_tier="MEDIUM",
        risk_action="recheck lineup and current prop market before lock",
    )

    [covered] = build_coverage(
        [row],
        STATUSES,
        direct_anchor_risks={
            ("BRA vs JPN", question): {
                "risk": "HIGH",
                "tags": "LARGE_SHIFT_RECHECK, LOW_REPLAY_SAMPLE, PLAYER_PROP_LINEUP_RECHECK",
                "action": "verify wording and compare an independent source before lock",
            }
        },
    )

    assert covered["coverage_state"] == "PLAYER_RECHECK"
    assert covered["direct_anchor_risk"] == "HIGH"
    assert "direct-anchor risk HIGH" in covered["coverage_gate"]
    assert "LARGE_SHIFT_RECHECK" in covered["coverage_gate"]
    assert "independent source" in covered["coverage_gate"]


def test_load_direct_anchor_risks_reads_current_anchor_table(tmp_path):
    path = tmp_path / "risk.md"
    path.write_text(
        """
# Polymarket Direct Anchor Risk Audit

## Current Anchor Recheck Rows

| risk | status | match | kickoff_utc | model | anchor | delta_pp | volume | tags | action | question | polymarket_question |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: | --- | --- | --- | --- |
| HIGH | promoted_count_corner | BRA vs JPN | 2026-06-29T17:00+00:00 | 50% | 57% | +7 | $11,330 | LARGE_SHIFT_RECHECK | verify wording | Will there be 9 or more total corner kicks? | Brazil vs. Japan: O/U 8.5 Total Corners |
""".strip()
        + "\n",
        encoding="utf-8",
    )

    risks = load_direct_anchor_risks(path)

    assert risks[("BRA vs JPN", "Will there be 9 or more total corner kicks?")]["risk"] == "HIGH"
    assert risks[("BRA vs JPN", "Will there be 9 or more total corner kicks?")]["tags"] == "LARGE_SHIFT_RECHECK"


def test_card_count_records_prelock_field_unavailability():
    row = _row(
        "Will there be 4 or more total cards shown in regulation (90 minutes + stoppage time)?",
        manual_policy="card_count_no_field_model",
        risk_tier="HIGH",
        risk_action="field average unavailable before lock; use no-field card value and carry lower confidence",
    )

    covered = _coverage(row)

    assert covered["evidence_area"] == "count_field_policy"
    assert covered["evidence_status"] == "PROMOTED"
    assert covered["coverage_state"] == "FIELD_UNAVAILABLE_PRELOCK"
    assert "not available before prediction lock" in covered["coverage_gate"]


def test_unresolved_player_prop_is_research_required():
    row = _row(
        "Will Cyle Larin score a goal (excluding own goals) in regulation (90 minutes + stoppage time)?",
        manual_policy="player_no_field_raw_scores_else_global_base",
        flags="GLOBAL_MINUTES_UNCONFIRMED,LINEUP_NOT_POSTED",
        risk_tier="HIGH",
        risk_action="clear current prop/lineup research; field is unavailable before lock",
    )

    covered = _coverage(row)

    assert covered["evidence_area"] == "player_no_field_fallback"
    assert covered["evidence_status"] == "PROMOTED_LIMITED"
    assert covered["coverage_state"] == "PLAYER_RESEARCH_REQUIRED"


def test_special_and_penalty_rows_keep_lower_confidence_statuses():
    special = _coverage(
        _row(
            "Will a goal be scored before the first hydration break?",
            manual_policy="raw_special_model",
            risk_tier="MEDIUM",
            risk_action="use raw weak prior; treat large field disagreement as a research flag",
        )
    )
    penalty = _coverage(
        _row(
            "Will a penalty kick be awarded during regulation (90 minutes + stoppage time)?",
            manual_policy="global_no_field_model",
            risk_tier="MEDIUM",
            risk_action="use base-rate model; avoid pushing above the low-event base without direct evidence",
        )
    )

    assert special["evidence_area"] == "special_markets"
    assert special["coverage_state"] == "PROXY_ONLY"
    assert penalty["evidence_area"] == "penalty_red"
    assert penalty["coverage_state"] == "GUARDRAIL_ONLY"


def test_count_threshold_special_warnings_are_distinct_coverage_gates():
    far = _coverage(
        _row(
            "Will there be 20 or more total shots (on and off target) in regulation (90 minutes + stoppage time)?",
            manual_policy="raw_special_model",
            risk_tier="HIGH",
            risk_action="verify count-volume inputs and current count sources before trusting raw special model",
            special_risk_state="COUNT_THRESHOLD_SCALED_FAR_FROM_BASE",
        )
    )
    heterogeneity = _coverage(
        _row(
            "Will there be 22 or more total shots (on and off target) in regulation (90 minutes + stoppage time)?",
            manual_policy="raw_special_model",
            risk_tier="MEDIUM",
            risk_action="verify count-volume inputs and current total-shots sources before lock",
            special_risk_state="COUNT_THRESHOLD_HIGH_HETEROGENEITY",
        )
    )

    assert far["evidence_area"] == "special_markets"
    assert far["coverage_state"] == "SPECIAL_COUNT_RESEARCH_REQUIRED"
    assert "count-volume inputs" in far["coverage_gate"]
    assert heterogeneity["coverage_state"] == "SPECIAL_COUNT_RECHECK"


def test_render_includes_read_only_guardrails_and_summary():
    rows = build_coverage(
        [
            _row("Will regulation (90 minutes + stoppage time) end in a tie?"),
            _row(
                "Will there be 4 or more total cards shown in regulation (90 minutes + stoppage time)?",
                manual_policy="card_count_no_field_model",
                risk_tier="HIGH",
                risk_action="field average unavailable before lock; use no-field card value and carry lower confidence",
            ),
        ],
        STATUSES,
    )

    text = render(rows, local_date="2026-06-28", tz=ZoneInfo("America/Chicago"), now=NOW)

    assert "# Manual Evidence Coverage Audit" in text
    assert "bot_predictions: not_read" in text
    assert "direct_anchor_risk" in text
    assert "FIELD_UNAVAILABLE_PRELOCK" in text
    assert "odds_goal_rows" in text
