"""Audit whether strategy evidence artifacts are internally fresh.

This is a read-only report over generated logs. It does not call SportsPredict,
does not read `/predictions`, and does not submit anything. The purpose is to
catch stale or inconsistent strategy artifacts before using
`strategy_evidence_matrix.md` as an operating summary.

Run the read-only API-backed reports first, then run this cheap consistency
check:

    python3 scripts/evidence_freshness_audit.py --write
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "data" / "logs"
OUT = LOG_DIR / "evidence_freshness_audit.md"

sys.path.insert(0, str(ROOT / "src"))

from manual.policy import COUNT_FAMILIES, ODDS_GOAL_FAMILIES  # noqa: E402
from questions.parser import parse  # noqa: E402
from questions.teams import ALL_TEAM_NAMES  # noqa: E402


COUNT_STATS = {"cards", "corners", "fouls", "offsides", "sot"}
PENALTY_FAMILIES = {"penalty_awarded", "penalty_or_red", "red_card"}
TOTAL_GOAL_SOURCE_FAMILIES = {"total_goals", "team_goals_ge"}
MATRIX_TIMESTAMP_SOURCES = [
    "manual_family_blend_backtest.md",
    "brier_decomposition_audit.md",
    "manual_calibration_watchlist.md",
    "odds_anchor_audit.md",
    "odds_conversion_backtest.md",
    "elo_history_residual_audit.md",
    "odds_total_source_audit.md",
    "count_intensity_replay.md",
    "count_intensity_policy_replay.md",
    "count_total_dependence_audit.md",
    "count_total_dependence_policy_replay.md",
    "card_field_component_audit.md",
    "referee_research_audit.md",
    "referee_card_adjustment_audit.md",
    "player_role_research_audit.md",
    "player_role_policy_backtest.md",
    "current_player_stats_replay.md",
    "player_prop_boundary_audit.md",
    "player_minutes_uncertainty_audit.md",
    "player_sot_rate_audit.md",
    "player_goal_involvement_rate_audit.md",
    "special_market_live_audit.md",
    "special_prior_shrinkage_backtest.md",
    "special_boundary_audit.md",
    "special_count_threshold_audit.md",
    "polymarket_direct_coverage_audit.md",
    "polymarket_direct_snapshot_score.md",
    "polymarket_direct_anchor_risk_audit.md",
    "leaderboard_tail_risk.md",
]


@dataclass(frozen=True)
class FreshnessRow:
    artifact: str
    status: str
    observed: str
    expected: str
    reason: str


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _clean(value: str) -> str:
    return value.strip().strip("`")


def _key_values(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        match = re.match(r"^- ([A-Za-z0-9_ ./%>=#-]+):\s*(.*)$", line)
        if match:
            out[match.group(1).strip()] = match.group(2).strip()
    return out


def _table_after(text: str, heading: str) -> list[dict[str, str]]:
    lines = text.splitlines()
    start = None
    for idx, line in enumerate(lines):
        if re.match(rf"^##+\s+{re.escape(heading)}\s*$", line.strip()):
            start = idx + 1
            break
    if start is None:
        return []
    while start < len(lines) and not lines[start].lstrip().startswith("|"):
        start += 1
    if start >= len(lines):
        return []
    headers = [_clean(part) for part in lines[start].strip().strip("|").split("|")]
    rows: list[dict[str, str]] = []
    for line in lines[start + 2 :]:
        if not line.lstrip().startswith("|"):
            break
        parts = [_clean(part) for part in line.strip().strip("|").split("|")]
        if len(parts) == len(headers):
            rows.append(dict(zip(headers, parts)))
    return rows


def _tables_after(text: str, heading: str) -> list[list[dict[str, str]]]:
    lines = text.splitlines()
    start = None
    for idx, line in enumerate(lines):
        if re.match(rf"^##+\s+{re.escape(heading)}\s*$", line.strip()):
            start = idx + 1
            break
    if start is None:
        return []
    end = len(lines)
    for idx in range(start, len(lines)):
        if lines[idx].startswith("##"):
            end = idx
            break

    tables: list[list[dict[str, str]]] = []
    idx = start
    while idx < end:
        if not lines[idx].lstrip().startswith("|"):
            idx += 1
            continue
        headers = [_clean(part) for part in lines[idx].strip().strip("|").split("|")]
        table: list[dict[str, str]] = []
        idx += 2
        while idx < end and lines[idx].lstrip().startswith("|"):
            parts = [_clean(part) for part in lines[idx].strip().strip("|").split("|")]
            if len(parts) == len(headers):
                table.append(dict(zip(headers, parts)))
            idx += 1
        if table:
            tables.append(table)
    return tables


def _table_with_headers(text: str, heading: str, required_headers: set[str]) -> list[dict[str, str]]:
    for table in _tables_after(text, heading):
        if table and required_headers.issubset(table[0].keys()):
            return table
    return []


def _int(value: str | None, default: int = 0) -> int:
    if value is None:
        return default
    value = value.strip().replace(",", "")
    if value in {"", "-"}:
        return default
    return int(float(value))


def _parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _fam_parts(famkey: str) -> tuple[str, str]:
    parts = famkey.split("/")
    family = parts[0] if parts else ""
    stat = parts[1] if len(parts) > 1 else ""
    return family, stat


def expected_family_counts(integrity_text: str) -> dict[str, int]:
    counts = {"odds_goal": 0, "count": 0, "player": 0, "penalty_red": 0}
    for row in _table_after(integrity_text, "Safe Exact Rows By Family"):
        family, stat = _fam_parts(row.get("family", ""))
        n = _int(row.get("rows"))
        if family.startswith("player_"):
            counts["player"] += n
        elif family in PENALTY_FAMILIES:
            counts["penalty_red"] += n
        elif family in ODDS_GOAL_FAMILIES:
            counts["odds_goal"] += n
        elif family in COUNT_FAMILIES and stat in COUNT_STATS:
            counts["count"] += n
    return counts


def _status(observed: int, expected: int, *, zero_ok: bool = False) -> str:
    if observed == expected and (zero_ok or expected > 0):
        return "FRESH"
    return "STALE_OR_INCONSISTENT"


def _log_keys(log_dir: Path, filename: str) -> dict[str, str] | None:
    path = log_dir / filename
    if not path.exists():
        return None
    return _key_values(_read(path))


def _table_total(log_dir: Path, filename: str, heading: str, value_col: str) -> int | None:
    path = log_dir / filename
    if not path.exists():
        return None
    rows = _table_after(_read(path), heading)
    if not rows:
        return None
    return sum(_int(row.get(value_col)) for row in rows)


def _table_rows(log_dir: Path, filename: str, heading: str) -> list[dict[str, str]] | None:
    path = log_dir / filename
    if not path.exists():
        return None
    rows = _table_after(_read(path), heading)
    if not rows:
        return None
    return rows


def _table_counts(log_dir: Path, filename: str, heading: str, label_col: str, value_col: str) -> dict[str, int] | None:
    path = log_dir / filename
    if not path.exists():
        return None
    rows = _table_after(_read(path), heading)
    if not rows:
        return None
    return {row.get(label_col, ""): _int(row.get(value_col)) for row in rows}


def _table_counts_with_headers(
    log_dir: Path,
    filename: str,
    heading: str,
    label_col: str,
    value_col: str,
) -> dict[str, int] | None:
    path = log_dir / filename
    if not path.exists():
        return None
    rows = _table_with_headers(_read(path), heading, {label_col, value_col})
    if not rows:
        return None
    return {row.get(label_col, ""): _int(row.get(value_col)) for row in rows}


def _is_total_goal_source_question(question: str) -> bool:
    try:
        spec = parse(question, teams=ALL_TEAM_NAMES)
    except Exception:
        return False
    return spec.family in TOTAL_GOAL_SOURCE_FAMILIES and spec.stat == "goals"


def build_rows(log_dir: Path = LOG_DIR) -> tuple[list[FreshnessRow], dict[str, str]]:
    integrity_path = log_dir / "backtest_integrity_audit.md"
    if not integrity_path.exists():
        return (
            [
                FreshnessRow(
                    "backtest_integrity_audit.md",
                    "MISSING",
                    "-",
                    "present",
                    "Run `uv run --with requests python scripts/backtest_integrity_audit.py --sports-predict --write`.",
                )
            ],
            {},
        )

    integrity_text = _read(integrity_path)
    integrity = _key_values(integrity_text)
    expected = expected_family_counts(integrity_text)
    safe_exact = _int(integrity.get("safe_exact_rows_for_strategy_scoring"))
    source_results = _int(integrity.get("sports_predict_results_rows"))
    unsafe_exact = _int(integrity.get("unsafe_question_overwrite_exact_rows"))
    meta = {
        "source_results_rows": str(source_results),
        "safe_exact_rows": str(safe_exact),
        "unsafe_question_overwrite_exact_rows": str(unsafe_exact),
        "blocked_by_safe_join": str(max(0, unsafe_exact - safe_exact)),
        **{f"expected_{key}_rows": str(value) for key, value in expected.items()},
    }

    rows: list[FreshnessRow] = [
        FreshnessRow(
            "backtest_integrity_audit.md",
            "FRESH",
            f"results={source_results}, safe_exact={safe_exact}",
            "reference",
            "Reference report for exact-history sample size and V1 safe-settlement limits.",
        )
    ]

    exact_reports = [
        ("manual_policy_backtest.md", "settled_rows", safe_exact, "combined manual policy rows must match safe exact rows"),
        (
            "manual_family_blend_backtest.md",
            "settled_rows",
            safe_exact,
            "family blend rows must match safe exact rows",
        ),
        (
            "brier_decomposition_audit.md",
            "settled_rows",
            safe_exact,
            "Brier decomposition rows must match safe exact rows",
        ),
        ("manual_calibration_backtest.md", "settled_rows", safe_exact, "calibration rows must match safe exact rows"),
        ("goal_policy_backtest.md", "settled_goal_rows", expected["odds_goal"], "goal rows must match safe exact odds-goal rows"),
        ("count_policy_backtest.md", "settled_count_rows", expected["count"], "count rows must match safe exact count rows"),
        ("count_no_field_policy_backtest.md", "settled_count_rows", expected["count"], "count no-field rows must match safe exact count rows"),
        ("player_policy_backtest.md", "settled_player_rows", expected["player"], "player rows must match safe exact player rows"),
        ("player_no_field_policy_backtest.md", "settled_player_rows", expected["player"], "player no-field rows must match safe exact player rows"),
        (
            "penalty_red_policy_backtest.md",
            "settled_penalty_red_rows",
            expected["penalty_red"],
            "penalty/red rows must match safe exact penalty/red rows",
        ),
    ]
    for filename, key, expected_value, reason in exact_reports:
        keys = _log_keys(log_dir, filename)
        if keys is None:
            rows.append(FreshnessRow(filename, "MISSING", "-", str(expected_value), f"Missing report: {reason}."))
            continue
        observed = _int(keys.get(key))
        rows.append(FreshnessRow(filename, _status(observed, expected_value), str(observed), str(expected_value), reason))

    result_reports = [
        (
            "special_official_results_audit.md",
            "sports_predict_results_rows",
            "official special audit should use the same latest /results feed as the integrity audit",
        ),
        (
            "polymarket_direct_snapshot_score.md",
            "sports_predict_results_rows",
            "Polymarket snapshot scorer should use the same latest /results feed as the integrity audit",
        ),
    ]
    for filename, key, reason in result_reports:
        keys = _log_keys(log_dir, filename)
        if keys is None:
            rows.append(FreshnessRow(filename, "MISSING", "-", str(source_results), f"Missing report: {reason}."))
            continue
        observed = _int(keys.get(key))
        rows.append(FreshnessRow(filename, _status(observed, source_results), str(observed), str(source_results), reason))

    odds_conversion = _log_keys(log_dir, "odds_conversion_backtest.md")
    if odds_conversion is None:
        rows.append(
            FreshnessRow(
                "odds_conversion_backtest.md",
                "MISSING",
                "-",
                "historical odds conversion gate",
                "Odds-conversion research gate must exist before changing bookmaker de-margin logic.",
            )
        )
    else:
        rows_1x2 = _int(odds_conversion.get("rows_1x2"))
        rows_ou = _int(odds_conversion.get("rows_over_under_2.5"))
        decision = odds_conversion.get("decision", "")
        meta["odds_conversion_1x2_rows"] = str(rows_1x2)
        meta["odds_conversion_ou_rows"] = str(rows_ou)
        status = (
            "FRESH"
            if rows_1x2 > 0 and rows_ou > 0 and (
                decision == "not_promoted_keep_multiplicative_devig"
                or decision.startswith("promote_")
            )
            else "STALE_OR_INCONSISTENT"
        )
        rows.append(
            FreshnessRow(
                "odds_conversion_backtest.md",
                status,
                f"1x2={rows_1x2}, ou={rows_ou}, decision={decision or 'missing'}",
                "positive rows and explicit decision",
                "Odds-conversion report must include both 1X2 and total-goals historical gates.",
            )
        )

    elo_history = _log_keys(log_dir, "elo_history_residual_audit.md")
    if elo_history is None:
        rows.append(
            FreshnessRow(
                "elo_history_residual_audit.md",
                "MISSING",
                "-",
                "international Elo-history research gate",
                "Elo-history residual research must exist before using Elo as even a no-odds fallback.",
            )
        )
    else:
        source_rows = _int(elo_history.get("source_rows"))
        scored_rows = _int(elo_history.get("scored_rows"))
        decision = elo_history.get("decision", "")
        meta["elo_history_scored_rows"] = str(scored_rows)
        status = (
            "FRESH"
            if source_rows > 0
            and scored_rows > 0
            and decision
            in {
                "candidate_history_feature_positive_unpriced_only",
                "not_promoted_keep_market_anchored_goal_stack",
            }
            else "STALE_OR_INCONSISTENT"
        )
        rows.append(
            FreshnessRow(
                "elo_history_residual_audit.md",
                status,
                f"source={source_rows}, scored={scored_rows}, decision={decision or 'missing'}",
                "positive rows and explicit decision",
                "Elo-history report is a proxy/no-odds fallback gate, not an odds override.",
            )
        )

    count_total = _log_keys(log_dir, "count_total_dependence_audit.md")
    if count_total is None:
        rows.append(
            FreshnessRow(
                "count_total_dependence_audit.md",
                "MISSING",
                "-",
                "total-count dependence research gate",
                "Total-count dependence must be explicitly checked before changing total-count threshold shape.",
            )
        )
    else:
        source_matches = _int(count_total.get("source_matches"))
        scored_markets = _int(count_total.get("scored_market_rows"))
        scored_predictions = _int(count_total.get("scored_prediction_rows"))
        decision = count_total.get("decision", "")
        meta["count_total_dependence_scored_markets"] = str(scored_markets)
        status = (
            "FRESH"
            if source_matches > 0
            and scored_markets > 0
            and scored_predictions >= scored_markets
            and decision
            in {
                "proxy_candidate_direct_total_nb_positive_not_live_promoted",
                "not_promoted_keep_independent_team_convolution",
            }
            else "STALE_OR_INCONSISTENT"
        )
        rows.append(
            FreshnessRow(
                "count_total_dependence_audit.md",
                status,
                (
                    f"matches={source_matches}, markets={scored_markets}, "
                    f"predictions={scored_predictions}, decision={decision or 'missing'}"
                ),
                "positive rows and explicit decision",
                "Total-count dependence report is a proxy gate; it must not silently become a live total-count override.",
            )
        )

    count_total_policy = _log_keys(log_dir, "count_total_dependence_policy_replay.md")
    if count_total_policy is None:
        rows.append(
            FreshnessRow(
                "count_total_dependence_policy_replay.md",
                "MISSING",
                "-",
                "settled Cup total-count replay gate",
                "Cup total-count dependence replay must exist before promoting direct-total NB beyond proxy evidence.",
            )
        )
    else:
        settled_rows = _int(count_total_policy.get("settled_total_count_rows_requested"))
        matched_contexts = _int(count_total_policy.get("matched_match_contexts"))
        decision = count_total_policy.get("decision", "")
        meta["count_total_dependence_cup_rows"] = str(settled_rows)
        status = (
            "FRESH"
            if settled_rows > 0
            and matched_contexts > 0
            and decision
            in {
                "not_promoted_keep_current_independent_total_count_shape",
                "promote_direct_total_nb_match_only_for_settled_total_count_thresholds",
                "promote_direct_total_nb_all_periods_for_settled_total_count_thresholds",
            }
            else "STALE_OR_INCONSISTENT"
        )
        rows.append(
            FreshnessRow(
                "count_total_dependence_policy_replay.md",
                status,
                f"rows={settled_rows}, contexts={matched_contexts}, decision={decision or 'missing'}",
                "positive rows, contexts, and explicit decision",
                "Cup total-count replay is the SportsPredict relevance gate for direct-total NB.",
            )
        )

    player_minutes = _log_keys(log_dir, "player_minutes_uncertainty_audit.md")
    if player_minutes is None:
        rows.append(
            FreshnessRow(
                "player_minutes_uncertainty_audit.md",
                "MISSING",
                "-",
                "player minutes uncertainty research gate",
                "Player-minutes uncertainty must be explicitly checked before changing player-prop minutes policy.",
            )
        )
    else:
        appearances = _int(player_minutes.get("appearances"))
        matches = _int(player_minutes.get("matches"))
        decision = player_minutes.get("decision", "")
        meta["player_minutes_appearances"] = str(appearances)
        meta["player_minutes_matches"] = str(matches)
        status = (
            "FRESH"
            if appearances > 0
            and matches > 0
            and decision
            in {
                "proxy_candidate_minutes_uncertainty_positive_not_live_promoted",
                "proxy_candidate_no_lineup_player_recent_distribution",
                "proxy_candidate_confirmed_role_distribution",
                "not_promoted_keep_point_minutes_with_lineup_guardrails",
            }
            else "STALE_OR_INCONSISTENT"
        )
        rows.append(
            FreshnessRow(
                "player_minutes_uncertainty_audit.md",
                status,
                f"appearances={appearances}, matches={matches}, decision={decision or 'missing'}",
                "positive rows and explicit decision",
                "Player-minutes report is a proxy gate; it must not silently become a live override.",
            )
        )

    player_sot_rate = _log_keys(log_dir, "player_sot_rate_audit.md")
    if player_sot_rate is None:
        rows.append(
            FreshnessRow(
                "player_sot_rate_audit.md",
                "MISSING",
                "-",
                "player SOT-rate proxy gate",
                "Player SOT shrinkage must be explicitly checked before changing player-SOT constants.",
            )
        )
    else:
        market_like = _int(player_sot_rate.get("market_like_rows"))
        scored = _int(player_sot_rate.get("chronological_scored_rows"))
        matches = _int(player_sot_rate.get("chronological_matches"))
        decision = player_sot_rate.get("decision", "")
        meta["player_sot_rate_market_like_rows"] = str(market_like)
        meta["player_sot_rate_chronological_rows"] = str(scored)
        status = (
            "FRESH"
            if market_like > 0
            and scored > 0
            and matches > 0
            and decision
            in {
                "proxy_candidate_app_shrink_k8_positive_for_1plus_only_not_live_promoted",
                "proxy_candidate_app_shrink_k8_positive_all_sot_thresholds_not_live_promoted",
                "not_promoted_keep_current_sot_shrinkage",
            }
            else "STALE_OR_INCONSISTENT"
        )
        rows.append(
            FreshnessRow(
                "player_sot_rate_audit.md",
                status,
                f"market_like={market_like}, scored={scored}, matches={matches}, decision={decision or 'missing'}",
                "positive rows and explicit decision",
                "Player SOT-rate report is a proxy gate; it must not silently become a live SOT override.",
            )
        )

    player_goal_rate = _log_keys(log_dir, "player_goal_involvement_rate_audit.md")
    if player_goal_rate is None:
        rows.append(
            FreshnessRow(
                "player_goal_involvement_rate_audit.md",
                "MISSING",
                "-",
                "player goal/involvement rate gate",
                "Player goal/involvement shrinkage must be explicitly checked before changing scorer/SOA constants.",
            )
        )
    else:
        market_like = _int(player_goal_rate.get("market_like_rows"))
        scored = _int(player_goal_rate.get("chronological_scored_rows"))
        matches = _int(player_goal_rate.get("chronological_matches"))
        decision = player_goal_rate.get("decision", "")
        meta["player_goal_rate_market_like_rows"] = str(market_like)
        meta["player_goal_rate_chronological_rows"] = str(scored)
        status = (
            "FRESH"
            if market_like > 0
            and scored > 0
            and matches > 0
            and decision
            in {
                "proxy_candidate_goal_involvement_shrinkage_positive_not_live_promoted",
                "not_promoted_keep_current_goal_involvement_shrinkage",
            }
            else "STALE_OR_INCONSISTENT"
        )
        rows.append(
            FreshnessRow(
                "player_goal_involvement_rate_audit.md",
                status,
                f"market_like={market_like}, scored={scored}, matches={matches}, decision={decision or 'missing'}",
                "positive rows and explicit decision",
                "Player goal/involvement rate report must not silently become a scorer/SOA override.",
            )
        )

    referee_adjustment = _log_keys(log_dir, "referee_card_adjustment_audit.md")
    if referee_adjustment is None:
        rows.append(
            FreshnessRow(
                "referee_card_adjustment_audit.md",
                "MISSING",
                "-",
                "referee card adjustment research gate",
                "Referee card adjustments need an explicit historical/proxy gate before live use.",
            )
        )
    else:
        source_matches = _int(referee_adjustment.get("source_matches"))
        scored_rows = _int(referee_adjustment.get("scored_rows"))
        unique_referees = _int(referee_adjustment.get("unique_referees"))
        decision = referee_adjustment.get("decision", "")
        meta["referee_card_source_matches"] = str(source_matches)
        meta["referee_card_unique_referees"] = str(unique_referees)
        status = (
            "FRESH"
            if source_matches > 0
            and scored_rows > 0
            and unique_referees > 0
            and decision
            in {
                "proxy_candidate_referee_threshold_positive_not_live_promoted",
                "proxy_candidate_lenient_referee_cap1_positive_not_live_promoted",
                "not_promoted_keep_field_or_neutral_referee_policy",
            }
            else "STALE_OR_INCONSISTENT"
        )
        rows.append(
            FreshnessRow(
                "referee_card_adjustment_audit.md",
                status,
                f"matches={source_matches}, scored={scored_rows}, refs={unique_referees}, decision={decision or 'missing'}",
                "positive rows and explicit decision",
                "Referee-card adjustment report is a proxy gate, not a live assignment check.",
            )
        )

    target = _log_keys(log_dir, "tournament_target_audit.md")
    known_open_markets: int | None = None
    live_actionable_markets: int | None = None
    research_queue_expected_markets: int | None = None
    if target is None:
        rows.append(
            FreshnessRow(
                "tournament_target_audit.md",
                "MISSING",
                "-",
                "present",
                "Open-market target scale is the reference for live board size.",
            )
        )
    else:
        known_open_markets = _int(target.get("known_open_markets"))
        meta["known_open_markets"] = str(known_open_markets)
        rows.append(
            FreshnessRow(
                "tournament_target_audit.md",
                "FRESH" if known_open_markets > 0 else "STALE_OR_INCONSISTENT",
                str(known_open_markets),
                "positive reference",
                "Reference report for currently exposed open-market board size.",
            )
        )

    if known_open_markets is not None:
        tail = _log_keys(log_dir, "leaderboard_tail_risk.md")
        if tail is None:
            rows.append(
                FreshnessRow(
                    "leaderboard_tail_risk.md",
                    "MISSING",
                    "-",
                    str(known_open_markets),
                    "Tail-risk simulation must use the same current open-market board size.",
                )
            )
        else:
            observed = _int(tail.get("simulated_markets_per_trial"))
            rows.append(
                FreshnessRow(
                    "leaderboard_tail_risk.md",
                    _status(observed, known_open_markets),
                    str(observed),
                    str(known_open_markets),
                    "Tail-risk simulation must use the same current open-market board size.",
                )
            )

        live_counts = _table_counts(log_dir, "live_market_risk_audit.md", "Risk Summary", "tier", "markets")
        if live_counts is None:
            rows.append(
                FreshnessRow(
                    "live_market_risk_audit.md",
                    "MISSING",
                    "-",
                    str(known_open_markets),
                    "Live risk audit risk-tier counts must sum to the current open board.",
                )
            )
        else:
            observed = sum(live_counts.values())
            live_actionable_markets = live_counts.get("HIGH", 0) + live_counts.get("MEDIUM", 0)
            meta["live_risk_markets"] = str(observed)
            meta["live_actionable_markets"] = str(live_actionable_markets)
            rows.append(
                FreshnessRow(
                    "live_market_risk_audit.md",
                    _status(observed, known_open_markets),
                    str(observed),
                    str(known_open_markets),
                    "Live risk audit risk-tier counts must sum to the current open board.",
                )
            )

        open_count_tables = [
            (
                "manual_evidence_coverage.md",
                "Coverage Summary",
                "markets",
                "Evidence coverage rows must sum to the current open board.",
            ),
            (
                "manual_execution_leverage.md",
                "Summary",
                "markets",
                "Execution leverage priorities must sum to the current open board.",
            ),
            (
                "match_lock_readiness_audit.md",
                "Match Summary",
                "markets",
                "Match-lock readiness rows must sum to the current open board.",
            ),
        ]
        for filename, heading, value_col, reason in open_count_tables:
            observed = _table_total(log_dir, filename, heading, value_col)
            if observed is None:
                rows.append(FreshnessRow(filename, "MISSING", "-", str(known_open_markets), reason))
                continue
            rows.append(
                FreshnessRow(
                    filename,
                    _status(observed, known_open_markets),
                    str(observed),
                    str(known_open_markets),
                    reason,
                )
            )

        question_family = _log_keys(log_dir, "question_family_coverage_audit.md")
        if question_family is None:
            rows.append(
                FreshnessRow(
                    "question_family_coverage_audit.md",
                    "MISSING",
                    "-",
                    "complete family ownership audit",
                    "Observed and parser question families must map to documented strategy owners.",
                )
            )
        else:
            family_status = question_family.get("question_family_coverage_status", "")
            observed_keys = _int(question_family.get("observed_family_keys"))
            parser_names = _int(question_family.get("parser_family_names"))
            non_promoted = _int(question_family.get("non_promoted_observed_family_keys"))
            meta["observed_question_family_keys"] = str(observed_keys)
            meta["parser_question_family_names"] = str(parser_names)
            meta["non_promoted_question_family_keys"] = str(non_promoted)
            ok_statuses = {
                "COMPLETE_PROMOTED_OBSERVED_FAMILIES",
                "COMPLETE_WITH_LIMITED_PROXY_OR_GUARDRAIL_ROWS",
            }
            rows.append(
                FreshnessRow(
                    "question_family_coverage_audit.md",
                    "FRESH"
                    if family_status in ok_statuses and observed_keys > 0 and parser_names > 0
                    else "STALE_OR_INCONSISTENT",
                    f"status={family_status or 'missing'}, observed={observed_keys}, parser={parser_names}, non_promoted={non_promoted}",
                    "complete observed/parser family ownership",
                    "Every observed and parser question family must have a documented strategy owner; limited/proxy/guardrail rows must stay labelled.",
                )
            )

        coverage_states = _table_counts_with_headers(
            log_dir,
            "manual_evidence_coverage.md",
            "Coverage Summary",
            "coverage_state",
            "markets",
        )
        leverage_states = _table_counts_with_headers(
            log_dir,
            "manual_execution_leverage.md",
            "Summary",
            "coverage_state",
            "markets",
        )
        if coverage_states is None or leverage_states is None:
            rows.append(
                FreshnessRow(
                    "manual_execution_leverage.md coverage_state",
                    "MISSING",
                    "-" if leverage_states is None else str(leverage_states),
                    "manual_evidence_coverage.md coverage_state",
                    "Execution leverage coverage-state counts must match the coverage audit it reads.",
                )
            )
        else:
            field_unavailable = coverage_states.get("FIELD_UNAVAILABLE_PRELOCK", 0)
            if live_actionable_markets is not None:
                research_queue_expected_markets = max(0, live_actionable_markets - field_unavailable)
                meta["research_queue_expected_markets"] = str(research_queue_expected_markets)
            rows.append(
                FreshnessRow(
                    "manual_execution_leverage.md coverage_state",
                    "FRESH" if leverage_states == coverage_states else "STALE_OR_INCONSISTENT",
                    str(dict(sorted(leverage_states.items()))),
                    str(dict(sorted(coverage_states.items()))),
                    "Execution leverage coverage-state counts must match the coverage audit it reads.",
                )
            )

        evidence_areas = _table_counts_with_headers(
            log_dir,
            "manual_evidence_coverage.md",
            "Coverage Summary",
            "evidence_area",
            "markets",
        )
        polymarket_direct_status = _table_counts(
            log_dir,
            "polymarket_direct_coverage_audit.md",
            "Coverage Summary",
            "status",
            "markets",
        )
        reason = "Manual evidence exact-Polymarket anchor count must match the direct coverage audit."
        if evidence_areas is None or polymarket_direct_status is None:
            rows.append(
                FreshnessRow(
                    "polymarket_direct_coverage_audit.md exact_anchors",
                    "MISSING",
                    "-" if polymarket_direct_status is None else str(sum(polymarket_direct_status.values())),
                    "manual_evidence_coverage.md exact_polymarket_anchors",
                    reason,
                )
            )
        else:
            observed = sum(polymarket_direct_status.values())
            expected_exact = evidence_areas.get("exact_polymarket_anchors", 0)
            meta["exact_polymarket_anchors"] = str(observed)
            rows.append(
                FreshnessRow(
                    "polymarket_direct_coverage_audit.md exact_anchors",
                    _status(observed, expected_exact, zero_ok=True),
                    str(observed),
                    str(expected_exact),
                    reason,
                )
            )
            risk_keys = _log_keys(log_dir, "polymarket_direct_anchor_risk_audit.md")
            reason = "Direct anchor risk audit must cover every current exact anchor from the coverage audit."
            if risk_keys is None:
                rows.append(
                    FreshnessRow(
                        "polymarket_direct_anchor_risk_audit.md current_anchor_rows",
                        "MISSING",
                        "-",
                        str(observed),
                        reason,
                    )
                )
            else:
                risk_observed = _int(risk_keys.get("current_anchor_rows"))
                meta["exact_polymarket_anchor_risk_rows"] = str(risk_observed)
                rows.append(
                    FreshnessRow(
                        "polymarket_direct_anchor_risk_audit.md current_anchor_rows",
                        _status(risk_observed, observed, zero_ok=True),
                        str(risk_observed),
                        str(observed),
                        reason,
                    )
                )

        calibration_watchlist = _log_keys(log_dir, "manual_calibration_watchlist.md")
        reason = "Calibration watchlist must be built from the current all-open board, not an older daily sheet."
        if calibration_watchlist is None:
            rows.append(
                FreshnessRow(
                    "manual_calibration_watchlist.md selected_markets",
                    "MISSING",
                    "-",
                    str(known_open_markets),
                    reason,
                )
            )
        else:
            source_context = calibration_watchlist.get("source_context", "")
            observed_selected = _int(calibration_watchlist.get("selected_markets"))
            status = (
                "FRESH"
                if source_context == "all-open" and observed_selected == known_open_markets
                else "STALE_OR_INCONSISTENT"
            )
            meta["calibration_watchlist_markets"] = str(observed_selected)
            rows.append(
                FreshnessRow(
                    "manual_calibration_watchlist.md selected_markets",
                    status,
                    f"{source_context or 'missing_context'} / {observed_selected}",
                    f"all-open / {known_open_markets}",
                    reason,
                )
            )

        execution_categories = _table_counts_with_headers(
            log_dir,
            "manual_execution_leverage.md",
            "Summary",
            "category",
            "markets",
        )
        if execution_categories is None:
            rows.append(
                FreshnessRow(
                    "manual_execution_leverage.md category",
                    "MISSING",
                    "-",
                    "category counts",
                    "Execution category counts are required to reconcile live evidence family reports.",
                )
            )
        else:
            odds_rows = _table_rows(log_dir, "odds_anchor_audit.md", "Open Match Odds Sources")
            reason = "Odds source audit goal-market count must match the execution odds-goal category."
            expected_odds_goal = execution_categories.get("odds_goal", 0)
            if odds_rows is None:
                rows.append(FreshnessRow("odds_anchor_audit.md goal_markets", "MISSING", "-", str(expected_odds_goal), reason))
            else:
                observed = sum(_int(row.get("goal_markets")) for row in odds_rows)
                meta["open_goal_markets"] = str(observed)
                rows.append(
                    FreshnessRow(
                        "odds_anchor_audit.md goal_markets",
                        _status(observed, expected_odds_goal),
                        str(observed),
                        str(expected_odds_goal),
                        reason,
                    )
                )

            coverage_market_rows = _table_rows(log_dir, "manual_evidence_coverage.md", "Markets")
            total_source = _log_keys(log_dir, "odds_total_source_audit.md")
            reason = "Total-goals source audit rows must match current manual coverage total/team-goal rows."
            if coverage_market_rows is None or total_source is None:
                rows.append(
                    FreshnessRow(
                        "odds_total_source_audit.md total_goal_rows",
                        "MISSING",
                        "-" if total_source is None else total_source.get("total_goal_rows", "-"),
                        "manual_evidence_coverage.md total/team-goal rows",
                        reason,
                    )
                )
            else:
                expected_total_goals = sum(
                    1 for row in coverage_market_rows if _is_total_goal_source_question(row.get("question", ""))
                )
                observed = _int(total_source.get("total_goal_rows"))
                meta["open_total_goal_rows"] = str(observed)
                rows.append(
                    FreshnessRow(
                        "odds_total_source_audit.md total_goal_rows",
                        _status(observed, expected_total_goals, zero_ok=True),
                        str(observed),
                        str(expected_total_goals),
                        reason,
                    )
                )

            player_rows = _table_rows(
                log_dir,
                "player_role_research_audit.md",
                "Current Open Player Role Summary",
            )
            reason = "Player-role research rows must match the execution player category."
            expected_players = execution_categories.get("player", 0)
            if player_rows is None:
                rows.append(FreshnessRow("player_role_research_audit.md player_rows", "MISSING", "-", str(expected_players), reason))
            else:
                observed = len(player_rows)
                meta["open_player_role_rows"] = str(observed)
                rows.append(
                    FreshnessRow(
                        "player_role_research_audit.md player_rows",
                        _status(observed, expected_players),
                        str(observed),
                        str(expected_players),
                        reason,
                    )
                )

            player_prop_rows = _table_rows(
                log_dir,
                "player_prop_diagnostics.md",
                "Current Open Player Props",
            )
            reason = "Player-prop diagnostics rows must match the execution player category."
            if player_prop_rows is None:
                rows.append(FreshnessRow("player_prop_diagnostics.md player_rows", "MISSING", "-", str(expected_players), reason))
            else:
                observed = len(player_prop_rows)
                meta["open_player_prop_rows"] = str(observed)
                rows.append(
                    FreshnessRow(
                        "player_prop_diagnostics.md player_rows",
                        _status(observed, expected_players),
                        str(observed),
                        str(expected_players),
                        reason,
                    )
                )

            current_player = _log_keys(log_dir, "current_player_stats_audit.md")
            reason = "Current-player-stat source audit rows must match the execution player category."
            if current_player is None:
                rows.append(
                    FreshnessRow(
                        "current_player_stats_audit.md player_rows",
                        "MISSING",
                        "-",
                        str(expected_players),
                        reason,
                    )
                )
            else:
                observed = _int(current_player.get("open_player_prop_rows"))
                source_status = current_player.get("current_player_stats_status", "unknown")
                source_counts = _table_counts(
                    log_dir,
                    "current_player_stats_audit.md",
                    "Source State Summary",
                    "source_state",
                    "rows",
                ) or {}
                matched = source_counts.get("CURRENT_PLAYER_STATS_SOURCE", 0)
                meta["current_player_stats_status"] = source_status
                meta["current_player_stats_rows"] = str(observed)
                meta["current_player_stats_matched_rows"] = str(matched)
                status = (
                    "FRESH"
                    if observed == expected_players
                    and source_status
                    in {
                        "COMPLETE",
                        "PARTIAL",
                        "NO_MATCHED_CURRENT_PLAYER_STATS",
                        "NO_OPEN_PLAYER_ROWS",
                        "NO_COMPLETED_PLAYER_STATS",
                    }
                    else "STALE_OR_INCONSISTENT"
                )
                rows.append(
                    FreshnessRow(
                        "current_player_stats_audit.md player_rows",
                        status,
                        f"rows={observed}, matched={matched}, status={source_status}",
                        str(expected_players),
                        reason,
                    )
                )

            current_player_replay = _log_keys(log_dir, "current_player_stats_replay.md")
            reason = "Current-player-stat replay must have an explicit leakage-safe promotion decision before current form can affect player-prop pricing."
            if current_player_replay is None:
                rows.append(
                    FreshnessRow(
                        "current_player_stats_replay.md",
                        "MISSING",
                        "-",
                        "complete replay with explicit promotion decision",
                        reason,
                    )
                )
            else:
                replay_status = current_player_replay.get("current_player_stats_replay_status", "")
                decision = current_player_replay.get("decision", "")
                settled = _int(current_player_replay.get("settled_player_rows"))
                matched = _int(current_player_replay.get("matched_replay_rows"))
                ci_low = current_player_replay.get("best_bootstrap_ci_2.5", "missing")
                pr_gt_0 = current_player_replay.get("best_bootstrap_pr_gt_0", "missing")
                meta["current_player_stats_replay_status"] = replay_status
                meta["current_player_stats_replay_decision"] = decision
                meta["current_player_stats_replay_matched_rows"] = str(matched)
                allowed_decisions = {
                    "NOT_PROMOTED_DIAGNOSTIC_ONLY",
                    "NOT_PROMOTED_TOO_SPARSE",
                    "PROMOTION_CANDIDATE_REVIEW",
                }
                status = (
                    "FRESH"
                    if replay_status in {"COMPLETE", "NO_MATCHED_REPLAY_ROWS"}
                    and decision in allowed_decisions
                    and settled >= expected_players
                    else "STALE_OR_INCONSISTENT"
                )
                rows.append(
                    FreshnessRow(
                        "current_player_stats_replay.md",
                        status,
                        (
                            f"status={replay_status or 'missing'}, decision={decision or 'missing'}, "
                            f"settled={settled}, matched={matched}, ci_2.5={ci_low}, pr_gt_0={pr_gt_0}"
                        ),
                        "explicit decision and settled rows >= current player category",
                        reason,
                    )
                )

            player_boundary = _log_keys(log_dir, "player_prop_boundary_audit.md")
            reason = "Player-prop boundary audit must cover the current open player-prop diagnostics rows."
            if player_boundary is None:
                rows.append(
                    FreshnessRow(
                        "player_prop_boundary_audit.md current_player_props",
                        "MISSING",
                        "-",
                        str(expected_players),
                        reason,
                    )
                )
            else:
                observed = _int(player_boundary.get("current_player_props"))
                high_rows = _int(player_boundary.get("high_priority_player_rows"))
                medium_rows = _int(player_boundary.get("medium_priority_player_rows"))
                meta["open_player_boundary_rows"] = str(observed)
                meta["open_player_boundary_actionable_rows"] = str(high_rows + medium_rows)
                rows.append(
                    FreshnessRow(
                        "player_prop_boundary_audit.md current_player_props",
                        _status(observed, expected_players),
                        str(observed),
                        str(expected_players),
                        reason,
                    )
                )

            special = _log_keys(log_dir, "special_market_live_audit.md")
            reason = "Special live audit row count must match the execution special-proxy category."
            expected_special = execution_categories.get("special_proxy", 0)
            if special is None:
                rows.append(FreshnessRow("special_market_live_audit.md special_rows", "MISSING", "-", str(expected_special), reason))
            else:
                observed = _int(special.get("special_rows"))
                meta["open_special_rows"] = str(observed)
                rows.append(
                    FreshnessRow(
                        "special_market_live_audit.md special_rows",
                        _status(observed, expected_special),
                        str(observed),
                        str(expected_special),
                        reason,
                    )
                )
            special_boundary = _log_keys(log_dir, "special_boundary_audit.md")
            reason = "Special boundary audit must cover the current live special audit rows."
            if special_boundary is None:
                rows.append(
                    FreshnessRow(
                        "special_boundary_audit.md current_special_rows",
                        "MISSING",
                        "-",
                        str(expected_special),
                        reason,
                    )
                )
            else:
                observed = _int(special_boundary.get("current_special_rows"))
                meta["open_special_boundary_rows"] = str(observed)
                rows.append(
                    FreshnessRow(
                        "special_boundary_audit.md current_special_rows",
                        _status(observed, expected_special),
                        str(observed),
                        str(expected_special),
                        reason,
                    )
                )

            special_count_threshold = _log_keys(log_dir, "special_count_threshold_audit.md")
            reason = "Special count-threshold audit must have positive proxy rows and an explicit promotion decision."
            if special_count_threshold is None:
                rows.append(
                    FreshnessRow(
                        "special_count_threshold_audit.md",
                        "MISSING",
                        "-",
                        "count-threshold proxy gate",
                        reason,
                    )
                )
            else:
                matches = _int(special_count_threshold.get("matches"))
                tournaments = _int(special_count_threshold.get("tournaments"))
                scored = _int(special_count_threshold.get("scored_rows"))
                decision = special_count_threshold.get("decision", "")
                meta["special_count_threshold_scored_rows"] = str(scored)
                status = (
                    "FRESH"
                    if matches > 0
                    and tournaments > 0
                    and scored > 0
                    and decision
                    in {
                        "proxy_candidate_distribution_and_adaptation_positive_not_live_promoted",
                        "proxy_candidate_count_distribution_positive_not_live_promoted",
                        "proxy_candidate_count_threshold_adaptation_positive_not_live_promoted",
                        "not_promoted_keep_count_threshold_guardrails",
                    }
                    else "STALE_OR_INCONSISTENT"
                )
                rows.append(
                    FreshnessRow(
                        "special_count_threshold_audit.md",
                        status,
                        f"matches={matches}, tournaments={tournaments}, scored={scored}, decision={decision or 'missing'}",
                        "positive rows and explicit decision",
                        reason,
                    )
                )

            special_count_source = _log_keys(log_dir, "special_count_source_audit.md")
            special_count_discovery = _log_keys(log_dir, "special_count_source_discovery.md")
            reason = (
                "Special count-source audit must reconcile its no-usable public exact count against the public-source "
                "discovery log before readiness can rely on it."
            )
            if special_count_source is None or special_count_discovery is None:
                observed = (
                    "source=missing"
                    if special_count_source is None
                    else "discovery=missing"
                )
                rows.append(
                    FreshnessRow(
                        "special_count_source_audit.md public_discovery_join",
                        "MISSING",
                        observed,
                        "source and discovery logs present",
                        reason,
                    )
                )
            else:
                required_rows = _int(special_count_source.get("required_rows"))
                source_no_usable = _int(special_count_source.get("no_usable_public_exact_rows"))
                source_near_miss = _int(special_count_source.get("near_miss_public_rows"))
                discovery_rows = _int(special_count_discovery.get("rows"))
                exact_usable = _int(special_count_discovery.get("exact_usable_rows"))
                exact_below = _int(special_count_discovery.get("exact_below_volume_rows"))
                discovery_near_miss = _int(special_count_discovery.get("near_miss_rows"))
                expected_no_usable = max(0, discovery_rows - exact_usable)
                expected_near_miss = exact_below + discovery_near_miss
                meta["special_count_source_required_rows"] = str(required_rows)
                meta["special_count_no_usable_public_exact_rows"] = str(source_no_usable)
                meta["special_count_public_near_miss_rows"] = str(source_near_miss)
                status = (
                    "FRESH"
                    if required_rows == discovery_rows
                    and source_no_usable == expected_no_usable
                    and source_near_miss == expected_near_miss
                    else "STALE_OR_INCONSISTENT"
                )
                rows.append(
                    FreshnessRow(
                        "special_count_source_audit.md public_discovery_join",
                        status,
                        (
                            f"required={required_rows}, source_no_usable={source_no_usable}, "
                            f"source_near_miss={source_near_miss}"
                        ),
                        (
                            f"discovery_rows={discovery_rows}, no_usable={expected_no_usable}, "
                            f"near_miss={expected_near_miss}"
                        ),
                        reason,
                    )
                )

        referee_availability = _table_rows(log_dir, "referee_research_audit.md", "Open Match Referee Availability")
        referee_cards = _table_rows(log_dir, "referee_research_audit.md", "Open Card Markets")
        expected_referee_cards: int | None = None
        reason = "Referee availability card-market counts must equal the detailed open-card market rows."
        if referee_availability is None or referee_cards is None:
            rows.append(
                FreshnessRow(
                    "referee_research_audit.md card_markets",
                    "MISSING",
                    "-" if referee_cards is None else str(len(referee_cards)),
                    "availability card_markets total",
                    reason,
                )
            )
        else:
            observed = len(referee_cards)
            expected_referee_cards = sum(_int(row.get("card_markets")) for row in referee_availability)
            meta["open_referee_card_rows"] = str(observed)
            rows.append(
                FreshnessRow(
                    "referee_research_audit.md card_markets",
                    _status(observed, expected_referee_cards, zero_ok=True),
                    str(observed),
                    str(expected_referee_cards),
                    reason,
                )
            )
        card_component = _log_keys(log_dir, "card_field_component_audit.md")
        reason = "Card-field component audit must match current referee card rows and execution card-count rows."
        expected_card_counts = 0
        if "execution_categories" in locals() and execution_categories is not None:
            expected_card_counts = execution_categories.get("count_cards", 0)
        if card_component is None:
            rows.append(
                FreshnessRow(
                    "card_field_component_audit.md open_cards",
                    "MISSING",
                    "-",
                    f"open_card_like={expected_referee_cards if expected_referee_cards is not None else 'unknown'}, count_cards={expected_card_counts}",
                    reason,
                )
            )
        else:
            observed_open_cards = _int(card_component.get("open_card_like_markets"))
            observed_field_cards = _int(card_component.get("field_required_card_count_rows"))
            expected_open_cards = expected_referee_cards if expected_referee_cards is not None else observed_open_cards
            status = (
                "FRESH"
                if observed_open_cards == expected_open_cards and observed_field_cards == expected_card_counts
                else "STALE_OR_INCONSISTENT"
            )
            meta["open_card_component_rows"] = str(observed_open_cards)
            meta["field_required_card_count_rows"] = str(observed_field_cards)
            rows.append(
                FreshnessRow(
                    "card_field_component_audit.md open_cards",
                    status,
                    f"open_card_like={observed_open_cards}, count_cards={observed_field_cards}",
                    f"open_card_like={expected_open_cards}, count_cards={expected_card_counts}",
                    reason,
                )
            )

    if research_queue_expected_markets is not None:
        observed = _table_total(log_dir, "manual_research_queue.md", "Queue Summary", "rows")
        reason = (
            "Research queue is intentionally filtered: it is high/medium only, "
            "excludes field-unavailable pre-lock rows, and excludes rows with "
            "completed negative research resolutions."
        )
        if observed is None:
            rows.append(
                FreshnessRow(
                    "manual_research_queue.md",
                    "MISSING",
                    "-",
                    f"<= {research_queue_expected_markets}",
                    reason,
                )
            )
        else:
            status = "FRESH" if 0 <= observed <= research_queue_expected_markets else "STALE_OR_INCONSISTENT"
            rows.append(
                FreshnessRow(
                    "manual_research_queue.md",
                    status,
                    str(observed),
                    f"<= {research_queue_expected_markets}",
                    reason,
                )
            )

    matrix = _log_keys(log_dir, "strategy_evidence_matrix.md")
    matrix_time: datetime | None = None
    if matrix is None:
        rows.append(FreshnessRow("strategy_evidence_matrix.md", "MISSING", "-", "present", "Run the consolidated strategy matrix."))
    else:
        matrix_time = _parse_utc(matrix.get("run_utc"))
        source_times: list[tuple[str, datetime]] = []
        missing_timestamp_sources: list[str] = []
        for filename in MATRIX_TIMESTAMP_SOURCES:
            keys = _log_keys(log_dir, filename)
            if keys is None:
                continue
            source_time = _parse_utc(keys.get("run_utc"))
            if source_time is None:
                missing_timestamp_sources.append(filename)
                continue
            source_times.append((filename, source_time))
        if matrix_time is None:
            matrix_status = "STALE_OR_INCONSISTENT"
            observed = matrix.get("run_utc", "missing")
            expected_matrix = "parseable run_utc"
            reason = "Strategy matrix must carry a parseable UTC timestamp."
        elif source_times:
            newest_name, newest_time = max(source_times, key=lambda item: item[1])
            matrix_status = "FRESH" if matrix_time >= newest_time else "STALE_OR_INCONSISTENT"
            observed = matrix_time.isoformat(timespec="seconds")
            expected_matrix = f">= {newest_name} {newest_time.isoformat(timespec='seconds')}"
            reason = "Strategy matrix timestamp must be at least as new as timestamped source reports it summarizes."
        else:
            matrix_status = "CHECKED"
            observed = matrix.get("run_utc", "present")
            expected_matrix = "timestamped source reports present when available"
            reason = "No timestamped matrix source reports were available; row-count checks still cover core reports."
        rows.append(
            FreshnessRow(
                "strategy_evidence_matrix.md",
                matrix_status,
                observed,
                expected_matrix,
                reason,
            )
        )
        if missing_timestamp_sources:
            rows.append(
                FreshnessRow(
                    "strategy_evidence_matrix.md source_timestamps",
                    "CHECKED",
                    ", ".join(missing_timestamp_sources),
                    "optional run_utc",
                    "These matrix source reports lack run_utc; their numeric freshness is checked elsewhere when possible.",
                )
            )

    promotion = _log_keys(log_dir, "promotion_strength_audit.md")
    reason = "Promotion strength audit must be generated after the strategy matrix it interprets."
    if promotion is None:
        rows.append(
            FreshnessRow(
                "promotion_strength_audit.md",
                "MISSING",
                "-",
                "present and newer than strategy_evidence_matrix.md",
                reason,
            )
        )
    else:
        promotion_time = _parse_utc(promotion.get("run_utc"))
        audited = _int(promotion.get("audited_areas"))
        warnings = _int(promotion.get("warning_rows"))
        meta["promotion_strength_audited_areas"] = str(audited)
        meta["promotion_strength_warning_rows"] = str(warnings)
        if promotion_time is None:
            status = "STALE_OR_INCONSISTENT"
            observed_promotion = promotion.get("run_utc", "missing")
            expected_promotion = "parseable run_utc"
        elif matrix_time is not None and promotion_time < matrix_time:
            status = "STALE_OR_INCONSISTENT"
            observed_promotion = promotion_time.isoformat(timespec="seconds")
            expected_promotion = f">= strategy_evidence_matrix.md {matrix_time.isoformat(timespec='seconds')}"
        else:
            status = "FRESH" if audited > 0 else "STALE_OR_INCONSISTENT"
            observed_promotion = f"audited={audited}, warnings={warnings}"
            expected_promotion = "positive audited_areas"
        rows.append(
            FreshnessRow(
                "promotion_strength_audit.md",
                status,
                observed_promotion,
                expected_promotion,
                reason,
            )
        )

    return rows, meta


def render(rows: list[FreshnessRow], meta: dict[str, str], *, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    counts = Counter(row.status for row in rows)
    lines = [
        "# Evidence Freshness Audit",
        "",
        "Read-only analysis. `/predictions` was not read and no submissions were made.",
        "",
        f"- run_utc: {now.astimezone(timezone.utc).isoformat(timespec='seconds')}",
        "- sports_predict: not_called",
        "- purpose: verify generated strategy evidence logs are internally consistent before trusting promotion decisions",
    ]
    if meta:
        lines += [
            f"- source_results_rows: {meta.get('source_results_rows', '?')}",
            f"- safe_exact_rows: {meta.get('safe_exact_rows', '?')}",
            f"- unsafe_question_overwrite_exact_rows: {meta.get('unsafe_question_overwrite_exact_rows', '?')}",
            f"- blocked_by_safe_join: {meta.get('blocked_by_safe_join', '?')}",
        ]
        if "known_open_markets" in meta:
            lines.append(f"- known_open_markets: {meta['known_open_markets']}")
        if "live_actionable_markets" in meta:
            lines.append(f"- live_actionable_markets: {meta['live_actionable_markets']}")
        if "research_queue_expected_markets" in meta:
            lines.append(f"- research_queue_expected_markets: {meta['research_queue_expected_markets']}")
        for key in [
            "calibration_watchlist_markets",
            "exact_polymarket_anchors",
            "open_goal_markets",
            "open_player_role_rows",
            "open_player_prop_rows",
            "current_player_stats_status",
            "current_player_stats_rows",
            "current_player_stats_matched_rows",
            "current_player_stats_replay_status",
            "current_player_stats_replay_decision",
            "current_player_stats_replay_matched_rows",
            "open_player_boundary_rows",
            "open_player_boundary_actionable_rows",
            "count_total_dependence_scored_markets",
            "count_total_dependence_cup_rows",
            "player_minutes_appearances",
            "player_minutes_matches",
            "player_sot_rate_market_like_rows",
            "player_sot_rate_chronological_rows",
            "player_goal_rate_market_like_rows",
            "player_goal_rate_chronological_rows",
            "open_special_rows",
            "open_special_boundary_rows",
            "special_count_threshold_scored_rows",
            "special_count_source_required_rows",
            "special_count_no_usable_public_exact_rows",
            "special_count_public_near_miss_rows",
            "open_referee_card_rows",
            "referee_card_source_matches",
            "referee_card_unique_referees",
            "open_card_component_rows",
            "field_required_card_count_rows",
            "observed_question_family_keys",
            "parser_question_family_names",
            "non_promoted_question_family_keys",
            "promotion_strength_audited_areas",
            "promotion_strength_warning_rows",
        ]:
            if key in meta:
                lines.append(f"- {key}: {meta[key]}")
    lines += [
        "",
        "## Status Summary",
        "",
        "| status | rows |",
        "| --- | ---: |",
    ]
    for status, n in sorted(counts.items()):
        lines.append(f"| {status} | {n} |")
    lines += [
        "",
        "## Artifact Checks",
        "",
        "| artifact | status | observed | expected | reason |",
        "| --- | --- | ---: | ---: | --- |",
    ]
    for row in rows:
        lines.append(f"| {row.artifact} | {row.status} | {row.observed} | {row.expected} | {row.reason} |")
    lines += [
        "",
        "## Interpretation",
        "",
        "- `FRESH`: the report's sample/result count matches the current integrity reference.",
        "- `STALE_OR_INCONSISTENT`: rerun that report before using the strategy matrix.",
        "- `MISSING`: the evidence gate cannot be evaluated.",
        "- `CHECKED`: supplemental check without a strict pass/fail condition.",
        "- `blocked_by_safe_join` is expected when `/results` has rows that cannot be safely joined to V1 history by question text alone.",
        "- Open-market freshness checks compare target, tail-risk, risk, coverage, leverage, research-queue, and live family reports so stale board-size or category drift is visible.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write", action="store_true", help=f"write {OUT}")
    args = ap.parse_args()

    rows, meta = build_rows(LOG_DIR)
    text = render(rows, meta=meta)
    print(text)
    if args.write:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(text, encoding="utf-8")
        print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
