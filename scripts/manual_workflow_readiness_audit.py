"""Check whether the manual prediction workflow is ready for live use.

This is a local, read-only audit over generated reports and the Codex
automation file. It never calls SportsPredict, never reads `/predictions`, and
never submits anything.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import re
import tomllib


ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "data" / "logs"
OUT = LOG_DIR / "manual_workflow_readiness_audit.md"
DEFAULT_AUTOMATION = Path.home() / ".codex" / "automations" / "probability-cup-nightly-manual-predictions-in-chat" / "automation.toml"


@dataclass(frozen=True)
class ReadinessCheck:
    check: str
    status: str
    observed: str
    required: str
    action: str


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _key_values(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        match = re.match(r"^- ([A-Za-z0-9_ ./%>=#-]+):\s*(.*)$", line)
        if match:
            out[match.group(1).strip()] = match.group(2).strip()
    return out


def _status_count(text: str, status: str) -> int:
    match = re.search(rf"^\|\s*{re.escape(status)}\s*\|\s*(\d+)\s*\|", text, flags=re.M)
    return int(match.group(1)) if match else 0


def _latest_manual_section(text: str, local_date: str) -> str:
    sections = re.split(r"(?=^##\s+)", text, flags=re.M)
    matches = [section for section in sections if f"full_manual_pricing_v2 local_date={local_date}" in section]
    return matches[-1] if matches else ""


def _manual_sheet_match_count(section: str) -> int:
    return len(re.findall(r"^###\s+", section, flags=re.M))


def _research_counts(text: str) -> tuple[int, int]:
    high = medium = 0
    for line in text.splitlines():
        if line.startswith("- HIGH:"):
            high = int(re.search(r"(\d+)", line).group(1))  # type: ignore[union-attr]
        if line.startswith("- MEDIUM:"):
            medium = int(re.search(r"(\d+)", line).group(1))  # type: ignore[union-attr]
    if high == 0 and medium == 0:
        high = _status_count(text, "HIGH")
        medium = _status_count(text, "MEDIUM")
    return high, medium


def _automation_prompt(data: dict) -> str:
    value = data.get("prompt", "")
    return value if isinstance(value, str) else ""


def _automation_rrule(data: dict) -> str:
    value = data.get("rrule", "")
    return value if isinstance(value, str) else ""


def _rrule_hours(rrule: str) -> set[int]:
    match = re.search(r"BYHOUR=([0-9,]+)", rrule)
    if not match:
        return set()
    return {int(part) for part in match.group(1).split(",") if part}


def _rrule_minutes(rrule: str) -> set[int]:
    match = re.search(r"BYMINUTE=([0-9,]+)", rrule)
    if not match:
        return set()
    return {int(part) for part in match.group(1).split(",") if part}


def build_readiness(
    *,
    root: Path = ROOT,
    local_date: str,
    automation_file: Path = DEFAULT_AUTOMATION,
) -> tuple[str, list[ReadinessCheck], dict[str, str]]:
    log_dir = root / "data" / "logs"
    checks: list[ReadinessCheck] = []
    meta: dict[str, str] = {"local_date": local_date}

    freshness_path = log_dir / "evidence_freshness_audit.md"
    if freshness_path.exists():
        freshness = _read(freshness_path)
        stale = _status_count(freshness, "STALE_OR_INCONSISTENT")
        missing = _status_count(freshness, "MISSING")
        fresh = _status_count(freshness, "FRESH")
        meta["fresh_evidence_rows"] = str(fresh)
        checks.append(
            ReadinessCheck(
                "evidence_freshness",
                "PASS" if fresh > 0 and stale == 0 and missing == 0 else "FAIL",
                f"fresh={fresh}, stale={stale}, missing={missing}",
                "all generated evidence checks fresh",
                "rerun stale/missing evidence gates before trusting strategy matrix",
            )
        )
    else:
        checks.append(ReadinessCheck("evidence_freshness", "FAIL", "missing", "audit exists", "run evidence_freshness_audit.py --write"))

    matrix_path = log_dir / "strategy_evidence_matrix.md"
    if matrix_path.exists():
        matrix = _read(matrix_path)
        keys = _key_values(matrix)
        checks.append(
            ReadinessCheck(
                "strategy_matrix_guardrails",
                "PASS" if keys.get("bot_predictions") == "not_read" and keys.get("sports_predict") == "not_called" else "FAIL",
                f"bot_predictions={keys.get('bot_predictions')}, sports_predict={keys.get('sports_predict')}",
                "bot_predictions=not_read and sports_predict=not_called",
                "regenerate strategy_evidence_matrix.py without bot/API side effects",
            )
        )
    else:
        checks.append(ReadinessCheck("strategy_matrix_guardrails", "FAIL", "missing", "matrix exists", "run strategy_evidence_matrix.py --write"))

    research_path = log_dir / "manual_research_queue.md"
    if research_path.exists():
        research = _read(research_path)
        high, medium = _research_counts(research)
        meta["research_high_rows"] = str(high)
        meta["research_medium_rows"] = str(medium)
        keys = _key_values(research)
        checks.append(
            ReadinessCheck(
                "manual_research_queue",
                "WARN" if high > 0 else "PASS",
                f"HIGH={high}, MEDIUM={medium}, bot_predictions={keys.get('bot_predictions')}",
                "queue generated and bot_predictions not_read",
                "clear HIGH rows with lineup/prop/current-source research before lock when possible; field averages are unavailable pre-lock",
            )
        )
    else:
        checks.append(ReadinessCheck("manual_research_queue", "FAIL", "missing", "queue exists", "run manual_research_queue.py --all-open --write"))

    field_capture_path = log_dir / "manual_field_capture_audit.md"
    if field_capture_path.exists():
        field_capture = _read(field_capture_path)
        keys = _key_values(field_capture)
        capture_status = keys.get("status", "unknown")
        meta["field_capture_status"] = "UNAVAILABLE_PRELOCK"
        observed = (
            f"status={capture_status}, required={keys.get('required_rows')}, "
            f"matched={keys.get('matched_rows')}, missing={keys.get('missing_rows')}, "
            f"blank={keys.get('blank_rows')}, invalid={keys.get('invalid_rows')}, "
            f"duplicates={keys.get('duplicate_rows')}, extra={keys.get('extra_rows')}"
        )
        if capture_status.startswith("INVALID"):
            status = "FAIL"
        else:
            status = "PASS"
        checks.append(
            ReadinessCheck(
                "field_capture_audit",
                status,
                observed,
                "field capture audited; field averages are unavailable before prediction lock",
                "do not wait for or fabricate field values pre-lock; use no-field/current-source manual entries",
            )
        )
    else:
        meta["field_capture_status"] = "missing"
        checks.append(
            ReadinessCheck(
                "field_capture_audit",
                "WARN",
                "missing",
                "manual_field_capture_audit.md generated for diagnostics",
                "run manual_field_capture_audit.py --write for visibility diagnostics; field values are unavailable pre-lock",
            )
        )

    player_prop_path = log_dir / "player_prop_diagnostics.md"
    if player_prop_path.exists():
        player_prop = _read(player_prop_path)
        keys = _key_values(player_prop)
        prop_status = keys.get("player_prop_anchor_status", "NOT_SUPPLIED")
        meta["player_prop_anchor_status"] = prop_status
        observed = (
            f"status={prop_status}, current_player_rows={keys.get('current_player_rows')}, "
            f"csv_rows={keys.get('csv_rows')}, usable={keys.get('usable')}, "
            f"current_missing_usable={keys.get('current_missing_usable')}, "
            f"high_priority_missing_usable={keys.get('high_priority_missing_usable')}, "
            f"blank={keys.get('current_blank')}, invalid={keys.get('current_invalid')}, "
            f"unused={keys.get('unused_usable')}"
        )
        if prop_status in {"COMPLETE", "NO_CURRENT_PLAYER_PROPS"}:
            status = "PASS"
        elif prop_status == "COMPLETE_WITH_UNUSED_ROWS":
            status = "WARN"
        elif prop_status in {"NOT_SUPPLIED", "INCOMPLETE", "PARTIAL"}:
            status = "WARN"
        else:
            status = "FAIL"
        checks.append(
            ReadinessCheck(
                "player_prop_anchor_audit",
                status,
                observed,
                "current player-prop anchor template audited; high-priority rows either anchored or intentionally prop/lineup-led",
                "run player_prop_diagnostics.py --sports-predict --all-open --write --anchor-template --player-prop-csv data/player_prop_anchors_template.csv",
            )
        )
        current_anchor_status = keys.get("current_prop_anchor_status", "unknown")
        meta["current_prop_anchor_status"] = current_anchor_status
        current_anchor_observed = (
            f"status={current_anchor_status}, anchored={keys.get('current_prop_anchor_rows')}, "
            f"missing={keys.get('current_prop_anchor_missing_rows')}, "
            f"high_missing={keys.get('high_priority_missing_current_prop_anchor')}, "
            f"medium_anchored={keys.get('medium_priority_anchor_rows')}"
        )
        if current_anchor_status in {"COMPLETE", "NO_CURRENT_PLAYER_PROPS"}:
            current_anchor_check_status = "PASS"
        elif current_anchor_status in {
            "PARTIAL_HIGH_PRIORITY_COVERED",
            "PARTIAL_HIGH_PRIORITY_MISSING",
            "NO_CURRENT_PROP_ANCHORS",
            "DISABLED",
            "unknown",
        }:
            current_anchor_check_status = "WARN"
        else:
            current_anchor_check_status = "FAIL"
        checks.append(
            ReadinessCheck(
                "current_prop_anchor_coverage",
                current_anchor_check_status,
                current_anchor_observed,
                "exact current player-prop anchors are accounted for separately from the manual CSV",
                "run player_prop_diagnostics.py --sports-predict --all-open --write with Polymarket player props enabled",
            )
        )
    else:
        meta["player_prop_anchor_status"] = "missing"
        meta["current_prop_anchor_status"] = "missing"
        checks.append(
            ReadinessCheck(
                "player_prop_anchor_audit",
                "WARN",
                "missing",
                "player_prop_diagnostics.md generated with anchor CSV audit",
                "run player_prop_diagnostics.py --sports-predict --all-open --write --anchor-template --player-prop-csv data/player_prop_anchors_template.csv",
            )
        )
        checks.append(
            ReadinessCheck(
                "current_prop_anchor_coverage",
                "WARN",
                "missing",
                "player_prop_diagnostics.md generated with exact current prop-anchor coverage",
                "run player_prop_diagnostics.py --sports-predict --all-open --write with Polymarket player props enabled",
            )
        )

    current_player_path = log_dir / "current_player_stats_audit.md"
    if current_player_path.exists():
        current_player = _read(current_player_path)
        keys = _key_values(current_player)
        current_player_status = keys.get("current_player_stats_status", "unknown")
        meta["current_player_stats_status"] = current_player_status
        observed = (
            f"status={current_player_status}, open_player_prop_rows={keys.get('open_player_prop_rows')}, "
            f"completed_player_appearance_rows={keys.get('completed_player_appearance_rows')}, "
            f"shrink_strength_appearances={keys.get('shrink_strength_appearances')}"
        )
        if current_player_status in {"COMPLETE", "NO_OPEN_PLAYER_ROWS"}:
            status = "PASS"
        elif current_player_status in {"PARTIAL", "NO_MATCHED_CURRENT_PLAYER_STATS", "NO_COMPLETED_PLAYER_STATS"}:
            status = "WARN"
        else:
            status = "FAIL"
        checks.append(
            ReadinessCheck(
                "current_player_stats_audit",
                status,
                observed,
                "current ESPN player-stat source audit generated for open player props",
                "run current_player_stats_audit.py --start-date 2026-06-11 --end-date YYYY-MM-DD --sports-predict --write",
            )
        )
    else:
        meta["current_player_stats_status"] = "missing"
        checks.append(
            ReadinessCheck(
                "current_player_stats_audit",
                "WARN",
                "missing",
                "current_player_stats_audit.md generated as a player-prop source check",
                "run current_player_stats_audit.py --start-date 2026-06-11 --end-date YYYY-MM-DD --sports-predict --write",
            )
        )

    current_player_replay_path = log_dir / "current_player_stats_replay.md"
    if current_player_replay_path.exists():
        current_player_replay = _read(current_player_replay_path)
        keys = _key_values(current_player_replay)
        replay_status = keys.get("current_player_stats_replay_status", "unknown")
        replay_decision = keys.get("decision", "unknown")
        meta["current_player_stats_replay_status"] = replay_status
        meta["current_player_stats_replay_decision"] = replay_decision
        observed = (
            f"status={replay_status}, decision={replay_decision}, "
            f"settled={keys.get('settled_player_rows')}, matched={keys.get('matched_replay_rows')}, "
            f"best={keys.get('best_policy')}, delta={keys.get('best_delta_vs_raw')}, "
            f"ci_2.5={keys.get('best_bootstrap_ci_2.5')}, pr_gt_0={keys.get('best_bootstrap_pr_gt_0')}"
        )
        if replay_status == "COMPLETE" and replay_decision in {
            "NOT_PROMOTED_DIAGNOSTIC_ONLY",
            "PROMOTION_CANDIDATE_REVIEW",
            "NOT_PROMOTED_TOO_SPARSE",
        }:
            status = "PASS"
        elif replay_status == "NO_MATCHED_REPLAY_ROWS":
            status = "WARN"
        else:
            status = "FAIL"
        checks.append(
            ReadinessCheck(
                "current_player_stats_replay",
                status,
                observed,
                "leakage-safe current-player-stat replay generated with an explicit promotion decision",
                "run current_player_stats_replay.py --start-date 2026-06-11 --end-date YYYY-MM-DD --sports-predict --write before promoting current-form player transforms",
            )
        )
    else:
        meta["current_player_stats_replay_status"] = "missing"
        meta["current_player_stats_replay_decision"] = "missing"
        checks.append(
            ReadinessCheck(
                "current_player_stats_replay",
                "WARN",
                "missing",
                "current_player_stats_replay.md generated as the player current-form promotion gate",
                "run current_player_stats_replay.py --start-date 2026-06-11 --end-date YYYY-MM-DD --sports-predict --write",
            )
        )

    lineup_source_path = log_dir / "lineup_source_audit.md"
    if lineup_source_path.exists():
        lineup_source = _read(lineup_source_path)
        keys = _key_values(lineup_source)
        lineup_status = keys.get("lineup_source_status", "unknown")
        meta["lineup_source_status"] = lineup_status
        observed = (
            f"status={lineup_status}, player_matches={keys.get('player_matches')}, "
            f"pulled={keys.get('pulled_matches')}, not_posted={keys.get('not_posted_matches')}, "
            f"in_window_not_posted={keys.get('in_window_not_posted_matches')}, "
            f"unmatched={keys.get('unmatched_players')}, no_event={keys.get('no_event_matches')}"
        )
        if lineup_status in {"COMPLETE", "NO_OPEN_PLAYER_ROWS"}:
            status = "PASS"
        elif lineup_status == "LINEUP_SOURCE_ISSUES":
            status = "FAIL"
        else:
            status = "WARN"
        checks.append(
            ReadinessCheck(
                "lineup_source_audit",
                status,
                observed,
                "ESPN lineup availability is explicitly checked for open player-prop matches",
                "run lineup_source_audit.py --sports-predict --all-open --write near kickoff; use --lineup-write only when posted lineups are verified",
            )
        )
    else:
        meta["lineup_source_status"] = "missing"
        checks.append(
            ReadinessCheck(
                "lineup_source_audit",
                "WARN",
                "missing",
                "lineup_source_audit.md generated for player-prop matches",
                "run lineup_source_audit.py --sports-predict --all-open --write before relying on player rows",
            )
        )

    special_count_source_path = log_dir / "special_count_source_audit.md"
    if special_count_source_path.exists():
        special_count_source = _read(special_count_source_path)
        keys = _key_values(special_count_source)
        special_count_status = keys.get("special_count_source_status", "unknown")
        meta["special_count_source_status"] = special_count_status
        observed = (
            f"status={special_count_status}, required={keys.get('required_rows')}, "
            f"two_source={keys.get('complete_two_source_rows')}, single={keys.get('single_source_rows')}, "
            f"blank={keys.get('blank_rows')}, invalid={keys.get('invalid_rows')}, "
            f"high_missing_two_source={keys.get('high_missing_two_source_rows')}, "
            f"public_no_usable_exact={keys.get('no_usable_public_exact_rows')}, "
            f"public_near_miss={keys.get('near_miss_public_rows')}"
        )
        if special_count_status in {"COMPLETE", "NO_SPECIAL_COUNT_ROWS"}:
            status = "PASS"
        elif special_count_status == "PARTIAL_SINGLE_SOURCE_RECHECKS":
            status = "WARN"
        elif special_count_status in {"INCOMPLETE", "MISSING_SOURCE_FILE"}:
            status = "WARN"
        else:
            status = "FAIL"
        checks.append(
            ReadinessCheck(
                "special_count_source_audit",
                status,
                observed,
                "special count-threshold proxy rows have independent current source checks or no such rows exist",
                "run special_market_live_audit.py --all-open --write --count-source-template, fill sources, then run special_count_source_audit.py --write",
            )
        )
    else:
        meta["special_count_source_status"] = "missing"
        checks.append(
            ReadinessCheck(
                "special_count_source_audit",
                "WARN",
                "missing",
                "special_count_source_audit.md generated from the current source template",
                "run special_market_live_audit.py --all-open --write --count-source-template and special_count_source_audit.py --write",
            )
        )

    special_count_discovery_path = log_dir / "special_count_source_discovery.md"
    if special_count_discovery_path.exists():
        special_count_discovery = _read(special_count_discovery_path)
        keys = _key_values(special_count_discovery)
        discovery_status = keys.get("decision", "unknown")
        meta["special_count_source_discovery_status"] = discovery_status
        observed = (
            f"decision={discovery_status}, exact_usable={keys.get('exact_usable_rows')}, "
            f"near_miss={keys.get('near_miss_rows')}, no_exact={keys.get('no_exact_market_rows')}, "
            f"polymarket={keys.get('polymarket_status')}"
        )
        status = "WARN" if discovery_status.startswith("DISCOVERY") or discovery_status == "NO_SPECIAL_COUNT_ROWS" else "FAIL"
        checks.append(
            ReadinessCheck(
                "special_count_source_discovery",
                status,
                observed,
                "public-market discovery distinguishes exact count sources from near-misses",
                "run special_count_source_discovery.py --write after generating special_count_sources_template.csv",
            )
        )
    else:
        meta["special_count_source_discovery_status"] = "missing"
        checks.append(
            ReadinessCheck(
                "special_count_source_discovery",
                "WARN",
                "missing",
                "special_count_source_discovery.md generated for unresolved count-threshold rows",
                "run special_count_source_discovery.py --write after generating special_count_sources_template.csv",
            )
        )

    current_stats_path = log_dir / "current_tournament_stats_audit.md"
    if current_stats_path.exists():
        current_stats = _read(current_stats_path)
        keys = _key_values(current_stats)
        stats_status = keys.get("current_tournament_stats_status", "unknown")
        meta["current_tournament_stats_status"] = stats_status
        observed = (
            f"status={stats_status}, completed_matches={keys.get('completed_matches')}, "
            f"team_match_rows={keys.get('team_match_rows')}, open_count_source_rows={keys.get('open_count_source_rows')}, "
            f"special_count_csv_source1_rows_written={keys.get('special_count_csv_source1_rows_written')}"
        )
        if stats_status in {"COMPLETE", "NO_OPEN_COUNT_ROWS"}:
            status = "PASS"
        elif stats_status == "NO_COMPLETED_MATCH_STATS":
            status = "FAIL"
        else:
            status = "WARN"
        checks.append(
            ReadinessCheck(
                "current_tournament_stats_audit",
                status,
                observed,
                "current ESPN tournament box-score source audit generated",
                "run current_tournament_stats_audit.py --sports-predict --write --update-special-count-csv",
            )
        )
    else:
        meta["current_tournament_stats_status"] = "missing"
        checks.append(
            ReadinessCheck(
                "current_tournament_stats_audit",
                "WARN",
                "missing",
                "current_tournament_stats_audit.md generated",
                "run current_tournament_stats_audit.py --sports-predict --write --update-special-count-csv",
            )
        )

    match_lock_path = log_dir / "match_lock_readiness_audit.md"
    if match_lock_path.exists():
        match_lock = _read(match_lock_path)
        keys = _key_values(match_lock)
        match_lock_decision = keys.get("decision", "unknown")
        meta["match_lock_decision"] = match_lock_decision
        observed = (
            f"decision={match_lock_decision}, matches={keys.get('matches')}, "
            f"hard_required_rows={keys.get('hard_required_rows')}, "
            f"recheck_rows={keys.get('recheck_rows')}, "
            f"low_confidence_rows={keys.get('low_confidence_rows')}"
        )
        if match_lock_decision == "LOCK_READY":
            status = "PASS"
        elif match_lock_decision in {
            "LOCK_BLOCKED_BY_REQUIRED_INPUTS",
            "LOCK_RECHECK_REQUIRED",
            "LOCK_LOW_CONFIDENCE_MONITOR",
        }:
            status = "WARN"
        else:
            status = "FAIL"
        checks.append(
            ReadinessCheck(
                "match_lock_readiness",
                status,
                observed,
                "match-level lock audit generated; hard blockers cleared before final manual entry",
                "run match_lock_readiness_audit.py --write after evidence coverage and clear hard/recheck rows before lock",
            )
        )
    else:
        meta["match_lock_decision"] = "missing"
        checks.append(
            ReadinessCheck(
                "match_lock_readiness",
                "WARN",
                "missing",
                "match_lock_readiness_audit.md generated from manual evidence coverage",
                "run match_lock_readiness_audit.py --write before treating a match as locked",
            )
        )

    submission_packet_path = log_dir / "manual_submission_packet_audit.md"
    if submission_packet_path.exists():
        submission_packet = _read(submission_packet_path)
        keys = _key_values(submission_packet)
        packet_status = keys.get("submission_packet_status", "unknown")
        packet_now_research = sum(
            _status_count(submission_packet, label)
            for label in (
                "NOW_PROP_ROLE_RESEARCH_THEN_LINEUP_RECHECK",
                "NOW_COUNT_SOURCE_RESEARCH",
                "NOW_STRATEGY_REVIEW",
            )
        )
        packet_near_lock = sum(
            _status_count(submission_packet, label)
            for label in (
                "NEAR_LOCK_ANCHOR_RECHECK",
                "NEAR_LOCK_COUNT_SOURCE_RECHECK",
                "NEAR_LOCK_LIMITED_SUPPORT_RECHECK",
                "NEAR_LOCK_MONITOR_ONLY",
                "NEAR_LOCK_PROP_LINEUP_RECHECK",
                "NEAR_LOCK_RESEARCHED_SOURCE_RECHECK",
            )
        )
        packet_low_confidence_action = sum(
            _status_count(submission_packet, label)
            for label in (
                "LOW_CONFIDENCE_GUARDRAIL_ENTRY",
                "LOW_CONFIDENCE_PROXY_ENTRY",
            )
        )
        packet_no_field = _status_count(submission_packet, "NO_FIELD_ACTION_PRELOCK")
        meta["submission_packet_status"] = packet_status
        meta["submission_packet_now_research_rows"] = str(packet_now_research)
        meta["submission_packet_near_lock_recheck_rows"] = str(packet_near_lock)
        meta["submission_packet_no_field_rows"] = str(packet_no_field)
        meta["submission_packet_low_confidence_action_rows"] = str(packet_low_confidence_action)
        observed = (
            f"status={packet_status}, matches={keys.get('matches')}, markets={keys.get('markets')}, "
            f"ready={keys.get('ready_rows')}, required={keys.get('required_input_rows')}, "
            f"recheck={keys.get('recheck_rows')}, low_confidence={keys.get('low_confidence_rows')}, "
            f"now_research={packet_now_research}, near_lock={packet_near_lock}, "
            f"no_field={packet_no_field}, low_conf_action={packet_low_confidence_action}"
        )
        if packet_status == "READY_TO_SUBMIT":
            status = "PASS"
        elif packet_status in {
            "NOT_LOCKED_REQUIRED_INPUTS",
            "NOT_LOCKED_RECHECKS",
            "READY_WITH_LOW_CONFIDENCE_ROWS",
            "NOT_LOCKED_REVIEW",
        }:
            status = "WARN"
        else:
            status = "FAIL"
        checks.append(
            ReadinessCheck(
                "manual_submission_packet",
                status,
                observed,
                "manual sheet row-level submission readiness is explicit",
                "run manual_submission_packet_audit.py --write after manual_evidence_coverage.py and before treating entries as locked",
            )
        )
    else:
        meta["submission_packet_status"] = "missing"
        checks.append(
            ReadinessCheck(
                "manual_submission_packet",
                "WARN",
                "missing",
                "manual_submission_packet_audit.md generated from the evidence coverage log",
                "run manual_submission_packet_audit.py --write before treating entries as locked",
            )
        )

    predictions_path = log_dir / "manual_predictions.md"
    if predictions_path.exists():
        predictions = _read(predictions_path)
        section = _latest_manual_section(predictions, local_date)
        match_count = _manual_sheet_match_count(section)
        meta["manual_sheet_matches"] = str(match_count)
        bot_ok = "bot_predictions: not_read" in section
        api_ok = "sports_predict:" in section and "read_only" in section
        status = "PASS" if section and match_count > 0 and bot_ok and api_ok else "FAIL"
        checks.append(
            ReadinessCheck(
                "manual_prediction_sheet",
                status,
                f"sections={'yes' if section else 'no'}, matches={match_count}, bot_ok={bot_ok}, api_ok={api_ok}",
                f"latest full_manual_pricing_v2 section for {local_date} with matches and read-only guardrails",
                "run full_manual_pricing.py for the local date and append to manual_predictions.md",
            )
        )
        if section and "field_csv: not_supplied" in section:
            checks.append(
                ReadinessCheck(
                    "field_csv_visibility",
                    "PASS",
                    "field_csv: not_supplied",
                    "field averages unavailable before prediction lock",
                    "do not supply or fabricate field_csv pre-lock",
                )
            )
    else:
        checks.append(ReadinessCheck("manual_prediction_sheet", "FAIL", "missing", "manual_predictions.md exists", "run full_manual_pricing.py"))

    if automation_file.exists():
        data = tomllib.loads(_read(automation_file))
        prompt = _automation_prompt(data)
        required_terms = [
            "docs/manual_strategy.md",
            "docs/strategies.md",
            "docs/full_research_program.md",
            "full_manual_pricing.py",
            "brier_decomposition_audit.py",
            "current_player_stats_audit.py",
            "current_player_stats_replay.py",
            "current_tournament_stats_audit.py",
            "lineup_source_audit.py",
            "special_count_source_discovery.py",
            "match_lock_readiness_audit.py",
            "manual_submission_packet_audit.py",
            "manual_workflow_readiness_audit.py",
            "manual_predictions.md",
            "Do not submit",
            "/predictions is not part",
        ]
        missing_terms = [term for term in required_terms if term not in prompt]
        status = "PASS" if data.get("status") == "ACTIVE" and data.get("kind") == "heartbeat" and not missing_terms else "FAIL"
        meta["automation_status"] = str(data.get("status"))
        checks.append(
            ReadinessCheck(
                "main_chat_automation",
                status,
                f"status={data.get('status')}, kind={data.get('kind')}, missing_terms={','.join(missing_terms) or '-'}",
                "ACTIVE heartbeat with manual-only prompt",
                "update the in-chat automation prompt before relying on scheduled runs",
            )
        )
        rrule = _automation_rrule(data)
        hours = _rrule_hours(rrule)
        minutes = _rrule_minutes(rrule)
        schedule_ok = (
            data.get("status") == "ACTIVE"
            and data.get("kind") == "heartbeat"
            and bool(data.get("target_thread_id"))
            and {9, 23}.issubset(hours)
            and {0, 30}.issubset(minutes)
        )
        meta["automation_schedule_status"] = "ACTIVE" if schedule_ok else "CHECK"
        checks.append(
            ReadinessCheck(
                "main_chat_automation_schedule",
                "PASS" if schedule_ok else "FAIL",
                (
                    f"status={data.get('status')}, kind={data.get('kind')}, "
                    f"target_thread={'yes' if data.get('target_thread_id') else 'no'}, "
                    f"hours={','.join(str(hour) for hour in sorted(hours)) or '-'}, "
                    f"minutes={','.join(str(minute) for minute in sorted(minutes)) or '-'}"
                ),
                "active thread heartbeat covering 09:00, 23:00, and half-hour pre-match polling",
                "update the in-chat heartbeat rrule to include 9 AM, 11 PM, and 30-minute match-window checks",
            )
        )
    else:
        checks.append(
            ReadinessCheck(
                "main_chat_automation",
                "FAIL",
                str(automation_file),
                "automation file exists",
                "create/update the probability-cup in-chat heartbeat automation",
            )
        )

    automation_inventory_path = log_dir / "automation_inventory_audit.md"
    if automation_inventory_path.exists():
        inventory = _read(automation_inventory_path)
        keys = _key_values(inventory)
        inventory_decision = keys.get("decision", "unknown")
        meta["automation_inventory_decision"] = inventory_decision
        observed = (
            f"decision={inventory_decision}, "
            f"active_duplicates={keys.get('active_stale_or_duplicate_count')}, "
            f"paused_legacy={keys.get('paused_legacy_count')}, "
            f"primary_status={keys.get('primary_status')}, "
            f"primary_schedule={keys.get('primary_schedule_status')}, "
            f"primary_prompt={keys.get('primary_prompt_status')}"
        )
        checks.append(
            ReadinessCheck(
                "automation_inventory",
                "PASS" if inventory_decision == "PRIMARY_HEARTBEAT_READY" else "FAIL",
                observed,
                "primary main-chat heartbeat ready and no active stale duplicate automations",
                "run automation_inventory_audit.py --write and pause/update any active stale standalone cron",
            )
        )
    else:
        meta["automation_inventory_decision"] = "missing"
        checks.append(
            ReadinessCheck(
                "automation_inventory",
                "WARN",
                "missing",
                "automation inventory report generated",
                "run automation_inventory_audit.py --write",
            )
        )

    failures = sum(check.status == "FAIL" for check in checks)
    warnings = sum(check.status == "WARN" for check in checks)
    meta["failures"] = str(failures)
    meta["warnings"] = str(warnings)
    if failures:
        decision = "NOT_READY"
    elif warnings:
        decision = "READY_WITH_RESEARCH_QUEUE"
    else:
        decision = "READY"
    return decision, checks, meta


def _table(rows: list[ReadinessCheck]) -> list[str]:
    lines = [
        "| check | status | observed | required | action |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(f"| {row.check} | {row.status} | {row.observed} | {row.required} | {row.action} |")
    return lines


def render(decision: str, checks: list[ReadinessCheck], meta: dict[str, str], *, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    lines = [
        "# Manual Workflow Readiness Audit",
        "",
        "Read-only analysis. `/predictions` was not read and no submissions were made.",
        "",
        f"- run_utc: {now.astimezone(timezone.utc).isoformat(timespec='seconds')}",
        "- sports_predict: not_called",
        "- bot_predictions: not_read",
        f"- local_date: {meta.get('local_date', '?')}",
        f"- decision: {decision}",
        f"- failures: {meta.get('failures', '0')}",
        f"- warnings: {meta.get('warnings', '0')}",
        f"- manual_sheet_matches: {meta.get('manual_sheet_matches', '0')}",
        f"- research_high_rows: {meta.get('research_high_rows', '0')}",
        f"- research_medium_rows: {meta.get('research_medium_rows', '0')}",
        f"- field_capture_status: {meta.get('field_capture_status', 'unknown')}",
        f"- player_prop_anchor_status: {meta.get('player_prop_anchor_status', 'unknown')}",
        f"- current_prop_anchor_status: {meta.get('current_prop_anchor_status', 'unknown')}",
        f"- current_player_stats_status: {meta.get('current_player_stats_status', 'unknown')}",
        f"- current_player_stats_replay_status: {meta.get('current_player_stats_replay_status', 'unknown')}",
        f"- current_player_stats_replay_decision: {meta.get('current_player_stats_replay_decision', 'unknown')}",
        f"- lineup_source_status: {meta.get('lineup_source_status', 'unknown')}",
        f"- special_count_source_status: {meta.get('special_count_source_status', 'unknown')}",
        f"- special_count_source_discovery_status: {meta.get('special_count_source_discovery_status', 'unknown')}",
        f"- current_tournament_stats_status: {meta.get('current_tournament_stats_status', 'unknown')}",
        f"- match_lock_decision: {meta.get('match_lock_decision', 'unknown')}",
        f"- submission_packet_status: {meta.get('submission_packet_status', 'unknown')}",
        f"- submission_packet_now_research_rows: {meta.get('submission_packet_now_research_rows', '0')}",
        f"- submission_packet_near_lock_recheck_rows: {meta.get('submission_packet_near_lock_recheck_rows', '0')}",
        f"- submission_packet_no_field_rows: {meta.get('submission_packet_no_field_rows', '0')}",
        f"- submission_packet_low_confidence_action_rows: {meta.get('submission_packet_low_confidence_action_rows', '0')}",
        f"- automation_status: {meta.get('automation_status', 'unknown')}",
        f"- automation_schedule_status: {meta.get('automation_schedule_status', 'unknown')}",
        f"- automation_inventory_decision: {meta.get('automation_inventory_decision', 'unknown')}",
        "",
        "## Checks",
        "",
        *_table(checks),
        "",
        "## Interpretation",
        "",
        "- `READY_WITH_RESEARCH_QUEUE` means the machinery is usable but live HIGH rows still need lineup, prop, or source research before lock; field averages are unavailable pre-lock.",
        "- `NOT_READY` means a required evidence, prediction-sheet, or automation guardrail is missing or stale.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local-date", required=True, help="America/Chicago local date, YYYY-MM-DD")
    parser.add_argument("--automation-file", type=Path, default=DEFAULT_AUTOMATION)
    parser.add_argument("--write", action="store_true", help=f"write {OUT}")
    args = parser.parse_args()

    decision, checks, meta = build_readiness(local_date=args.local_date, automation_file=args.automation_file)
    text = render(decision, checks, meta)
    print(text)
    if args.write:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(text + "\n", encoding="utf-8")
        print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
