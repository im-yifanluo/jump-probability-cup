"""Audit how strong each manual strategy promotion claim really is.

This is a read-only consistency report over existing backtest logs. It does not
call SportsPredict, does not read `/predictions`, and does not submit anything.
The purpose is to separate strong promotions from useful-but-soft evidence so
the manual workflow does not overclaim edge or hide weak direct comparisons.
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
OUT = LOG_DIR / "promotion_strength_audit.md"


@dataclass(frozen=True)
class PromotionStrengthRow:
    area: str
    matrix_status: str
    strength: str
    primary_evidence: str
    weak_point: str
    action: str


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


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


def _row(rows: list[dict[str, str]], key: str, value: str) -> dict[str, str]:
    for row in rows:
        if row.get(key) == value:
            return row
    return {}


def _float(value: str | None, default: float = 0.0) -> float:
    if value is None:
        return default
    cleaned = value.strip().replace("%", "").replace(",", "")
    if cleaned in {"", "-"}:
        return default
    return float(cleaned)


def _matrix_statuses(log_dir: Path) -> dict[str, str]:
    matrix = _read(log_dir / "strategy_evidence_matrix.md")
    return {row.get("area", ""): row.get("status", "") for row in _table_after(matrix, "Matrix")}


def _strength_from_ci(ci: float, pr: float | None = None) -> str:
    if ci > 0:
        return "STRONG"
    if pr is not None and pr >= 0.95:
        return "SOFT_POSITIVE"
    return "WEAK_OR_UNPROMOTED"


def build_rows(log_dir: Path = LOG_DIR) -> list[PromotionStrengthRow]:
    statuses = _matrix_statuses(log_dir)
    rows: list[PromotionStrengthRow] = []

    manual_text = _read(log_dir / "manual_policy_backtest.md")
    manual_keys = _key_values(manual_text)
    manual_holdout = _row(_table_after(manual_text, "Chronological Holdout"), "policy", "combined_manual_policy")
    manual_ci = _float(manual_keys.get("ci_2.5"))
    rows.append(
        PromotionStrengthRow(
            "combined_manual_policy",
            statuses.get("combined_manual_policy", "?"),
            "PROMOTED_STRONG" if manual_ci > 0 else "PROMOTED_SOFT",
            f"holdout_delta={manual_holdout.get('delta_vs_current', '?')}, ci_2.5={manual_keys.get('ci_2.5', '?')}",
            "requires field only for historical field-policy branches; pre-lock field is unavailable",
            "Use only when the required live inputs exist; keep player/source gates in force.",
        )
    )

    goal_text = _read(log_dir / "goal_policy_backtest.md")
    goal_compare = _row(_table_after(goal_text, "Direct Goal Policy Comparisons"), "comparison", "raw_current_minus_field")
    goal_shrink = _row(
        _table_after(goal_text, "Direct Goal Policy Comparisons"),
        "comparison",
        "raw_current_minus_best_shrink75",
    )
    goal_strength = _strength_from_ci(
        _float(goal_compare.get("bootstrap_ci_2.5")),
        _float(goal_compare.get("bootstrap_pr_gt_0")),
    )
    rows.append(
        PromotionStrengthRow(
            "odds_goal_rows",
            statuses.get("odds_goal_rows", "?"),
            "PROMOTED_STRONG" if goal_strength == "STRONG" else "PROMOTED_SOFT",
            (
                f"raw_vs_field holdout={goal_compare.get('chronological_holdout_delta', '?')}, "
                f"ci_2.5={goal_compare.get('bootstrap_ci_2.5', '?')}, "
                f"Pr>0={goal_compare.get('bootstrap_pr_gt_0', '?')}"
            ),
            (
                f"raw_vs_shrink75 ci_2.5={goal_shrink.get('bootstrap_ci_2.5', '?')}, "
                f"Pr>0={goal_shrink.get('bootstrap_pr_gt_0', '?')}"
            ),
            "Keep raw odds-goal anchors, but do not add extremization or new odds transforms from this evidence alone.",
        )
    )

    odds_conversion_text = _read(log_dir / "odds_conversion_backtest.md")
    odds_conversion_keys = _key_values(odds_conversion_text)
    x1_methods = _table_after(odds_conversion_text, "1X2 Closing Odds")
    ou_methods = _table_after(odds_conversion_text, "Over/Under 2.5 Closing Odds")
    x1_best = max(
        (row for row in x1_methods if row.get("method") != "multiplicative"),
        key=lambda row: _float(row.get("delta_vs_multiplicative_x100")),
        default={"method": "power"},
    )
    ou_best = max(
        (row for row in ou_methods if row.get("method") != "multiplicative"),
        key=lambda row: _float(row.get("delta_vs_multiplicative_x100")),
        default={"method": "power"},
    )
    x1_boot = _row(
        _table_after(odds_conversion_text, "1X2 Closing Odds Candidate Bootstrap Summary"),
        "method",
        x1_best.get("method", "power"),
    )
    ou_boot = _row(
        _table_after(odds_conversion_text, "Over/Under 2.5 Closing Odds Candidate Bootstrap Summary"),
        "method",
        ou_best.get("method", "power"),
    )
    rows.append(
        PromotionStrengthRow(
            "odds_conversion_power",
            statuses.get("odds_conversion_power", "?"),
            "NOT_PROMOTED_TOTALS_GATE",
            (
                f"1X2 best={x1_best.get('method', '?')} ci_2.5={x1_boot.get('ci_2.5', '?')}, "
                f"Pr>0={x1_boot.get('pr_gt_0', '?')}"
            ),
            (
                f"totals best={ou_best.get('method', '?')} ci_2.5={ou_boot.get('ci_2.5', '?')}, "
                f"Pr>0={ou_boot.get('pr_gt_0', '?')}; "
                f"decision={odds_conversion_keys.get('decision', 'not_run')}"
            ),
            "Keep proportional de-margin; power, OO-EPC, Shin, odds-ratio, and FL-GLM remain monitored leads until totals clear the same gate.",
        )
    )

    count_text = _read(log_dir / "count_policy_backtest.md")
    card_component = _key_values(_read(log_dir / "card_field_component_audit.md"))
    count_fixed = _row(_table_after(count_text, "Match-Level Bootstrap"), "policy", "strong_sot_current_cards_field_else_global")
    count_component = _row(
        _table_after(count_text, "Direct Count Policy Comparisons"),
        "comparison",
        "promoted_minus_strong_sot_no_card_field",
    )
    count_strength = "PROMOTED_STRONG" if _float(count_fixed.get("ci_2.5")) > 0 else "PROMOTED_SOFT"
    if _float(count_component.get("bootstrap_ci_2.5")) <= 0:
        count_strength += "_COMPONENT_WARNING"
    rows.append(
        PromotionStrengthRow(
            "count_field_policy",
            statuses.get("count_field_policy", "?"),
            count_strength,
            (
                f"overall ci_2.5={count_fixed.get('ci_2.5', '?')}, "
                f"point={count_fixed.get('point', '?')}"
            ),
            (
                "increment over strong-SOT/no-card-field alternative: "
                f"holdout={count_component.get('chronological_holdout_delta', '?')}, "
                f"ci_2.5={count_component.get('bootstrap_ci_2.5', '?')}, "
                f"Pr>0={count_component.get('bootstrap_pr_gt_0', '?')}; "
                f"card_component_decision={card_component.get('decision', 'not_run')}"
            ),
            "Keep the documented count policy; pre-lock card rows use no-field/current-source values because field is unavailable, and do not mine extra card/corner transforms from the component warning.",
        )
    )

    player_text = _read(log_dir / "player_policy_backtest.md")
    player_boundary = _key_values(_read(log_dir / "player_prop_boundary_audit.md"))
    player_fixed = _row(_table_after(player_text, "Match-Level Bootstrap"), "policy", "asym075_cap10")
    player_vs_field = _row(
        _table_after(player_text, "Direct Player Policy Comparison"),
        "comparison",
        "asym075_cap10_minus_field",
    )
    player_strength = "PROMOTED_LIMITED_STRONG" if _float(player_fixed.get("ci_2.5")) > 0 else "PROMOTED_LIMITED_SOFT"
    if _float(player_vs_field.get("bootstrap_ci_2.5")) <= 0:
        player_strength += "_FIELD_WARNING"
    rows.append(
        PromotionStrengthRow(
            "researched_player_props",
            statuses.get("researched_player_props", "?"),
            player_strength,
            (
                f"overall ci_2.5={player_fixed.get('ci_2.5', '?')}, "
                f"point={player_fixed.get('point', '?')}"
            ),
            (
                f"vs_field holdout={player_vs_field.get('chronological_holdout_delta', '?')}, "
                f"ci_2.5={player_vs_field.get('bootstrap_ci_2.5', '?')}, "
                f"Pr>0={player_vs_field.get('bootstrap_pr_gt_0', '?')}; "
                f"boundary_decision={player_boundary.get('decision', 'not_run')}"
            ),
            "Use this only after player research is cleared; unresolved player rows stay exact-prop/lineup led.",
        )
    )

    no_field_text = _read(log_dir / "player_no_field_policy_backtest.md")
    no_field_boot = _row(
        _table_after(no_field_text, "Match-Level Bootstrap"),
        "comparison",
        "raw_scores_else_global_base_minus_raw_model",
    )
    rows.append(
        PromotionStrengthRow(
            "player_no_field_fallback",
            statuses.get("player_no_field_fallback", "?"),
            "PROMOTED_LIMITED_STRONG" if _float(no_field_boot.get("ci_2.5")) > 0 else "PROMOTED_LIMITED_SOFT",
            f"ci_2.5={no_field_boot.get('ci_2.5', '?')}, point={no_field_boot.get('point_delta', '?')}",
            "support excludes prior-only, team-unknown, bench, unmatched-lineup, and high-threshold SOT rows",
            "Keep the source-limited no-field fallback exactly within tested support.",
        )
    )

    direct_text = _read(log_dir / "polymarket_direct_snapshot_score.md")
    direct_keys = _key_values(direct_text)
    direct_latest = _row(_table_after(direct_text, "Summary"), "slice", "latest per market")
    rows.append(
        PromotionStrengthRow(
            "exact_polymarket_anchors",
            statuses.get("exact_polymarket_anchors", "?"),
            "CURRENT_SOURCE_ONLY_NEGATIVE_REPLAY",
            (
                f"scored_latest={direct_keys.get('scored_latest_markets', '?')}, "
                f"delta={direct_latest.get('delta', '?')}, "
                f"gate={direct_keys.get('historical_promotion_decision', '?')}"
            ),
            "current exact anchors are useful source hierarchy, not historical edge yet",
            "Keep semantic/volume gates and direct-anchor risk checks; keep accumulating scored snapshots.",
        )
    )

    special_text = _read(log_dir / "special_official_results_audit.md")
    special_keys = _key_values(special_text)
    special_boundary = _key_values(_read(log_dir / "special_boundary_audit.md"))
    rows.append(
        PromotionStrengthRow(
            "special_markets",
            statuses.get("special_markets", "?"),
            "PROXY_ONLY_OFFICIAL_SAMPLE_TOO_SMALL",
            f"official_settled_special_rows={special_keys.get('settled_special_rows', '?')}",
            (
                "StatsBomb proxy remains useful but not official SportsPredict validation; "
                f"boundary_decision={special_boundary.get('decision', 'not_run')}"
            ),
            "Use raw proxy priors with live weak-prior/count-volume warnings; do not apply generic field transforms.",
        )
    )

    return rows


def render(rows: list[PromotionStrengthRow], *, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    counts = Counter(row.strength for row in rows)
    warning_rows = [
        row
        for row in rows
        if any(token in row.strength for token in ("SOFT", "WARNING", "CURRENT_SOURCE_ONLY", "PROXY_ONLY", "NOT_PROMOTED"))
    ]
    lines = [
        "# Promotion Strength Audit",
        "",
        "Read-only analysis. `/predictions` was not read and no submissions were made.",
        "",
        f"- run_utc: {now.astimezone(timezone.utc).isoformat(timespec='seconds')}",
        "- sports_predict: not_called",
        "- decision: classification_only_no_probability_transform",
        f"- audited_areas: {len(rows)}",
        f"- warning_rows: {len(warning_rows)}",
        "",
        "## Strength Summary",
        "",
        "| strength | areas |",
        "| --- | ---: |",
    ]
    for strength in sorted(counts):
        lines.append(f"| {strength} | {counts[strength]} |")

    lines.extend(
        [
            "",
            "## Areas",
            "",
            "| area | matrix_status | strength | primary_evidence | weak_point | action |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in rows:
        lines.append(
            f"| {row.area} | {row.matrix_status} | {row.strength} | "
            f"{row.primary_evidence} | {row.weak_point} | {row.action} |"
        )

    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- `PROMOTED_STRONG` means the branch has positive holdout evidence and a positive match-bootstrap lower bound for its main comparison.",
            "- `PROMOTED_SOFT` means the branch is directionally useful and may be operationally correct, but the direct bootstrap lower bound is not strictly positive.",
            "- `*_WARNING` marks a promoted branch whose overall evidence is stronger than one important component comparison.",
            "- `CURRENT_SOURCE_ONLY` and `PROXY_ONLY` rows are not historical strategy promotions.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write", action="store_true", help=f"write {OUT}")
    ap.add_argument("--log", type=Path, default=OUT, help="log path")
    args = ap.parse_args()

    report = render(build_rows())
    print(report)
    if args.write:
        args.log.parent.mkdir(parents=True, exist_ok=True)
        args.log.write_text(report, encoding="utf-8")
        print(f"wrote {args.log}")


if __name__ == "__main__":
    main()
