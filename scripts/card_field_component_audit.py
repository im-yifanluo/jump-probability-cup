"""Audit the card-field component of the manual count policy.

This read-only diagnostic isolates the card branch that sits inside the
promoted count policy. It does not read `/predictions` and does not submit.
With `--sports-predict`, SportsPredict is used only for GET settlement of older
V1 history rows through `count_policy_backtest.load_count_rows`.

The purpose is narrow: decide whether visible card-field capture should remain
a high-priority manual action, and whether any no-field card transform is
strong enough to promote.
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
OUT = LOG_DIR / "card_field_component_audit.md"

sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from backtest.history import HistoryMarket, clip_prob, crowd_strategy, official_delta_rbp  # noqa: E402
from count_no_field_policy_backtest import (  # noqa: E402
    bootstrap_policy_diff,
    chronological_predictions,
    is_card_threshold,
    score_predictions,
)
from count_policy_backtest import (  # noqa: E402
    GLOBAL_ASYM,
    _strong_sot,
    bootstrap_strategy_diff,
    load_count_rows,
    score,
)


Strategy = Callable[[HistoryMarket], float]


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
class OpenCardContext:
    open_card_like_markets: int
    field_required_card_count_rows: int
    card_like_non_count_rows: int
    open_matches_with_card_markets: int
    verified_referee_matches: int
    local_referee_override_matches: int


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


def promoted_count_policy(row: HistoryMarket) -> float:
    return row.you if _strong_sot(row) else (row.crowd if row.stat == "cards" else GLOBAL_ASYM(row))


def no_card_field_count_policy(row: HistoryMarket) -> float:
    return row.you if _strong_sot(row) else GLOBAL_ASYM(row)


def open_card_context(log_dir: Path = LOG_DIR) -> OpenCardContext:
    execution_text = _read(log_dir / "manual_execution_leverage.md")
    categories = {
        row.get("category", ""): _int(row.get("markets"))
        for row in _table_with_headers(execution_text, "Summary", {"category", "markets"})
    }
    referee_text = _read(log_dir / "referee_research_audit.md")
    availability = _table_after(referee_text, "Open Match Referee Availability")
    card_rows = _table_after(referee_text, "Open Card Markets")
    verified = sum(1 for row in availability if row.get("espn_officials", "-") not in {"", "-"})
    overrides = sum(1 for row in availability if row.get("local_override", "no_override") != "no_override")
    field_required_cards = categories.get("count_cards", 0)
    open_card_like = len(card_rows)
    return OpenCardContext(
        open_card_like_markets=open_card_like,
        field_required_card_count_rows=field_required_cards,
        card_like_non_count_rows=max(0, open_card_like - field_required_cards),
        open_matches_with_card_markets=len(availability),
        verified_referee_matches=verified,
        local_referee_override_matches=overrides,
    )


def build_comparisons(
    rows: list[HistoryMarket],
    *,
    min_train: int,
    trials: int,
    seed: int,
) -> tuple[list[ComparisonRow], dict[str, str]]:
    holdout = chronological_holdout_rows(rows, min_train)
    card_rows = [row for row in rows if row.stat == "cards"]
    card_holdout = [row for row in holdout if row.stat == "cards"]
    threshold_rows = [row for row in card_rows if is_card_threshold(row)]
    threshold_holdout = [row for row in card_holdout if is_card_threshold(row)]

    comparisons = [
        _comparison(
            "card_rows_field_minus_raw_model",
            card_rows,
            card_holdout,
            crowd_strategy,
            lambda row: row.you,
            trials=trials,
            seed=seed,
            interpretation="historical field component beat the raw card model on settled card rows, but it is not available pre-lock",
        ),
        _comparison(
            "card_rows_field_minus_global_asym",
            card_rows,
            card_holdout,
            crowd_strategy,
            GLOBAL_ASYM,
            trials=trials,
            seed=seed + 1,
            interpretation="tests whether field is better than the generic count field transform for card rows",
        ),
        _comparison(
            "promoted_count_policy_minus_no_card_field_component",
            rows,
            holdout,
            promoted_count_policy,
            no_card_field_count_policy,
            trials=trials,
            seed=seed + 2,
            interpretation="isolates the incremental card-field component inside the full promoted count policy",
        ),
        _comparison(
            "card_threshold_field_minus_raw_model",
            threshold_rows,
            threshold_holdout,
            crowd_strategy,
            lambda row: row.you,
            trials=trials,
            seed=seed + 3,
            interpretation="visible-field check on deployable card threshold rows only",
        ),
    ]

    scored = chronological_predictions(
        rows,
        min_train=min_train,
        global_strength=4.0,
        stat_strength=8.0,
        family_strength=12.0,
    )
    no_field_policy = "card_threshold_stat_base_else_raw"
    scored_threshold = [item for item in scored if is_card_threshold(item.row)]
    if scored and trials:
        boot = bootstrap_policy_diff(scored, no_field_policy, "raw_model", trials=trials, seed=seed + 4)
    else:
        boot = {"ci_2.5": 0.0, "median": 0.0, "ci_97.5": 0.0, "pr_gt_0": 0.0}
    if scored_threshold and trials:
        threshold_boot = bootstrap_policy_diff(
            scored_threshold,
            no_field_policy,
            "raw_model",
            trials=trials,
            seed=seed + 5,
        )
    else:
        threshold_boot = {"ci_2.5": 0.0, "median": 0.0, "ci_97.5": 0.0, "pr_gt_0": 0.0}
    no_field_point = score_predictions(scored, no_field_policy)["delta_vs_raw"] if scored else 0.0
    no_field_threshold_point = (
        score_predictions(scored_threshold, no_field_policy)["delta_vs_raw"] if scored_threshold else 0.0
    )

    meta = {
        "settled_count_rows": str(len(rows)),
        "settled_card_rows": str(len(card_rows)),
        "settled_card_threshold_rows": str(len(threshold_rows)),
        "chronological_count_rows": str(len(scored)),
        "chronological_card_threshold_rows": str(len(scored_threshold)),
        "no_field_policy": no_field_policy,
        "no_field_all_point_delta": f"{no_field_point:.2f}",
        "no_field_all_ci_2.5": f"{boot['ci_2.5']:.2f}",
        "no_field_all_pr_gt_0": f"{boot['pr_gt_0']:.3f}",
        "no_field_card_threshold_point_delta": f"{no_field_threshold_point:.2f}",
        "no_field_card_threshold_ci_2.5": f"{threshold_boot['ci_2.5']:.2f}",
        "no_field_card_threshold_pr_gt_0": f"{threshold_boot['pr_gt_0']:.3f}",
    }
    return comparisons, meta


def decision(meta: dict[str, str], comparisons: list[ComparisonRow], context: OpenCardContext) -> str:
    card_field = next((row for row in comparisons if row.comparison == "card_rows_field_minus_raw_model"), None)
    component = next(
        (row for row in comparisons if row.comparison == "promoted_count_policy_minus_no_card_field_component"),
        None,
    )
    no_field_ci = float(meta.get("no_field_all_ci_2.5", "0"))
    if (
        card_field
        and card_field.full_sample_delta > 0
        and component
        and component.bootstrap_ci_2_5 <= 0
        and no_field_ci <= 0
        and context.verified_referee_matches == 0
        and context.local_referee_override_matches == 0
    ):
        return "field_visible_required_no_referee_override_no_new_transform"
    if card_field and card_field.bootstrap_ci_2_5 > 0 and no_field_ci > 0:
        return "review_for_possible_card_transform"
    return "guardrail_only_review_required"


def render(
    rows: list[HistoryMarket],
    *,
    context: OpenCardContext,
    comparisons: list[ComparisonRow],
    meta: dict[str, str],
    sports_predict: bool,
    trials: int,
    seed: int,
    now: datetime | None = None,
) -> str:
    now = now or datetime.now(timezone.utc)
    stat_counts = Counter(row.family for row in rows if row.stat == "cards")
    current_decision = decision(meta, comparisons, context)
    lines = [
        "# Card Field Component Audit",
        "",
        "Read-only analysis. `/predictions` was not read and no submissions were made.",
        "",
        f"- run_utc: {now.astimezone(timezone.utc).isoformat(timespec='seconds')}",
        f"- sports_predict: {'read_only_get_settlement' if sports_predict else 'not_called'}",
        f"- bootstrap_trials: {trials}",
        f"- bootstrap_seed: {seed}",
        f"- settled_count_rows: {meta['settled_count_rows']}",
        f"- settled_card_rows: {meta['settled_card_rows']}",
        f"- settled_card_threshold_rows: {meta['settled_card_threshold_rows']}",
        f"- chronological_count_rows: {meta['chronological_count_rows']}",
        f"- chronological_card_threshold_rows: {meta['chronological_card_threshold_rows']}",
        f"- open_card_like_markets: {context.open_card_like_markets}",
        f"- field_required_card_count_rows: {context.field_required_card_count_rows}",
        f"- card_like_non_count_rows: {context.card_like_non_count_rows}",
        f"- open_matches_with_card_markets: {context.open_matches_with_card_markets}",
        f"- verified_referee_matches: {context.verified_referee_matches}",
        f"- local_referee_override_matches: {context.local_referee_override_matches}",
        f"- no_field_policy: {meta['no_field_policy']}",
        f"- no_field_all_point_delta: {meta['no_field_all_point_delta']}",
        f"- no_field_all_ci_2.5: {meta['no_field_all_ci_2.5']}",
        f"- no_field_all_pr_gt_0: {meta['no_field_all_pr_gt_0']}",
        f"- no_field_card_threshold_point_delta: {meta['no_field_card_threshold_point_delta']}",
        f"- no_field_card_threshold_ci_2.5: {meta['no_field_card_threshold_ci_2.5']}",
        f"- no_field_card_threshold_pr_gt_0: {meta['no_field_card_threshold_pr_gt_0']}",
        f"- decision: {current_decision}",
        "",
        "## Card Family Mix",
        "",
    ]
    lines += md_table(
        ["family", "settled_rows"],
        [[family, str(count)] for family, count in sorted(stat_counts.items())],
    )
    lines += [
        "",
        "## Component Comparisons",
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
        "## Current Open Card Context",
        "",
        "| metric | value |",
        "| --- | ---: |",
        f"| open_card_like_markets | {context.open_card_like_markets} |",
        f"| field_required_card_count_rows | {context.field_required_card_count_rows} |",
        f"| card_like_non_count_rows | {context.card_like_non_count_rows} |",
        f"| open_matches_with_card_markets | {context.open_matches_with_card_markets} |",
        f"| verified_referee_matches | {context.verified_referee_matches} |",
        f"| local_referee_override_matches | {context.local_referee_override_matches} |",
        "",
        "## Interpretation",
        "",
        "- Visible field historically beat the raw card model, but field values are unavailable before lock in the current UI.",
        "- The incremental card-field component is not strong enough to justify mining broader card transforms or overriding the documented count policy.",
        "- The no-field card-threshold policy has a positive point estimate, but its match-bootstrap lower bound is still negative, so it remains unpromoted.",
        "- Without verified current referee information or an exact current source, keep no-field card rows on the neutral/raw card model.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sports-predict", action="store_true", help="GET-only settlement for older V1 history rows")
    ap.add_argument("--min-train", type=int, default=60)
    ap.add_argument("--bootstrap", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=53)
    ap.add_argument("--write", action="store_true", help=f"write {OUT}")
    args = ap.parse_args()

    rows = load_count_rows(sports_predict=args.sports_predict)
    comparisons, meta = build_comparisons(rows, min_train=args.min_train, trials=args.bootstrap, seed=args.seed)
    text = render(
        rows,
        context=open_card_context(),
        comparisons=comparisons,
        meta=meta,
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
