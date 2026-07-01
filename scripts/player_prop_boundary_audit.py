"""Audit player-prop policy boundaries for manual entry.

This read-only diagnostic separates three claims that can otherwise get mixed
together:

1. pre-lock unresolved player props should generally be exact-prop/lineup led
   because field values are unavailable;
2. the no-field global-base fallback is strong only inside tested support; and
3. current unresolved/source-poor/high-threshold rows should stay research
   dependent, not be treated as validated edges.

It does not read `/predictions` and does not submit. With `--sports-predict`,
SportsPredict is used only for GET settlement of older V1 history rows through
`player_policy_backtest.load_player_rows`.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import re
import sys
from typing import Callable


ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "data" / "logs"
OUT = LOG_DIR / "player_prop_boundary_audit.md"

sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from backtest.history import HistoryMarket, asymmetric_field_policy, clip_prob, crowd_strategy  # noqa: E402
from manual.policy import (  # noqa: E402
    NO_FIELD_PLAYER_SOURCE_UNVALIDATED_FLAGS,
    unresolved_player_flags,
    validated_player_no_field_base_applies,
)
from player_no_field_policy_backtest import (  # noqa: E402
    bootstrap_policy_diff,
    chronological_predictions,
    player_bucket,
    score_field_context,
    score_predictions,
)
from player_policy_backtest import bootstrap_strategy_diff, load_player_rows, score  # noqa: E402
from questions.parser import parse  # noqa: E402
from questions.teams import ALL_TEAM_NAMES  # noqa: E402


Strategy = Callable[[HistoryMarket], float]
PLAYER_ASYM = asymmetric_field_policy(above_k=0.0, below_k=0.75, max_abs_dev=0.10)


@dataclass(frozen=True)
class ComparisonRow:
    comparison: str
    n: int
    full_sample_delta: float
    chronological_holdout_delta: float
    bootstrap_ci_2_5: float
    bootstrap_median: float
    bootstrap_ci_97_5: float
    bootstrap_pr_gt_0: float
    interpretation: str


@dataclass(frozen=True)
class CurrentPlayerContext:
    current_player_props: int
    high_priority_rows: int
    medium_priority_rows: int
    current_prop_anchor_rows: int
    source_unvalidated_rows: int
    global_minutes_unconfirmed_rows: int
    scorer_prop_recheck_rows: int
    lineup_not_posted_rows: int
    high_threshold_sot_rows: int
    no_field_base_supported_rows: int
    no_field_base_applied_rows: int
    raw_unvalidated_threshold_rows: int
    research_required_rows: int


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


def _int(value: str | None, default: int = 0) -> int:
    if value is None:
        return default
    value = value.strip().replace(",", "")
    if value in {"", "-"}:
        return default
    return int(float(value))


def _flags(value: str | None) -> set[str]:
    if not value or value == "OK":
        return set()
    return {part.strip() for part in value.split(",") if part.strip()}


def md_table(headers: list[str], rows: list[list[str]]) -> list[str]:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return lines


def chronological_holdout_rows(rows: list[HistoryMarket], min_train: int) -> list[HistoryMarket]:
    holdout: list[HistoryMarket] = []
    for order in sorted({row.order for row in rows}):
        if sum(1 for row in rows if row.order < order) >= min_train:
            holdout.extend([row for row in rows if row.order == order])
    return holdout


def _delta(rows: list[HistoryMarket], left: Strategy, right: Strategy) -> float:
    return score(rows, left)["delta"] - score(rows, right)["delta"]


def _comparison(
    label: str,
    rows: list[HistoryMarket],
    holdout_rows: list[HistoryMarket],
    left: Strategy,
    right: Strategy,
    *,
    trials: int,
    seed: int,
    interpretation: str,
) -> ComparisonRow:
    if trials:
        boot = bootstrap_strategy_diff(rows, left, right, trials=trials, seed=seed)
    else:
        boot = {"ci_2.5": 0.0, "median": 0.0, "ci_97.5": 0.0, "pr_gt_0": 0.0}
    return ComparisonRow(
        comparison=label,
        n=len(rows),
        full_sample_delta=_delta(rows, left, right),
        chronological_holdout_delta=_delta(holdout_rows, left, right),
        bootstrap_ci_2_5=boot["ci_2.5"],
        bootstrap_median=boot["median"],
        bootstrap_ci_97_5=boot["ci_97.5"],
        bootstrap_pr_gt_0=boot["pr_gt_0"],
        interpretation=interpretation,
    )


def current_player_rows(log_dir: Path = LOG_DIR) -> list[dict[str, str]]:
    return _table_after(_read(log_dir / "player_prop_diagnostics.md"), "Current Open Player Props")


def current_player_context(log_dir: Path = LOG_DIR) -> tuple[CurrentPlayerContext, Counter[str]]:
    rows = current_player_rows(log_dir)
    manual_policy_counts: Counter[str] = Counter()
    high_threshold_sot = 0
    no_field_supported = 0
    source_unvalidated = 0
    for row in rows:
        manual_policy_counts[row.get("manual_policy", "")] += 1
        flags = _flags(row.get("flags"))
        if flags & NO_FIELD_PLAYER_SOURCE_UNVALIDATED_FLAGS:
            source_unvalidated += 1
        try:
            spec = parse(row.get("question", ""), teams=ALL_TEAM_NAMES)
        except Exception:
            continue
        if spec.family == "player_sot_ge" and (spec.threshold or 1) > 1:
            high_threshold_sot += 1
        if validated_player_no_field_base_applies(spec):
            no_field_supported += 1

    context = CurrentPlayerContext(
        current_player_props=len(rows),
        high_priority_rows=sum(1 for row in rows if row.get("priority") == "HIGH"),
        medium_priority_rows=sum(1 for row in rows if row.get("priority") == "MEDIUM"),
        current_prop_anchor_rows=sum(1 for row in rows if "CURRENT_PROP_MARKET_ANCHOR" in _flags(row.get("flags"))),
        source_unvalidated_rows=source_unvalidated,
        global_minutes_unconfirmed_rows=sum(
            1 for row in rows if "GLOBAL_MINUTES_UNCONFIRMED" in _flags(row.get("flags"))
        ),
        scorer_prop_recheck_rows=sum(1 for row in rows if "SCORER_PROP_RECHECK" in _flags(row.get("flags"))),
        lineup_not_posted_rows=sum(1 for row in rows if "LINEUP_NOT_POSTED" in _flags(row.get("flags"))),
        high_threshold_sot_rows=high_threshold_sot,
        no_field_base_supported_rows=no_field_supported,
        no_field_base_applied_rows=manual_policy_counts.get("player_no_field_raw_scores_else_global_base", 0),
        raw_unvalidated_threshold_rows=(
            manual_policy_counts.get("player_no_field_raw_model_unvalidated_threshold", 0)
            + manual_policy_counts.get("player_no_field_raw_model_unvalidated_threshold_and_source", 0)
        ),
        research_required_rows=sum(
            count for policy, count in manual_policy_counts.items() if policy.startswith("research_required")
        ),
    )
    return context, manual_policy_counts


def build_historical_comparisons(
    rows: list[HistoryMarket],
    *,
    min_train: int,
    trials: int,
    seed: int,
) -> tuple[list[ComparisonRow], dict[str, str], list[dict[str, str]]]:
    holdout = chronological_holdout_rows(rows, min_train)
    model_above = [row for row in rows if row.you > row.crowd + 0.03]
    model_above_holdout = [row for row in holdout if row.you > row.crowd + 0.03]
    sot_rows = [row for row in rows if row.famkey == "player_sot_ge/sot"]
    sot_holdout = [row for row in holdout if row.famkey == "player_sot_ge/sot"]
    score_or_assist_rows = [row for row in rows if row.family == "player_scores_or_assists"]
    score_or_assist_holdout = [row for row in holdout if row.family == "player_scores_or_assists"]

    comparisons = [
        _comparison(
            "field_minus_raw_all_player_props",
            rows,
            holdout,
            crowd_strategy,
            lambda row: row.you,
            trials=trials,
            seed=seed,
            interpretation="historical field-visible comparison; pre-lock field values are unavailable",
        ),
        _comparison(
            "asym075_minus_field_all_player_props",
            rows,
            holdout,
            PLAYER_ASYM,
            crowd_strategy,
            trials=trials,
            seed=seed + 1,
            interpretation="tests whether researched-player asymmetric policy beats simply using field",
        ),
        _comparison(
            "field_minus_raw_when_model_above_field",
            model_above,
            model_above_holdout,
            crowd_strategy,
            lambda row: row.you,
            trials=trials,
            seed=seed + 2,
            interpretation="above-field player model rows are the dangerous direction for manual entries",
        ),
        _comparison(
            "field_minus_raw_player_sot_1plus_family",
            sot_rows,
            sot_holdout,
            crowd_strategy,
            lambda row: row.you,
            trials=trials,
            seed=seed + 3,
            interpretation="historical 1+ SOT player family lagged field",
        ),
        _comparison(
            "field_minus_raw_score_or_assist_family",
            score_or_assist_rows,
            score_or_assist_holdout,
            crowd_strategy,
            lambda row: row.you,
            trials=trials,
            seed=seed + 4,
            interpretation="historical score-or-assist player family lagged field",
        ),
    ]

    scored = chronological_predictions(
        rows,
        min_train=min_train,
        global_strength=2.0,
        family_strength=4.0,
    )
    no_field_policy = "raw_scores_else_global_base"
    no_field_boot = bootstrap_policy_diff(scored, no_field_policy, "raw_model", trials=trials, seed=seed + 5)
    raw_vs_field_boot = bootstrap_policy_diff(scored, "raw_model", "visible_field_context", trials=trials, seed=seed + 6)
    no_field_point = score_predictions(scored, no_field_policy)["delta_vs_raw"] if scored else 0.0
    raw_vs_field_point = -score_field_context(scored)["delta_vs_raw"] if scored else 0.0
    bucket_counts = Counter(player_bucket(item.row) for item in scored)
    bucket_rows = [{"bucket": bucket, "holdout_rows": str(count)} for bucket, count in sorted(bucket_counts.items())]
    meta = {
        "settled_player_rows": str(len(rows)),
        "chronological_player_rows": str(len(scored)),
        "no_field_policy": no_field_policy,
        "no_field_point_delta": f"{no_field_point:.2f}",
        "no_field_ci_2.5": f"{no_field_boot['ci_2.5']:.2f}",
        "no_field_pr_gt_0": f"{no_field_boot['pr_gt_0']:.3f}",
        "raw_model_minus_visible_field_context_point_delta": f"{raw_vs_field_point:.2f}",
        "raw_model_minus_visible_field_context_ci_2.5": f"{raw_vs_field_boot['ci_2.5']:.2f}",
        "raw_model_minus_visible_field_context_pr_gt_0": f"{raw_vs_field_boot['pr_gt_0']:.3f}",
        "supported_holdout_buckets": ", ".join(f"{row['bucket']}:{row['holdout_rows']}" for row in bucket_rows),
        "higher_threshold_sot_holdout_rows": str(
            sum(
                count
                for bucket, count in bucket_counts.items()
                if bucket.startswith("player_sot_ge/sot/ge") and bucket not in {"player_sot_ge/sot/ge1"}
            )
        ),
    }
    return comparisons, meta, bucket_rows


def decision(meta: dict[str, str], comparisons: list[ComparisonRow], context: CurrentPlayerContext) -> str:
    asym_vs_field = next((row for row in comparisons if row.comparison == "asym075_minus_field_all_player_props"), None)
    no_field_ci = float(meta.get("no_field_ci_2.5", "0"))
    higher_threshold_holdout = _int(meta.get("higher_threshold_sot_holdout_rows"))
    if (
        asym_vs_field
        and asym_vs_field.bootstrap_ci_2_5 <= 0
        and no_field_ci > 0
        and context.current_player_props > 0
        and (context.source_unvalidated_rows > 0 or context.high_threshold_sot_rows > 0)
        and higher_threshold_holdout == 0
    ):
        return "field_or_exact_prop_required_for_unresolved_players_limited_no_field_base"
    return "review_required_before_player_policy_change"


def render(
    rows: list[HistoryMarket],
    *,
    comparisons: list[ComparisonRow],
    meta: dict[str, str],
    bucket_rows: list[dict[str, str]],
    context: CurrentPlayerContext,
    manual_policy_counts: Counter[str],
    sports_predict: bool,
    trials: int,
    seed: int,
    now: datetime | None = None,
) -> str:
    now = now or datetime.now(timezone.utc)
    current_decision = decision(meta, comparisons, context)
    lines = [
        "# Player Prop Boundary Audit",
        "",
        "Read-only analysis. `/predictions` was not read and no submissions were made.",
        "",
        f"- run_utc: {now.astimezone(timezone.utc).isoformat(timespec='seconds')}",
        f"- sports_predict: {'read_only_get_settlement' if sports_predict else 'not_called'}",
        f"- bootstrap_trials: {trials}",
        f"- bootstrap_seed: {seed}",
        f"- settled_player_rows: {meta['settled_player_rows']}",
        f"- chronological_player_rows: {meta['chronological_player_rows']}",
        f"- current_player_props: {context.current_player_props}",
        f"- high_priority_player_rows: {context.high_priority_rows}",
        f"- medium_priority_player_rows: {context.medium_priority_rows}",
        f"- current_prop_anchor_rows: {context.current_prop_anchor_rows}",
        f"- source_unvalidated_rows: {context.source_unvalidated_rows}",
        f"- global_minutes_unconfirmed_rows: {context.global_minutes_unconfirmed_rows}",
        f"- scorer_prop_recheck_rows: {context.scorer_prop_recheck_rows}",
        f"- lineup_not_posted_rows: {context.lineup_not_posted_rows}",
        f"- high_threshold_sot_rows: {context.high_threshold_sot_rows}",
        f"- no_field_base_supported_current_rows: {context.no_field_base_supported_rows}",
        f"- no_field_base_applied_current_rows: {context.no_field_base_applied_rows}",
        f"- raw_unvalidated_threshold_current_rows: {context.raw_unvalidated_threshold_rows}",
        f"- research_required_current_rows: {context.research_required_rows}",
        f"- no_field_policy: {meta['no_field_policy']}",
        f"- no_field_point_delta: {meta['no_field_point_delta']}",
        f"- no_field_ci_2.5: {meta['no_field_ci_2.5']}",
        f"- no_field_pr_gt_0: {meta['no_field_pr_gt_0']}",
        f"- raw_model_minus_visible_field_context_point_delta: {meta['raw_model_minus_visible_field_context_point_delta']}",
        f"- raw_model_minus_visible_field_context_ci_2.5: {meta['raw_model_minus_visible_field_context_ci_2.5']}",
        f"- raw_model_minus_visible_field_context_pr_gt_0: {meta['raw_model_minus_visible_field_context_pr_gt_0']}",
        f"- higher_threshold_sot_holdout_rows: {meta['higher_threshold_sot_holdout_rows']}",
        f"- decision: {current_decision}",
        "",
        "## Historical Field Comparisons",
        "",
    ]
    lines += md_table(
        [
            "comparison",
            "n",
            "full_sample_delta",
            "chronological_holdout_delta",
            "bootstrap_ci_2.5",
            "bootstrap_median",
            "bootstrap_ci_97.5",
            "bootstrap_pr_gt_0",
            "interpretation",
        ],
        [
            [
                row.comparison,
                str(row.n),
                f"{row.full_sample_delta:.2f}",
                f"{row.chronological_holdout_delta:.2f}",
                f"{row.bootstrap_ci_2_5:.2f}",
                f"{row.bootstrap_median:.2f}",
                f"{row.bootstrap_ci_97_5:.2f}",
                f"{row.bootstrap_pr_gt_0:.3f}",
                row.interpretation,
            ]
            for row in comparisons
        ],
    )
    lines += [
        "",
        "## No-Field Support",
        "",
        "| metric | value |",
        "| --- | ---: |",
        f"| no_field_policy | {meta['no_field_policy']} |",
        f"| no_field_point_delta | {meta['no_field_point_delta']} |",
        f"| no_field_ci_2.5 | {meta['no_field_ci_2.5']} |",
        f"| no_field_pr_gt_0 | {meta['no_field_pr_gt_0']} |",
        f"| raw_model_minus_visible_field_context_point_delta | {meta['raw_model_minus_visible_field_context_point_delta']} |",
        f"| raw_model_minus_visible_field_context_ci_2.5 | {meta['raw_model_minus_visible_field_context_ci_2.5']} |",
        f"| higher_threshold_sot_holdout_rows | {meta['higher_threshold_sot_holdout_rows']} |",
        "",
        "## Holdout Buckets",
        "",
    ]
    lines += md_table(
        ["bucket", "holdout_rows"],
        [[row["bucket"], row["holdout_rows"]] for row in bucket_rows],
    )
    lines += [
        "",
        "## Current Open Player Context",
        "",
        "| metric | value |",
        "| --- | ---: |",
        f"| current_player_props | {context.current_player_props} |",
        f"| high_priority_player_rows | {context.high_priority_rows} |",
        f"| medium_priority_player_rows | {context.medium_priority_rows} |",
        f"| current_prop_anchor_rows | {context.current_prop_anchor_rows} |",
        f"| source_unvalidated_rows | {context.source_unvalidated_rows} |",
        f"| global_minutes_unconfirmed_rows | {context.global_minutes_unconfirmed_rows} |",
        f"| scorer_prop_recheck_rows | {context.scorer_prop_recheck_rows} |",
        f"| lineup_not_posted_rows | {context.lineup_not_posted_rows} |",
        f"| high_threshold_sot_rows | {context.high_threshold_sot_rows} |",
        f"| no_field_base_supported_current_rows | {context.no_field_base_supported_rows} |",
        f"| no_field_base_applied_current_rows | {context.no_field_base_applied_rows} |",
        f"| raw_unvalidated_threshold_current_rows | {context.raw_unvalidated_threshold_rows} |",
        f"| research_required_current_rows | {context.research_required_rows} |",
        "",
        "## Current Manual Policy Counts",
        "",
    ]
    lines += md_table(
        ["manual_policy", "current_rows"],
        [[policy or "missing", str(count)] for policy, count in sorted(manual_policy_counts.items())],
    )
    lines += [
        "",
        "## Interpretation",
        "",
        "- Player props remain an execution-risk family: unresolved rows need exact current prop odds, confirmed lineup research, or structured player research.",
        "- The no-field global-base fallback is promoted only inside tested support: scorer rows stay raw; score/assist and 1+ SOT can use the global base when source/minutes flags are cleared.",
        "- Current high-threshold player SOT rows have no settled no-field holdout support, so they stay raw unless an exact prop anchor is available.",
        "- Source-unvalidated rows (`PRIOR_ONLY` or `TEAM_UNKNOWN`) should not receive the global base until exact prop odds or structured player research clears the source flag.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sports-predict", action="store_true", help="GET-only settlement for older V1 history rows")
    ap.add_argument("--min-train", type=int, default=30)
    ap.add_argument("--bootstrap", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=61)
    ap.add_argument("--write", action="store_true", help=f"write {OUT}")
    args = ap.parse_args()

    rows = load_player_rows(sports_predict=args.sports_predict)
    comparisons, meta, bucket_rows = build_historical_comparisons(
        rows,
        min_train=args.min_train,
        trials=args.bootstrap,
        seed=args.seed,
    )
    context, manual_policy_counts = current_player_context()
    text = render(
        rows,
        comparisons=comparisons,
        meta=meta,
        bucket_rows=bucket_rows,
        context=context,
        manual_policy_counts=manual_policy_counts,
        sports_predict=args.sports_predict,
        trials=args.bootstrap,
        seed=args.seed,
    )
    print(text)
    if args.write:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(text, encoding="utf-8")
        print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
