"""Build a consolidated evidence matrix for the manual strategy.

This is a read-only audit over existing backtest logs. It does not call
SportsPredict, does not read `/predictions`, and does not submit anything.

The purpose is to make strategy promotion explicit: a branch is not treated as
optimized merely because it looks plausible in one report. The matrix records
the current operational rule, evidence strength, and the action allowed by that
evidence.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "data" / "logs"
OUT = LOG_DIR / "strategy_evidence_matrix.md"


@dataclass(frozen=True)
class EvidenceRow:
    area: str
    rule: str
    status: str
    sample: str
    primary_delta: str
    holdout_delta: str
    uncertainty: str
    source: str
    action: str


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _clean(value: str) -> str:
    return value.strip().strip("`")


def _float(value: str | None, default: float = 0.0) -> float:
    if value is None:
        return default
    value = value.strip().replace("%", "").replace(",", "")
    if value in {"", "-"}:
        return default
    return float(value)


def _key_values(text: str) -> dict[str, str]:
    out = {}
    for line in text.splitlines():
        m = re.match(r"^- ([A-Za-z0-9_ ./%>=#-]+):\s*(.*)$", line)
        if m:
            out[m.group(1).strip()] = m.group(2).strip()
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
    rows = []
    for line in lines[start + 2 :]:
        if not line.lstrip().startswith("|"):
            break
        parts = [_clean(part) for part in line.strip().strip("|").split("|")]
        if len(parts) != len(headers):
            continue
        rows.append(dict(zip(headers, parts)))
    return rows


def _row(rows: list[dict[str, str]], key: str, value: str) -> dict[str, str]:
    for row in rows:
        if row.get(key) == value:
            return row
    return {}


def _fmt(value: str | float, suffix: str = "") -> str:
    if isinstance(value, str):
        return value
    return f"{value:.2f}{suffix}"


def _combined_policy(log_dir: Path) -> EvidenceRow:
    text = _read(log_dir / "manual_policy_backtest.md")
    combined = _row(_table_after(text, "Combined Policy"), "policy", "combined_manual_policy")
    holdout = _row(_table_after(text, "Chronological Holdout"), "policy", "combined_manual_policy")
    keys = _key_values(text)
    ci = _float(keys.get("ci_2.5"))
    status = "PROMOTED" if _float(combined.get("delta_vs_current")) > 0 and _float(holdout.get("delta_vs_current")) > 0 and ci > 0 else "GUARDRAIL_ONLY"
    return EvidenceRow(
        "combined_manual_policy",
        "Use the family-specific manual policy when its required live inputs are supplied.",
        status,
        f"{combined.get('n', '?')} rows / {keys.get('matches', '?')} matches",
        f"{combined.get('delta_vs_current', '?')} RBP",
        f"{holdout.get('delta_vs_current', '?')} RBP",
        f"match-bootstrap ci_2.5={keys.get('ci_2.5', '?')}",
        "manual_policy_backtest.md",
        "Allowed when the relevant live input exists; field values are unavailable before lock.",
    )


def _goal_policy(log_dir: Path) -> EvidenceRow:
    text = _read(log_dir / "goal_policy_backtest.md")
    current = _row(_table_after(text, "Fixed Policies"), "policy", "current")
    comparison = _row(_table_after(text, "Direct Goal Policy Comparisons"), "comparison", "raw_current_minus_field")
    keys = _key_values(text)
    pr = _float(comparison.get("bootstrap_pr_gt_0"))
    status = (
        "PROMOTED"
        if _float(comparison.get("full_sample_delta")) > 0
        and _float(comparison.get("chronological_holdout_delta")) > 0
        and pr >= 0.95
        else "GUARDRAIL_ONLY"
    )
    robustness = (
        f"raw>field configs={keys.get('raw_beats_field_configs', '?')}/{keys.get('robustness_configs', '?')}, "
        f"raw>shrink75 configs={keys.get('raw_beats_shrink75_configs', '?')}/{keys.get('robustness_configs', '?')}"
        if keys.get("robustness_configs")
        else "sensitivity grid missing"
    )
    return EvidenceRow(
        "odds_goal_rows",
        "Keep raw odds-anchored model values; do not shrink goal/outcome rows to field.",
        status,
        f"{current.get('n', '?')} goal rows",
        f"raw-current minus field {comparison.get('full_sample_delta', '?')} RBP",
        f"{comparison.get('chronological_holdout_delta', '?')} RBP",
        f"Pr(>0)={comparison.get('bootstrap_pr_gt_0', '?')}, ci_2.5={comparison.get('bootstrap_ci_2.5', '?')}; {robustness}",
        "goal_policy_backtest.md",
        "Use raw odds unless the odds-source audit flags stale/misoriented feeds.",
    )


def _count_policy(log_dir: Path) -> EvidenceRow:
    text = _read(log_dir / "count_policy_backtest.md")
    fixed = _row(_table_after(text, "Fixed Policies"), "policy", "strong_sot_current_cards_field_else_global")
    holdout = _row(_table_after(text, "Chronological Holdout"), "policy", "strong_sot_current_cards_field_else_global")
    boot = _row(_table_after(text, "Match-Level Bootstrap"), "policy", "strong_sot_current_cards_field_else_global")
    status = (
        "PROMOTED"
        if _float(fixed.get("delta_vs_current")) > 0
        and _float(holdout.get("delta_vs_current")) > 0
        and _float(boot.get("ci_2.5")) > 0
        else "GUARDRAIL_ONLY"
    )
    return EvidenceRow(
        "count_field_policy",
        "Historical count-field policy is promoted, but pre-lock entries use no-field/current-source count values.",
        status,
        f"{fixed.get('n', '?')} count rows",
        f"{fixed.get('delta_vs_current', '?')} RBP",
        f"{holdout.get('delta_vs_current', '?')} RBP",
        f"ci_2.5={boot.get('ci_2.5', '?')}",
        "count_policy_backtest.md",
        "Do not apply the historical field branch pre-lock; use no-field/current-source count values and do not mine narrower count families.",
    )


def _card_field_component(log_dir: Path) -> EvidenceRow:
    text = _read(log_dir / "card_field_component_audit.md")
    keys = _key_values(text)
    field_vs_raw = _row(_table_after(text, "Component Comparisons"), "comparison", "card_rows_field_minus_raw_model")
    component = _row(
        _table_after(text, "Component Comparisons"),
        "comparison",
        "promoted_count_policy_minus_no_card_field_component",
    )
    return EvidenceRow(
        "card_field_component",
        "Without pre-lock field values or verified referee context, keep the neutral/raw card model.",
        "GUARDRAIL_ONLY",
        f"{keys.get('settled_card_rows', '?')} settled card rows / {keys.get('open_card_like_markets', '?')} open card-like markets",
        f"field-minus-raw={field_vs_raw.get('full_sample_delta', '?')} RBP",
        f"field-minus-raw holdout={field_vs_raw.get('chronological_holdout_delta', '?')} RBP",
        (
            f"field ci_2.5={field_vs_raw.get('bootstrap_ci_2.5', '?')}; "
            f"component ci_2.5={component.get('bootstrap_ci_2.5', '?')}; "
            f"no-field ci_2.5={keys.get('no_field_all_ci_2.5', '?')}"
        ),
        "card_field_component_audit.md",
        "Treat card field evidence as historical/diagnostic; no-field card rows remain lower-confidence unless current referee/source evidence clears them.",
    )


def _count_no_field(log_dir: Path) -> EvidenceRow:
    text = _read(log_dir / "count_no_field_policy_backtest.md")
    best = _row(_table_after(text, "Chronological No-Field Policies"), "policy", "card_threshold_stat_base_else_raw")
    boot = _row(
        _table_after(text, "Match-Level Bootstrap"),
        "comparison",
        "card_threshold_stat_base_else_raw_minus_raw_model",
    )
    keys = _key_values(text)
    robustness = (
        f"robust configs={keys.get('robustness_positive_configs', '?')}/{keys.get('robustness_configs', '?')}, "
        f"min_delta={keys.get('robustness_min_delta_vs_raw', '?')}"
        if keys.get("robustness_configs")
        else "sensitivity grid missing"
    )
    status = "NOT_PROMOTED" if _float(boot.get("ci_2.5")) <= 0 else "PROMOTED"
    return EvidenceRow(
        "count_no_field_fallback",
        "No raw-count replacement is promoted when field averages are absent.",
        status,
        f"{best.get('n', '?')} holdout count rows",
        f"{best.get('delta_vs_raw', '?')} RBP vs raw",
        "walk-forward only",
        f"ci_2.5={boot.get('ci_2.5', '?')}; {robustness}",
        "count_no_field_policy_backtest.md",
        "Use raw count probabilities without field, except exact current corner anchors.",
    )


def _count_intensity_tuning(log_dir: Path) -> EvidenceRow:
    text = _read(log_dir / "count_intensity_policy_replay.md")
    overall = _table_after(text, "Overall Manual-Policy Replay")
    best = overall[0] if overall else {}
    current = _row(overall, "candidate", "current_v4")
    boot = _row(_table_after(text, "Bootstrap Difference Vs Current V4 Policy"), "candidate", best.get("candidate", ""))
    keys = _key_values(text)
    status = (
        "PROMOTED"
        if best.get("candidate") != "current_v4"
        and _float(best.get("delta_vs_current_v4_policy")) > 0
        and _float(boot.get("ci_2.5")) > 0
        else "NOT_PROMOTED"
    )
    return EvidenceRow(
        "count_intensity_tuning",
        "Keep the current count-intensity map; do not promote SOT/corner/card multiplier tuning from point estimates alone.",
        status,
        f"{best.get('n', keys.get('settled_count_rows_requested', '?'))} count rows",
        (
            f"best challenger={best.get('candidate', '?')} "
            f"delta_vs_current_v4={best.get('delta_vs_current_v4_policy', '?')} RBP"
        ),
        f"current_v4 manual-policy delta_vs_submitted={current.get('delta_vs_submitted', '?')} RBP",
        f"best challenger ci_2.5={boot.get('ci_2.5', '?')}, ci_97.5={boot.get('ci_97.5', '?')}",
        "count_intensity_policy_replay.md + count_intensity_replay.md",
        "Use as directional monitoring only; require positive match-bootstrap and reconstructed current context before changing live count multipliers.",
    )


def _count_total_dependence(log_dir: Path) -> EvidenceRow:
    text = _read(log_dir / "count_total_dependence_audit.md")
    policy_text = _read(log_dir / "count_total_dependence_policy_replay.md")
    keys = _key_values(text)
    policy_keys = _key_values(policy_text)
    decision = keys.get("decision", "not_run")
    policy_decision = policy_keys.get("decision", "not_run")
    direct_nb = _row(
        _table_after(text, "Match-Block Bootstraps Vs Current"),
        "comparison",
        "direct_total_nb_minus_current",
    )
    recent = _row(_table_after(text, "Recent Holdout Since 2023"), "method", "direct_total_nb")
    policy_manual = _row(
        _table_after(policy_text, "Manual-Policy Replay Bootstrap Vs Current"),
        "candidate",
        "direct_total_nb_all_periods",
    )
    policy_raw = _row(
        _table_after(policy_text, "Raw Model Replay Bootstrap Vs Current"),
        "candidate",
        "direct_total_nb_all_periods",
    )
    status = (
        "PROMOTED_LIMITED"
        if policy_decision.startswith("promote_")
        else ("PROXY_ONLY" if decision.startswith("proxy_candidate") else "NOT_PROMOTED")
    )
    return EvidenceRow(
        "count_total_dependence",
        "Direct-total NB is a proxy candidate for total-count thresholds; do not replace the live independent convolution yet.",
        status,
        (
            f"{keys.get('scored_market_rows', '?')} club-history rows; "
            f"{policy_keys.get('settled_total_count_rows_requested', '?')} settled Cup total-count rows"
        ),
        f"direct_total_nb delta={direct_nb.get('point_delta_x100', '?')} x100",
        (
            f"2023+ proxy={recent.get('delta_vs_current_x100', '?')} x100; "
            f"Cup manual={policy_manual.get('point_delta', '?')} RBP"
        ),
        (
            f"ci_2.5={direct_nb.get('ci_2.5', '?')}, "
            f"Pr>0={direct_nb.get('pr_gt_0', '?')}; "
            f"Cup raw ci_2.5={policy_raw.get('ci_2.5', '?')}, "
            f"Cup manual ci_2.5={policy_manual.get('ci_2.5', '?')}; "
            f"decision={policy_decision}"
        ),
        "count_total_dependence_audit.md + count_total_dependence_policy_replay.md",
        "Do not change live total-count probabilities; current Cup replay failed the promotion gate despite positive club-history proxy evidence.",
    )


def _global_calibration(log_dir: Path) -> EvidenceRow:
    text = _read(log_dir / "manual_calibration_backtest.md")
    keys = _key_values(text)
    fixed = _table_after(text, "Full-Sample Fixed Factors")
    best_fixed = fixed[0] if fixed else {}
    point = _float(keys.get("point_delta"))
    ci_low = _float(keys.get("ci_2.5"))
    status = "REJECTED" if point <= 0 or ci_low <= 0 else "PROMOTED"
    return EvidenceRow(
        "global_post_calibration",
        "Do not add a global distance-from-50 calibration multiplier to the combined manual policy.",
        status,
        f"{keys.get('settled_rows', '?')} rows / {keys.get('matches', '?')} matches",
        f"best full-sample factor={best_fixed.get('factor', '?')} delta={best_fixed.get('delta_vs_current', '?')} RBP",
        f"walk-forward paired delta={keys.get('point_delta', '?')} RBP",
        f"ci_2.5={keys.get('ci_2.5', '?')}, Pr(>0)={keys.get('pr_gt_0', '?')}",
        "manual_calibration_backtest.md",
        "Keep the family-specific policy; monitor reliability bins as more rows settle.",
    )


def _family_linear_pool(log_dir: Path) -> EvidenceRow:
    text = _read(log_dir / "manual_family_blend_backtest.md")
    keys = _key_values(text)
    fixed = _row(_table_after(text, "Full-Sample Global Linear Pools"), "policy", "global_linear_pool_w0.50")
    walk = _row(_table_after(text, "Walk-Forward Result"), "policy", "walkforward_category_linear_pool")
    combined = _row(_table_after(text, "Walk-Forward Result"), "policy", "combined_manual_policy")
    point = _float(keys.get("point_delta"))
    ci_low = _float(keys.get("ci_2.5"))
    status = "PROMOTED" if point > 0 and ci_low > 0 else "REJECTED"
    return EvidenceRow(
        "category_linear_pool",
        "Do not replace the family-specific manual policy with adaptive category-wise model/field linear pooling.",
        status,
        f"{keys.get('settled_rows', '?')} rows / {keys.get('matches', '?')} matches",
        f"best simple global 50/50 delta={fixed.get('delta_vs_current', '?')} RBP",
        (
            f"walk-forward pool {walk.get('delta_vs_current', '?')} RBP "
            f"vs combined {combined.get('delta_vs_current', '?')} RBP"
        ),
        f"paired delta={keys.get('point_delta', '?')}, ci_2.5={keys.get('ci_2.5', '?')}, Pr(>0)={keys.get('pr_gt_0', '?')}",
        "manual_family_blend_backtest.md",
        "Use the report as evidence against adaptive category pooling; keep only specific promoted family rules.",
    )


def _calibration_watchlist(log_dir: Path) -> EvidenceRow:
    text = _read(log_dir / "manual_calibration_watchlist.md")
    summary = {row.get("severity", ""): row.get("rows", "0") for row in _table_after(text, "Current Watchlist Summary")}
    flagged = _table_after(text, "Flagged Historical Buckets")
    high_buckets = [
        f"{row.get('bucket', '?')} ({row.get('direction', '?')}, abs_error={row.get('abs_error', '?')})"
        for row in flagged
        if row.get("severity") == "HIGH"
    ]
    medium_buckets = [
        f"{row.get('bucket', '?')} ({row.get('direction', '?')}, abs_error={row.get('abs_error', '?')})"
        for row in flagged
        if row.get("severity") == "MEDIUM"
    ]
    return EvidenceRow(
        "calibration_watchlist",
        "Flag live entries in historically weak probability buckets; do not auto-transform probabilities.",
        "GUARDRAIL_ONLY",
        f"current watchlist: {summary.get('HIGH', '0')} HIGH, {summary.get('MEDIUM', '0')} MEDIUM",
        "no probability delta by design",
        "derived from rejected calibration layer",
        f"HIGH buckets: {', '.join(high_buckets) or 'none'}; MEDIUM buckets: {', '.join(medium_buckets) or 'none'}",
        "manual_calibration_watchlist.md + manual_calibration_backtest.md",
        "Use as source, lineup, prop, and wording recheck list only; never as a blanket multiplier.",
    )


def _brier_decomposition_reliability(log_dir: Path) -> EvidenceRow:
    text = _read(log_dir / "brier_decomposition_audit.md")
    keys = _key_values(text)
    combined = _row(_table_after(text, "Policy Decomposition"), "policy", "combined_manual_policy")
    worst_category = _table_after(text, "Combined Policy By Category")
    category_note = "no category rows"
    if worst_category:
        top = worst_category[0]
        category_note = (
            f"worst category={top.get('category', '?')} "
            f"rel={top.get('reliability', '?')} ece={top.get('ece', '?')}"
        )
    return EvidenceRow(
        "brier_decomposition_reliability",
        "Use Murphy-style Brier decomposition to diagnose reliability and resolution; do not transform probabilities.",
        "GUARDRAIL_ONLY",
        f"{keys.get('settled_rows', '?')} rows / {keys.get('matches', '?')} matches",
        f"combined reliability={combined.get('reliability', keys.get('combined_reliability', '?'))}",
        f"combined resolution={combined.get('resolution', keys.get('combined_resolution', '?'))}",
        (
            f"uncertainty={combined.get('uncertainty', keys.get('combined_uncertainty', '?'))}; "
            f"ece={combined.get('ece', keys.get('combined_ece', '?'))}; "
            f"worst_bucket={keys.get('worst_bucket', '?')} abs_error={keys.get('worst_bucket_abs_error', '?')}; "
            f"{category_note}"
        ),
        "brier_decomposition_audit.md + manual_calibration_backtest.md",
        "Use reliability/resolution errors as source, lineup, prop, referee, and wording recheck priorities only.",
    )


def _player_policy(log_dir: Path) -> EvidenceRow:
    text = _read(log_dir / "player_policy_backtest.md")
    fixed = _row(_table_after(text, "Fixed Policies"), "policy", "asym075_cap10")
    holdout = _row(_table_after(text, "Chronological Holdout"), "policy", "asym075_cap10")
    boot = _row(_table_after(text, "Match-Level Bootstrap"), "policy", "asym075_cap10")
    direct = _row(_table_after(text, "Direct Player Policy Comparison"), "comparison", "asym075_cap10_minus_field")
    status = (
        "PROMOTED_LIMITED"
        if _float(fixed.get("delta_vs_current")) > 0
        and _float(holdout.get("delta_vs_current")) > 0
        and _float(boot.get("ci_2.5")) > 0
        else "GUARDRAIL_ONLY"
    )
    return EvidenceRow(
        "researched_player_props",
        "Use exact prop/lineup-led player values or the validated no-field fallback; historical field cap is not actionable pre-lock.",
        status,
        f"{fixed.get('n', '?')} player rows",
        f"{fixed.get('delta_vs_current', '?')} RBP",
        f"{holdout.get('delta_vs_current', '?')} RBP",
        f"ci_2.5={boot.get('ci_2.5', '?')}; vs field holdout={direct.get('chronological_holdout_delta', '?')}",
        "player_policy_backtest.md",
        "Unresolved player rows remain exact-prop/lineup led; recheck this as new player rows settle.",
    )


def _player_no_field(log_dir: Path) -> EvidenceRow:
    text = _read(log_dir / "player_no_field_policy_backtest.md")
    best = _row(_table_after(text, "Chronological No-Field Policies"), "policy", "raw_scores_else_global_base")
    boot = _row(
        _table_after(text, "Match-Level Bootstrap"),
        "comparison",
        "raw_scores_else_global_base_minus_raw_model",
    )
    keys = _key_values(text)
    status = "PROMOTED_LIMITED" if _float(boot.get("ci_2.5")) > 0 else "NOT_PROMOTED"
    robustness = (
        f"robust configs={keys.get('robustness_positive_configs', '?')}/{keys.get('robustness_configs', '?')}, "
        f"min_delta={keys.get('robustness_min_delta_vs_raw', '?')}"
        if keys.get("robustness_configs")
        else "sensitivity grid missing"
    )
    return EvidenceRow(
        "player_no_field_fallback",
        "Use raw scorer probabilities and global-base fallback only inside tested player support.",
        status,
        f"{best.get('n', '?')} holdout player rows",
        f"{best.get('delta_vs_raw', '?')} RBP vs raw",
        "walk-forward only",
        f"ci_2.5={boot.get('ci_2.5', '?')}; {robustness}",
        "player_no_field_policy_backtest.md",
        "Do not apply to prior-only, team-unknown, confirmed-bench, unmatched-lineup, or 2+/3+ SOT rows.",
    )


def _current_player_stats_replay(log_dir: Path) -> EvidenceRow:
    text = _read(log_dir / "current_player_stats_replay.md")
    keys = _key_values(text)
    decision = keys.get("decision", "not_run")
    status = "PROXY_ONLY" if decision != "PROMOTION_CANDIDATE_REVIEW" else "PROMOTION_CANDIDATE_REVIEW"
    return EvidenceRow(
        "current_player_stats_replay",
        "Use ESPN current-tournament player stats as research context only; do not replace prop odds or confirmed lineups.",
        status,
        f"{keys.get('matched_replay_rows', '?')} matched replay rows / {keys.get('settled_player_rows', '?')} settled player rows",
        f"best={keys.get('best_policy', '?')} delta={keys.get('best_delta_vs_raw', '?')} RBP",
        "leakage-safe earlier-event replay",
        (
            f"ci_2.5={keys.get('best_bootstrap_ci_2.5', '?')}, "
            f"Pr>0={keys.get('best_bootstrap_pr_gt_0', '?')}; decision={decision}"
        ),
        "current_player_stats_replay.md + current_player_stats_audit.md",
        "Do not promote until match-bootstrap lower bound is positive; use large disagreement as a player research flag.",
    )


def _player_prop_boundary(log_dir: Path) -> EvidenceRow:
    text = _read(log_dir / "player_prop_boundary_audit.md")
    keys = _key_values(text)
    field_above = _row(_table_after(text, "Historical Field Comparisons"), "comparison", "field_minus_raw_when_model_above_field")
    asym_vs_field = _row(_table_after(text, "Historical Field Comparisons"), "comparison", "asym075_minus_field_all_player_props")
    return EvidenceRow(
        "player_prop_boundary",
        "Keep unresolved player props exact-prop/lineup led; no-field global base is limited to tested support.",
        "GUARDRAIL_ONLY",
        f"{keys.get('settled_player_rows', '?')} settled rows / {keys.get('current_player_props', '?')} open player props",
        f"no-field={keys.get('no_field_point_delta', '?')} RBP; above-field field-minus-raw={field_above.get('full_sample_delta', '?')} RBP",
        f"above-field holdout={field_above.get('chronological_holdout_delta', '?')} RBP",
        (
            f"no-field ci_2.5={keys.get('no_field_ci_2.5', '?')}; "
            f"asym-vs-field ci_2.5={asym_vs_field.get('bootstrap_ci_2.5', '?')}; "
            f"high-threshold SOT holdout={keys.get('higher_threshold_sot_holdout_rows', '?')}"
        ),
        "player_prop_boundary_audit.md",
        "Use exact prop odds or structured player research for unresolved/source-poor/high-threshold rows; apply no-field base only inside tested support.",
    )


def _player_role_policy(log_dir: Path) -> EvidenceRow:
    text = _read(log_dir / "player_role_policy_backtest.md")
    keys = _key_values(text)
    comparison = _row(
        _table_after(text, "Direct Policy Comparisons"),
        "comparison",
        "benchrisk_field_else_asym_minus_player_asym075_cap10",
    )
    policy = _row(_table_after(text, "Overall Policy Replay"), "policy", "benchrisk_field_else_asym")
    status = "PROMOTED_LIMITED" if _float(comparison.get("bootstrap_ci_2.5")) > 0 else "GUARDRAIL_ONLY"
    return EvidenceRow(
        "player_recent_role",
        "Use recent bench/rotation history as a player-prop research warning, not as an automatic probability transform.",
        status,
        f"{keys.get('classified_role_rows', keys.get('settled_player_rows_requested', '?'))} classified player rows",
        f"bench-risk policy delta={policy.get('delta_vs_submitted', '?')} RBP",
        f"vs existing player policy holdout={comparison.get('chronological_holdout_delta', '?')} RBP",
        (
            f"direct ci_2.5={comparison.get('bootstrap_ci_2.5', '?')}, "
            f"Pr(>0)={comparison.get('bootstrap_pr_gt_0', '?')}"
        ),
        "player_role_policy_backtest.md + player_role_research_audit.md",
        "Final lineup or exact current prop odds must resolve role risk before lock.",
    )


def _player_minutes_uncertainty(log_dir: Path) -> EvidenceRow:
    text = _read(log_dir / "player_minutes_uncertainty_audit.md")
    keys = _key_values(text)
    decision = keys.get("decision", "not_run")
    status = "PROXY_ONLY" if decision.startswith("proxy_candidate") else "NOT_PROMOTED"
    no_lineup_bits = []
    confirmed_bits = []
    for family in ("sot1", "scores", "score_or_assist"):
        no_lineup = _row(
            _table_after(text, f"{family} Candidate Bootstraps"),
            "comparison",
            "player_recent_distribution_minus_point90_no_lineup",
        )
        confirmed = _row(
            _table_after(text, f"{family} Candidate Bootstraps"),
            "comparison",
            "role_empirical_distribution_minus_role_point_live",
        )
        if no_lineup:
            no_lineup_bits.append(
                f"{family} no-lineup delta={no_lineup.get('point_delta_x100', '?')} ci={no_lineup.get('ci_2.5', '?')}"
            )
        if confirmed:
            confirmed_bits.append(
                f"{family} role delta={confirmed.get('point_delta_x100', '?')} ci={confirmed.get('ci_2.5', '?')}"
            )
    return EvidenceRow(
        "player_minutes_uncertainty",
        "Empirical minutes distributions are a player-prop research candidate, not a live override.",
        status,
        f"{keys.get('appearances', '?')} appearances / {keys.get('matches', '?')} StatsBomb matches",
        "; ".join(no_lineup_bits) or "?",
        "; ".join(confirmed_bits) or "?",
        f"decision={decision}; proxy event replay, not official SportsPredict settlement",
        "player_minutes_uncertainty_audit.md",
        "Keep current live point-minutes policy until official replay/current-prop integration clears; use lineups and exact prop odds first.",
    )


def _player_sot_rate(log_dir: Path) -> EvidenceRow:
    text = _read(log_dir / "player_sot_rate_audit.md")
    keys = _key_values(text)
    decision = keys.get("decision", "not_run")
    boot_rows = _table_after(text, "Chronological Candidate Bootstraps")
    app8_thr1 = next(
        (
            row
            for row in boot_rows
            if row.get("threshold") == "1"
            and row.get("comparison") == "app_shrink_k8_minus_app_shrink_k1"
        ),
        {},
    )
    app8_thr2 = next(
        (
            row
            for row in boot_rows
            if row.get("threshold") == "2"
            and row.get("comparison") == "app_shrink_k8_minus_app_shrink_k1"
        ),
        {},
    )
    status = "PROXY_ONLY" if decision.startswith("proxy_candidate") else "NOT_PROMOTED"
    return EvidenceRow(
        "player_sot_rate_shrinkage",
        "Stronger player SOT shrinkage is proxy evidence for 1+ SOT only; high-threshold SOT remains guarded.",
        status,
        (
            f"{keys.get('market_like_rows', '?')} attacking-position rows / "
            f"{keys.get('matches', '?')} StatsBomb matches"
        ),
        f"1+ app_k8 delta={app8_thr1.get('point_delta_x100', '?')} x100",
        f"2+ app_k8 delta={app8_thr2.get('point_delta_x100', '?')} x100",
        (
            f"1+ ci_2.5={app8_thr1.get('ci_2.5', '?')}, Pr>0={app8_thr1.get('pr_gt_0', '?')}; "
            f"2+ ci_2.5={app8_thr2.get('ci_2.5', '?')}; decision={decision}"
        ),
        "player_sot_rate_audit.md",
        "Do not change live SOT constants from this proxy alone; use it as a 1+ SOT research lead and keep 2+/3+ SOT prop/source/lineup-led.",
    )


def _player_goal_involvement_rate(log_dir: Path) -> EvidenceRow:
    text = _read(log_dir / "player_goal_involvement_rate_audit.md")
    keys = _key_values(text)
    decision = keys.get("decision", "not_run")
    boot_rows = _table_after(text, "Chronological Candidate Bootstraps")
    goal_app8 = next(
        (
            row
            for row in boot_rows
            if row.get("family") == "goal"
            and row.get("comparison") == "app_shrink_k8_minus_app_shrink_k6"
        ),
        {},
    )
    inv_app8 = next(
        (
            row
            for row in boot_rows
            if row.get("family") == "goal_or_assist"
            and row.get("comparison") == "app_shrink_k8_minus_app_shrink_k6"
        ),
        {},
    )
    status = "PROXY_ONLY" if decision.startswith("proxy_candidate") else "NOT_PROMOTED"
    return EvidenceRow(
        "player_goal_involvement_rate_shrinkage",
        "Do not change scorer or score-or-assist shrinkage from point estimates alone.",
        status,
        (
            f"{keys.get('market_like_rows', '?')} attacking-position rows / "
            f"{keys.get('matches', '?')} StatsBomb matches"
        ),
        f"goal app_k8 delta={goal_app8.get('point_delta_x100', '?')} x100",
        f"G+A app_k8 delta={inv_app8.get('point_delta_x100', '?')} x100",
        (
            f"goal ci_2.5={goal_app8.get('ci_2.5', '?')}; "
            f"G+A ci_2.5={inv_app8.get('ci_2.5', '?')}; decision={decision}"
        ),
        "player_goal_involvement_rate_audit.md",
        "Keep current goal/involvement shrinkage unless official replay or current prop integration clears a stronger gate.",
    )


def _penalty_red(log_dir: Path) -> EvidenceRow:
    text = _read(log_dir / "penalty_red_policy_backtest.md")
    fixed = _row(_table_after(text, "Fixed Policies"), "policy", "global_asym075_cap12")
    holdout = _row(_table_after(text, "Chronological Holdout"), "policy", "global_asym075_cap12")
    boot = _row(_table_after(text, "Match-Level Bootstrap"), "policy", "global_asym075_cap12")
    keys = _key_values(text)
    robustness = (
        f"robust configs={keys.get('robustness_positive_configs', '?')}/{keys.get('robustness_configs', '?')}, "
        f"min_delta={keys.get('robustness_min_delta_vs_current', '?')}"
        if keys.get("robustness_configs")
        else "sensitivity grid missing"
    )
    status = "GUARDRAIL_ONLY" if _float(boot.get("ci_2.5")) <= 0 else "PROMOTED"
    return EvidenceRow(
        "penalty_red",
        "Use low-event base/global asymmetric rule; no referee-specific penalty/red transform.",
        status,
        f"{fixed.get('n', '?')} penalty/red rows",
        f"{fixed.get('delta_vs_current', '?')} RBP",
        f"{holdout.get('delta_vs_current', '?')} RBP",
        f"ci_2.5={boot.get('ci_2.5', '?')}; {robustness}",
        "penalty_red_policy_backtest.md",
        "Avoid moving above the low-event base without a direct current anchor.",
    )


def _referee_card_context(log_dir: Path) -> EvidenceRow:
    text = _read(log_dir / "referee_research_audit.md")
    availability = _table_after(text, "Open Match Referee Availability")
    card_markets = sum(int(_float(row.get("card_markets"))) for row in availability)
    verified = sum(1 for row in availability if row.get("espn_officials", "-") not in {"", "-"})
    override = sum(1 for row in availability if row.get("local_override", "no_override") != "no_override")
    return EvidenceRow(
        "referee_card_context",
        "Do not add referee-specific card adjustments unless a current official and independent card-rate research are both verified.",
        "GUARDRAIL_ONLY",
        f"{len(availability)} open matches / {card_markets} card-like markets",
        f"verified_referee_matches={verified}",
        f"local_overrides={override}",
        "ESPN official fields often absent; strict-ref overrides are capped and no-field card rows stay neutral without verified context",
        "referee_research_audit.md",
        "Field values are unavailable pre-lock; keep neutral card factor unless the audit explicitly clears referee research.",
    )


def _referee_card_adjustment(log_dir: Path) -> EvidenceRow:
    text = _read(log_dir / "referee_card_adjustment_audit.md")
    keys = _key_values(text)
    decision = keys.get("decision", "not_run")
    threshold_rate = _row(
        _table_after(text, "Candidate Bootstraps"),
        "comparison",
        "referee_shrink20_minus_league_prior",
    )
    cap1 = _row(
        _table_after(text, "Candidate Bootstraps"),
        "comparison",
        "referee_cap1_poisson_minus_league_prior",
    )
    status = "PROXY_ONLY" if decision.startswith("proxy_candidate") else "NOT_PROMOTED"
    return EvidenceRow(
        "referee_card_adjustment",
        "Referee-specific card rates are proxy research only; live use needs verified current official and independent current rate source.",
        status,
        f"{keys.get('source_matches', '?')} Football-Data matches / {keys.get('unique_referees', '?')} referees",
        f"threshold-shrink delta={threshold_rate.get('point_delta_x100', '?')} x100",
        "Football-Data chronological replay only",
        (
            f"threshold ci_2.5={threshold_rate.get('ci_2.5', '?')}, Pr>0={threshold_rate.get('pr_gt_0', '?')}; "
            f"cap1 ci_2.5={cap1.get('ci_2.5', '?')}; decision={decision}"
        ),
        "referee_card_adjustment_audit.md",
        "Do not apply automatically; no referee transform is allowed without verified assignment plus archived current referee-rate research.",
    )


def _specials(log_dir: Path) -> EvidenceRow:
    text = _read(log_dir / "special_market_backtest.md")
    live_text = _read(log_dir / "special_market_live_audit.md")
    official = _key_values(_read(log_dir / "special_official_results_audit.md"))
    boundary = _key_values(_read(log_dir / "special_boundary_audit.md"))
    usable = sum(
        1
        for row in _table_after(text, "Leave-One-Tournament-Out Binary Baselines")
        if row.get("confidence") == "usable_base_prior"
    )
    weak = sum(
        1
        for row in _table_after(text, "Leave-One-Tournament-Out Binary Baselines")
        if row.get("confidence") == "weak_near_coinflip"
    )
    count_priors = _table_after(text, "Leave-One-Tournament-Out Count Threshold Priors")
    live = _key_values(live_text)
    risk_counts = {row.get("risk_state", ""): row.get("rows", "0") for row in _table_after(live_text, "Risk Summary")}
    weak_live = int(risk_counts.get("WEAK_PRIOR", "0"))
    count_scaled_far = int(risk_counts.get("COUNT_THRESHOLD_SCALED_FAR_FROM_BASE", "0"))
    heterogeneity_rows = _table_after(text, "Tournament Heterogeneity Diagnostics")
    heterogeneity_counts = Counter(row.get("heterogeneity", "") for row in heterogeneity_rows)
    heterogeneity_note = (
        f"heterogeneity high={heterogeneity_counts.get('high', 0)}, "
        f"moderate={heterogeneity_counts.get('moderate', 0)}, low={heterogeneity_counts.get('low', 0)}"
        if heterogeneity_rows
        else "heterogeneity audit missing"
    )
    live_note = (
        f"live rows={live.get('special_rows', '?')}; "
        f"{weak_live} weak-prior live rows, {count_scaled_far} count-threshold row far from base"
    )
    official_note = (
        f"official settled special rows={official.get('settled_special_rows', '?')}"
        f" (usable={official.get('usable_special_rows', '?')}, "
        f"special-like={official.get('special_like_settled_rows', '?')}, "
        f"unsupported={official.get('unsupported_special_rows', '?')}, "
        f"uninferable={official.get('uninferable_special_rows', '?')})"
    )
    adaptation = _row(_table_after(text, "Adaptation Bootstrap"), "scope", "knockout_only")
    adaptation_note = (
        f"knockout adaptation {adaptation.get('decision', '?')} "
        f"(delta={adaptation.get('point_delta', '?')}, ci_97.5={adaptation.get('ci_97.5', '?')})"
        if adaptation
        else "adaptation audit missing"
    )
    return EvidenceRow(
        "special_markets",
        "Use raw StatsBomb special priors; do not apply generic field transforms.",
        "PROXY_ONLY",
        f"314 StatsBomb matches; {official_note}; {live_note}",
        f"{usable} usable binary families; {len(count_priors)} usable count-threshold priors",
        "no official SportsPredict holdout",
        (
            f"{weak} weak near-coinflip families; {heterogeneity_note}; oracle-volume only for scaling; "
            f"{adaptation_note}; boundary_decision={boundary.get('decision', 'not_run')}"
        ),
        "special_market_backtest.md + special_market_live_audit.md + special_official_results_audit.md + special_boundary_audit.md",
        "Treat hydration-goal timing, current-tournament trends, and high-heterogeneity count-threshold rows as research warnings; do not claim official validation yet.",
    )


def _special_boundary(log_dir: Path) -> EvidenceRow:
    text = _read(log_dir / "special_boundary_audit.md")
    keys = _key_values(text)
    return EvidenceRow(
        "special_boundary",
        "Use raw special proxy priors only with explicit weak-prior/count-threshold guardrails.",
        "GUARDRAIL_ONLY",
        (
            f"{keys.get('current_special_rows', '?')} open special rows; "
            f"official settled={keys.get('official_settled_special_rows', '?')}"
        ),
        f"max_scaled_shift_pp={keys.get('max_scaled_shift_pp', '?')}",
        f"weak_prior_rows={keys.get('weak_prior_rows', '?')}",
        (
            f"shrinkage={keys.get('shrinkage_decision', '?')}, "
            f"weak_shrink_ci_2.5={keys.get('weak_shrink_ci_2.5', '?')}, "
            f"knockout_adaptation={keys.get('knockout_adaptation_decision', '?')}"
        ),
        "special_boundary_audit.md",
        "Do not apply field, shrinkage, trend, or catch-up transforms to special rows without a new promotion gate.",
    )


def _special_count_thresholds(log_dir: Path) -> EvidenceRow:
    text = _read(log_dir / "special_count_threshold_audit.md")
    keys = _key_values(text)
    decision = keys.get("decision", "not_run")
    adaptive = _row(
        _table_after(text, "Candidate Bootstraps"),
        "comparison",
        "adaptive_nb_mean_s64_minus_base_bernoulli",
    )
    nb = _row(
        _table_after(text, "Candidate Bootstraps"),
        "comparison",
        "nb_count_prior_minus_base_bernoulli",
    )
    status = "PROXY_ONLY" if decision.startswith("proxy_candidate") else "GUARDRAIL_ONLY"
    return EvidenceRow(
        "special_count_thresholds",
        "Count-threshold special adaptation is proxy research only; live total-shots/early-corner rows still need current source checks.",
        status,
        f"{keys.get('scored_rows', '?')} scored threshold rows / {keys.get('tournaments', '?')} tournaments",
        f"adaptive_nb_s64 delta={adaptive.get('point_delta_x100', '?')} x100",
        "StatsBomb leave-one-tournament-out threshold replay",
        (
            f"adaptive ci_2.5={adaptive.get('ci_2.5', '?')}, Pr>0={adaptive.get('pr_gt_0', '?')}; "
            f"nb_prior ci_2.5={nb.get('ci_2.5', '?')}; decision={decision}"
        ),
        "special_count_threshold_audit.md",
        "Do not apply as a live transform without current-tournament count outcomes, ex-ante count-volume data, or official SportsPredict replay.",
    )


def _special_prior_shrinkage(log_dir: Path) -> EvidenceRow:
    text = _read(log_dir / "special_prior_shrinkage_backtest.md")
    keys = _key_values(text)
    boot = _row(_table_after(text, "Best-Factor Bootstrap"), "scope", "weak_near_coinflip_binary")
    overall = _row(_table_after(text, "Best-Factor Bootstrap"), "scope", "all_special")
    status = "PROMOTED_LIMITED" if keys.get("promotion_decision") == "promoted" else "NOT_PROMOTED"
    return EvidenceRow(
        "special_prior_shrinkage",
        "Do not automatically shrink StatsBomb special priors toward 50%; raw priors remain the proxy default.",
        status,
        (
            f"{keys.get('total_scored_rows', '?')} proxy rows; "
            f"{keys.get('weak_near_coinflip_binary_rows', '?')} weak near-coinflip rows"
        ),
        f"overall best_factor={overall.get('best_factor', keys.get('best_overall_factor', '?'))}",
        (
            f"weak best_factor={boot.get('best_factor', keys.get('weak_best_factor', '?'))} "
            f"delta={boot.get('point_delta_vs_raw', keys.get('weak_point_delta', '?'))} RBP"
        ),
        (
            f"weak ci_2.5={boot.get('ci_2.5', keys.get('weak_ci_2.5', '?'))}, "
            f"Pr(>0)={boot.get('pr_gt_0', keys.get('weak_pr_gt_0', '?'))}"
        ),
        "special_prior_shrinkage_backtest.md",
        "Use weak-prior shrinkage signals only as current-source recheck warnings; do not transform live probabilities.",
    )


def _direct_polymarket(log_dir: Path) -> EvidenceRow:
    text = _read(log_dir / "polymarket_direct_coverage_audit.md")
    counts = {row.get("status", ""): row.get("markets", "0") for row in _table_after(text, "Coverage Summary")}
    score_path = log_dir / "polymarket_direct_snapshot_score.md"
    score_keys: dict[str, str] = {}
    latest_score = {}
    if score_path.exists():
        score_text = _read(score_path)
        score_keys = _key_values(score_text)
        latest_score = _row(_table_after(score_text, "Summary"), "slice", "latest per market")
    scored_latest = _float(score_keys.get("scored_latest_markets"))
    score_delta = latest_score.get("delta", "+0.0000")
    score_note = (
        f"latest scored={int(scored_latest)} delta={score_delta}, "
        f"wins={latest_score.get('anchor_wins', '?')}, "
        f"gate={score_keys.get('historical_promotion_decision', '?')}"
        if score_keys
        else "snapshot score report missing"
    )
    unscored = score_keys.get("unscored_latest_markets") or score_keys.get("unscored_snapshot_rows", "?")
    return EvidenceRow(
        "exact_polymarket_anchors",
        "Use exact same-question Polymarket non-player/player/corner anchors under semantic and volume gates.",
        "CURRENT_SOURCE_RULE",
        (
            f"{counts.get('promoted_non_player', '0')} non-player, "
            f"{counts.get('promoted_player_prop', '0')} player, "
            f"{counts.get('promoted_count_corner', '0')} corner; "
            f"{score_keys.get('snapshot_rows', '?')} snapshots"
        ),
        score_note,
        "current-source hierarchy",
        f"semantic tests guard false matches; unscored latest markets={unscored}",
        "polymarket_direct_coverage_audit.md + polymarket_direct_anchor_snapshots.csv + polymarket_direct_snapshot_score.md",
        (
            "Allowed only for exact wording, team/player, threshold, period, and volume matches; "
            "keep scoring archived latest-per-market rows as results settle before promoting beyond current-source."
        ),
    )


def _direct_polymarket_risk(log_dir: Path) -> EvidenceRow:
    text = _read(log_dir / "polymarket_direct_anchor_risk_audit.md")
    keys = _key_values(text)
    risk_counts = {row.get("risk_level", ""): row.get("markets", "0") for row in _table_after(text, "Risk Summary")}
    scored_latest = keys.get("scored_latest_markets", "?")
    high = keys.get("high_risk_rows", risk_counts.get("HIGH", "?"))
    medium = keys.get("medium_risk_rows", risk_counts.get("MEDIUM", "?"))
    large = keys.get("large_shift_rows", "?")
    return EvidenceRow(
        "exact_polymarket_anchor_risk",
        "Use the direct-anchor risk audit to recheck large exact-anchor shifts and fragile player/corner anchor types.",
        "GUARDRAIL_ONLY",
        f"{keys.get('current_anchor_rows', '?')} current anchors; high={high}, medium={medium}",
        f"large_shift_rows={large}",
        f"scored_latest={scored_latest}",
        f"gate={keys.get('historical_promotion_decision', '?')}",
        "polymarket_direct_anchor_risk_audit.md",
        "Do not transform probabilities from this audit alone; recheck large shifts, lineups, and alternate sources before lock.",
    )


def _odds_source_quality(log_dir: Path) -> EvidenceRow:
    text = _read(log_dir / "odds_anchor_audit.md")
    state_counts = {row.get("state", ""): row.get("matches", "0") for row in _table_after(text, "Source State Summary")}
    matches = _table_after(text, "Open Match Odds Sources")
    nonaligned = sum(
        int(_float(count))
        for state, count in state_counts.items()
        if state not in {"ALIGNED", "LOW_VOLUME_PM"}
    )
    max_shift = max((_float(row.get("blend_shift")) for row in matches), default=0.0)
    max_diff = max((_float(row.get("max_diff")) for row in matches), default=0.0)
    status = "CURRENT_SOURCE_RULE" if nonaligned == 0 else "GUARDRAIL_ONLY"
    return EvidenceRow(
        "odds_source_quality",
        "Use raw odds-goal anchors only after the source audit confirms bookmaker/Polymarket orientation and disagreement are clean.",
        status,
        ", ".join(f"{state}={count}" for state, count in sorted(state_counts.items())) or f"{len(matches)} open matches",
        f"max_book_pm_diff={max_diff:.3f}",
        f"max_blend_shift={max_shift:.3f}",
        "LOW_VOLUME_PM is a near-lock recheck flag; BOOK_ONLY or source disagreement requires targeted review before entry",
        "odds_anchor_audit.md",
        "Do not change probabilities from this audit alone; rerun odds anchors when any match is BOOK_ONLY, low-volume with material shift, or disagreed.",
    )


def _odds_conversion(log_dir: Path) -> EvidenceRow:
    text = _read(log_dir / "odds_conversion_backtest.md")
    keys = _key_values(text)
    decision = keys.get("decision", "not_run")
    x1_methods = _table_after(text, "1X2 Closing Odds")
    ou_methods = _table_after(text, "Over/Under 2.5 Closing Odds")
    x1_best = max(
        (row for row in x1_methods if row.get("method") != "multiplicative"),
        key=lambda row: _float(row.get("delta_vs_multiplicative_x100")),
        default={},
    )
    ou_best = max(
        (row for row in ou_methods if row.get("method") != "multiplicative"),
        key=lambda row: _float(row.get("delta_vs_multiplicative_x100")),
        default={},
    )
    x1_recent = _row(
        _table_after(text, "1X2 Closing Odds Recent Holdout Since 2023"),
        "method",
        x1_best.get("method", "power"),
    )
    ou_recent = _row(
        _table_after(text, "Over/Under 2.5 Closing Odds Recent Holdout Since 2023"),
        "method",
        ou_best.get("method", "power"),
    )
    x1_candidate_boot = _row(
        _table_after(text, "1X2 Closing Odds Candidate Bootstrap Summary"),
        "method",
        x1_best.get("method", "power"),
    )
    ou_candidate_boot = _row(
        _table_after(text, "Over/Under 2.5 Closing Odds Candidate Bootstrap Summary"),
        "method",
        ou_best.get("method", "power"),
    )
    promoted_method = decision.removeprefix("promote_").split("_devig", 1)[0] if decision.startswith("promote_") else ""
    status = "PROMOTED_LIMITED" if promoted_method else "NOT_PROMOTED"
    return EvidenceRow(
        "odds_conversion_power",
        "Keep proportional de-margin until favorite-longshot-aware de-vig clears both 1X2 and totals gates.",
        status,
        f"{keys.get('rows_1x2', '?')} 1X2 rows / {keys.get('rows_over_under_2.5', '?')} totals rows",
        (
            f"best 1X2={x1_best.get('method', '?')} {x1_best.get('delta_vs_multiplicative_x100', '?')} x100; "
            f"best totals={ou_best.get('method', '?')} {ou_best.get('delta_vs_multiplicative_x100', '?')} x100"
        ),
        (
            f"2023+ {x1_best.get('method', '?')} 1X2={x1_recent.get('delta_vs_multiplicative_x100', '?')} x100; "
            f"{ou_best.get('method', '?')} totals={ou_recent.get('delta_vs_multiplicative_x100', '?')} x100"
        ),
        (
            f"1X2 best ci_2.5={x1_candidate_boot.get('ci_2.5', '?')}, Pr>0={x1_candidate_boot.get('pr_gt_0', '?')}; "
            f"totals best ci_2.5={ou_candidate_boot.get('ci_2.5', '?')}, Pr>0={ou_candidate_boot.get('pr_gt_0', '?')}; "
            f"decision={decision}"
        ),
        "odds_conversion_backtest.md",
        "Do not change bookmaker odds conversion yet; power, Shin, OO-EPC, and FL-GLM are covered, and totals remain below the promotion gate.",
    )


def _odds_total_source_quality(log_dir: Path) -> EvidenceRow:
    path = log_dir / "odds_total_source_audit.md"
    if not path.exists():
        return EvidenceRow(
            "odds_total_source_quality",
            "Use the total-goals source audit to recheck exact total/team-goal anchors, thin derivative markets, and rows without direct total anchors.",
            "GUARDRAIL_ONLY",
            "audit missing",
            "unknown",
            "unknown",
            "run odds_total_source_audit.md before relying on total-source gates",
            "odds_total_source_audit.md + manual_evidence_coverage.md + polymarket_direct_coverage_audit.md",
            "Missing audit blocks any new total-source optimization; use existing odds-goal rules and rerun the audit.",
        )
    text = _read(path)
    keys = _key_values(text)
    state_counts = {row.get("source_state", ""): row.get("markets", "0") for row in _table_after(text, "Source State Summary")}
    total_rows = keys.get("total_goal_rows", "?")
    direct_rows = keys.get("direct_total_rows", "?")
    missing = keys.get("missing_direct_total_rows", state_counts.get("NO_DIRECT_TOTAL_ANCHOR", "?"))
    mismatch = keys.get("entry_mismatch_rows", state_counts.get("DIRECT_TOTAL_ENTRY_MISMATCH", "?"))
    large = keys.get("large_shift_rows", state_counts.get("DIRECT_TOTAL_LARGE_SHIFT", "?"))
    thin = keys.get("thin_direct_rows", state_counts.get("DIRECT_TOTAL_THIN", "?"))
    max_delta = keys.get("max_abs_direct_delta_pp", "?")
    return EvidenceRow(
        "odds_total_source_quality",
        "Use the total-goals source audit to recheck exact total/team-goal anchors, thin derivative markets, and rows without direct total anchors.",
        "GUARDRAIL_ONLY",
        f"{total_rows} current total/team-goal rows; direct={direct_rows}, missing_direct={missing}",
        f"large_shift_rows={large}",
        f"max_abs_direct_delta_pp={max_delta}",
        f"entry_mismatch_rows={mismatch}; thin_direct_rows={thin}; state_counts={', '.join(f'{state}={count}' for state, count in sorted(state_counts.items())) or 'missing'}",
        "odds_total_source_audit.md + manual_evidence_coverage.md + polymarket_direct_coverage_audit.md",
        "Do not transform probabilities from this audit alone; use it to prioritize near-lock totals-source and wording checks.",
    )


def _elo_history_residual(log_dir: Path) -> EvidenceRow:
    text = _read(log_dir / "elo_history_residual_audit.md")
    keys = _key_values(text)
    decision = keys.get("decision", "not_run")
    best_model = keys.get("best_history_model", "?")
    all_best = _row(_table_after(text, "All Scored Matches Since 1994"), "model", best_model)
    recent_best = _row(_table_after(text, "Recent Holdout Since 2021"), "model", best_model)
    recent_plain = _row(_table_after(text, "Recent Holdout Since 2021"), "model", "elo_history_w0")
    boot = _row(
        _table_after(text, "Best History Model Vs Plain Elo Year Bootstrap"),
        "comparison",
        f"{best_model}_minus_elo_history_w0",
    )
    status = "PROXY_ONLY" if decision == "candidate_history_feature_positive_unpriced_only" else "NOT_PROMOTED"
    return EvidenceRow(
        "elo_history_residual",
        "Recent Elo trajectory is allowed only as an unpriced national-team fallback candidate, never as an odds override.",
        status,
        f"{keys.get('scored_rows', '?')} scored rows / {keys.get('source_rows', '?')} source rows",
        f"{best_model} delta_vs_rolling_base={all_best.get('delta_vs_rolling_base_x100', '?')} x100",
        (
            f"2021+ {best_model} brier={recent_best.get('brier', '?')}; "
            f"plain_elo brier={recent_plain.get('brier', '?')}"
        ),
        (
            f"history-vs-plain-elo ci_2.5={boot.get('ci_2.5', '?')}, "
            f"Pr>0={boot.get('pr_gt_0', '?')}; decision={decision}"
        ),
        "elo_history_residual_audit.md",
        "Do not change priced goal/outcome probabilities; keep this for no-odds fallbacks or future archived odds-residual replay.",
    )


def _tail_risk(log_dir: Path) -> EvidenceRow:
    text = _read(log_dir / "leaderboard_tail_risk.md")
    hard = _row(_table_after(text, "Strategy Tail Distribution"), "strategy", "hard_model_side_99_1")
    manual = _row(_table_after(text, "Strategy Tail Distribution"), "strategy", "combined_manual_policy")
    return EvidenceRow(
        "catch_up_extremizing",
        "Do not hard-extremize probabilities to chase the leaderboard.",
        "REJECTED",
        f"{manual.get('strategy', 'combined_manual_policy')} vs hard strategies, 20k trials",
        f"manual mean={manual.get('mean', '?')}; hard mean={hard.get('mean', '?')}",
        "special-proxy open mix",
        f"hard Pr>=#1={hard.get('Pr >= 871.09', '?')}",
        "leaderboard_tail_risk.md",
        "Capture calibrated edge instead: lineups, prop anchors, current sources, and odds refreshes.",
    )


def build_matrix(log_dir: Path = LOG_DIR) -> list[EvidenceRow]:
    return [
        _combined_policy(log_dir),
        _global_calibration(log_dir),
        _family_linear_pool(log_dir),
        _calibration_watchlist(log_dir),
        _brier_decomposition_reliability(log_dir),
        _goal_policy(log_dir),
        _odds_source_quality(log_dir),
        _odds_conversion(log_dir),
        _odds_total_source_quality(log_dir),
        _elo_history_residual(log_dir),
        _count_policy(log_dir),
        _card_field_component(log_dir),
        _count_intensity_tuning(log_dir),
        _count_total_dependence(log_dir),
        _count_no_field(log_dir),
        _player_policy(log_dir),
        _player_no_field(log_dir),
        _current_player_stats_replay(log_dir),
        _player_prop_boundary(log_dir),
        _player_role_policy(log_dir),
        _player_minutes_uncertainty(log_dir),
        _player_sot_rate(log_dir),
        _player_goal_involvement_rate(log_dir),
        _penalty_red(log_dir),
        _referee_card_context(log_dir),
        _referee_card_adjustment(log_dir),
        _specials(log_dir),
        _special_boundary(log_dir),
        _special_count_thresholds(log_dir),
        _special_prior_shrinkage(log_dir),
        _direct_polymarket(log_dir),
        _direct_polymarket_risk(log_dir),
        _tail_risk(log_dir),
    ]


def render(rows: list[EvidenceRow], *, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    counts = Counter(row.status for row in rows)
    lines = [
        "# Strategy Evidence Matrix",
        "",
        f"- run_utc: {now.astimezone(timezone.utc).isoformat(timespec='seconds')}",
        "- bot_predictions: not_read",
        "- sports_predict: not_called",
        "- purpose: consolidate which manual strategy branches are promoted, limited, proxy-only, or rejected",
        "",
        "## Status Summary",
        "",
        "| status | rows |",
        "| --- | ---: |",
    ]
    for status in sorted(counts):
        lines.append(f"| {status} | {counts[status]} |")
    lines.extend(
        [
            "",
            "## Matrix",
            "",
            "| area | status | current rule | sample | primary_delta | holdout_delta | uncertainty | source | allowed action |",
            "| --- | --- | --- | --- | ---: | ---: | --- | --- | --- |",
        ]
    )
    for row in rows:
        lines.append(
            f"| {row.area} | {row.status} | {row.rule} | {row.sample} | "
            f"{row.primary_delta} | {row.holdout_delta} | {row.uncertainty} | "
            f"{row.source} | {row.action} |"
        )
    lines.extend(
        [
            "",
            "## Promotion Rule",
            "",
            "- `PROMOTED`: full-sample and chronological/holdout evidence are positive, with adequate bootstrap/probability support.",
            "- `PROMOTED_LIMITED`: evidence is positive only inside an explicitly bounded support set.",
            "- `CURRENT_SOURCE_RULE`: current exact market anchors are allowed by semantic/volume gates but lack adequate scored replay.",
            "- `PROXY_ONLY`: useful prior evidence exists, but official SportsPredict settlement is absent or incomplete.",
            "- `GUARDRAIL_ONLY`: direction is plausible, but uncertainty is too wide for an aggressive promotion.",
            "- `NOT_PROMOTED` or `REJECTED`: do not use as an optimization rule.",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write", action="store_true", help=f"write {OUT}")
    args = ap.parse_args()

    text = render(build_matrix())
    print(text)
    if args.write:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(text, encoding="utf-8")
        print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
