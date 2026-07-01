"""Audit boundaries for special-market proxy priors.

This read-only report ties together the live special-market audit, StatsBomb
proxy backtest, shrinkage rejection, and sparse official SportsPredict special
outcome audit. It exists to prevent the operational mistake of treating raw
special priors as officially validated edges or applying automatic shrinkage /
current-tournament trend adjustments without promotion evidence.

It does not call SportsPredict, does not read `/predictions`, and does not
submit anything.
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
OUT = LOG_DIR / "special_boundary_audit.md"


@dataclass(frozen=True)
class SpecialBoundary:
    current_special_rows: int
    weak_prior_rows: int
    count_threshold_proxy_rows: int
    count_threshold_high_heterogeneity_rows: int
    count_threshold_scaled_far_rows: int
    high_heterogeneity_rows: int
    max_scaled_shift_pp: int
    official_settled_special_rows: int
    official_usable_special_rows: int
    shrinkage_decision: str
    weak_shrink_ci_2_5: float
    weak_shrink_pr_gt_0: float
    knockout_adaptation_decision: str
    knockout_adaptation_ci_97_5: float
    decision: str


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


def _table_after(text: str, heading: str) -> list[dict[str, str]]:
    tables = _tables_after(text, heading)
    return tables[0] if tables else []


def _table_with_headers(text: str, heading: str, required_headers: set[str]) -> list[dict[str, str]]:
    for table in _tables_after(text, heading):
        if table and required_headers.issubset(table[0].keys()):
            return table
    return []


def _row(rows: list[dict[str, str]], key: str, value: str) -> dict[str, str]:
    for row in rows:
        if row.get(key) == value:
            return row
    return {}


def _int(value: str | None, default: int = 0) -> int:
    if value is None:
        return default
    value = value.strip().replace(",", "")
    if value in {"", "-"}:
        return default
    return int(float(value))


def _float(value: str | None, default: float = 0.0) -> float:
    if value is None:
        return default
    cleaned = value.strip().replace("%", "").replace(",", "")
    if cleaned in {"", "-"}:
        return default
    return float(cleaned)


def _counts(rows: list[dict[str, str]], label: str, value: str) -> dict[str, int]:
    return {row.get(label, ""): _int(row.get(value)) for row in rows}


def build_boundary(log_dir: Path = LOG_DIR) -> tuple[SpecialBoundary, list[dict[str, str]], list[dict[str, str]]]:
    live_text = _read(log_dir / "special_market_live_audit.md")
    backtest_text = _read(log_dir / "special_market_backtest.md")
    shrink_text = _read(log_dir / "special_prior_shrinkage_backtest.md")
    official_text = _read(log_dir / "special_official_results_audit.md")

    live_keys = _key_values(live_text)
    risk_counts = _counts(
        _table_with_headers(live_text, "Risk Summary", {"risk_state", "rows"}),
        "risk_state",
        "rows",
    )
    live_rows = _table_after(live_text, "Live Special Rows")
    max_scaled = max((_int(row.get("scaled_shift_pp")) for row in live_rows), default=0)
    high_heterogeneity_rows = sum(1 for row in live_rows if row.get("heterogeneity") == "high")

    official = _key_values(official_text)
    shrink = _key_values(shrink_text)
    shrink_boot = _row(_table_after(shrink_text, "Best-Factor Bootstrap"), "scope", "weak_near_coinflip_binary")
    adaptation = _row(_table_after(backtest_text, "Adaptation Bootstrap"), "scope", "knockout_only")

    weak_prior_rows = risk_counts.get("WEAK_PRIOR", 0)
    count_threshold_proxy = risk_counts.get("COUNT_THRESHOLD_PROXY", 0)
    count_threshold_high = risk_counts.get("COUNT_THRESHOLD_HIGH_HETEROGENEITY", 0)
    count_threshold_far = risk_counts.get("COUNT_THRESHOLD_SCALED_FAR_FROM_BASE", 0)
    official_rows = _int(official.get("settled_special_rows"))
    shrink_decision = shrink.get("promotion_decision", "missing")
    knockout_decision = adaptation.get("decision", "missing")

    if official_rows < 30 and shrink_decision != "promoted" and knockout_decision != "promoted":
        decision = "raw_proxy_with_guardrails_no_shrink_no_official_promotion"
    else:
        decision = "review_special_policy_before_manual_use"

    boundary = SpecialBoundary(
        current_special_rows=_int(live_keys.get("special_rows")),
        weak_prior_rows=weak_prior_rows,
        count_threshold_proxy_rows=count_threshold_proxy,
        count_threshold_high_heterogeneity_rows=count_threshold_high,
        count_threshold_scaled_far_rows=count_threshold_far,
        high_heterogeneity_rows=high_heterogeneity_rows,
        max_scaled_shift_pp=max_scaled,
        official_settled_special_rows=official_rows,
        official_usable_special_rows=_int(official.get("usable_special_rows")),
        shrinkage_decision=shrink_decision,
        weak_shrink_ci_2_5=_float(shrink_boot.get("ci_2.5", shrink.get("weak_ci_2.5"))),
        weak_shrink_pr_gt_0=_float(shrink_boot.get("pr_gt_0", shrink.get("weak_pr_gt_0"))),
        knockout_adaptation_decision=knockout_decision,
        knockout_adaptation_ci_97_5=_float(adaptation.get("ci_97.5")),
        decision=decision,
    )
    return boundary, live_rows, _table_after(official_text, "By Family")


def md_table(headers: list[str], rows: list[list[str]]) -> list[str]:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return lines


def render(
    boundary: SpecialBoundary,
    *,
    live_rows: list[dict[str, str]],
    official_family_rows: list[dict[str, str]],
    now: datetime | None = None,
) -> str:
    now = now or datetime.now(timezone.utc)
    risk_counts = Counter(row.get("risk_state", "") for row in live_rows)
    confidence_counts = Counter(row.get("confidence", "") for row in live_rows)
    lines = [
        "# Special Boundary Audit",
        "",
        "Read-only analysis. `/predictions` was not read and no submissions were made.",
        "",
        f"- run_utc: {now.astimezone(timezone.utc).isoformat(timespec='seconds')}",
        "- sports_predict: not_called",
        "- purpose: ensure special-market proxy priors are used only inside documented guardrails",
        f"- current_special_rows: {boundary.current_special_rows}",
        f"- weak_prior_rows: {boundary.weak_prior_rows}",
        f"- count_threshold_proxy_rows: {boundary.count_threshold_proxy_rows}",
        f"- count_threshold_high_heterogeneity_rows: {boundary.count_threshold_high_heterogeneity_rows}",
        f"- count_threshold_scaled_far_rows: {boundary.count_threshold_scaled_far_rows}",
        f"- high_heterogeneity_rows: {boundary.high_heterogeneity_rows}",
        f"- max_scaled_shift_pp: {boundary.max_scaled_shift_pp}",
        f"- official_settled_special_rows: {boundary.official_settled_special_rows}",
        f"- official_usable_special_rows: {boundary.official_usable_special_rows}",
        f"- shrinkage_decision: {boundary.shrinkage_decision}",
        f"- weak_shrink_ci_2.5: {boundary.weak_shrink_ci_2_5:.2f}",
        f"- weak_shrink_pr_gt_0: {boundary.weak_shrink_pr_gt_0:.3f}",
        f"- knockout_adaptation_decision: {boundary.knockout_adaptation_decision}",
        f"- knockout_adaptation_ci_97.5: {boundary.knockout_adaptation_ci_97_5:.4f}",
        f"- decision: {boundary.decision}",
        "",
        "## Boundary Summary",
        "",
        "| check | value | implication |",
        "| --- | ---: | --- |",
        f"| official_settled_special_rows | {boundary.official_settled_special_rows} | below historical-promotion sample; do not claim official validation |",
        f"| weak_prior_rows | {boundary.weak_prior_rows} | compare current source before treating as edge |",
        f"| count_threshold_high_or_far_rows | {boundary.count_threshold_high_heterogeneity_rows + boundary.count_threshold_scaled_far_rows} | recheck count-volume inputs before lock |",
        f"| max_scaled_shift_pp | {boundary.max_scaled_shift_pp} | largest live model-vs-base proxy movement needing source review |",
        f"| shrinkage_decision | {boundary.shrinkage_decision} | automatic shrinkage remains unpromoted |",
        f"| knockout_adaptation_decision | {boundary.knockout_adaptation_decision} | current-tournament trend adjustment remains unpromoted |",
        "",
        "## Live Risk Counts",
        "",
    ]
    lines += md_table(
        ["risk_state", "rows"],
        [[key or "missing", str(value)] for key, value in sorted(risk_counts.items())],
    )
    lines += ["", "## Live Confidence Counts", ""]
    lines += md_table(
        ["confidence", "rows"],
        [[key or "missing", str(value)] for key, value in sorted(confidence_counts.items())],
    )
    lines += [
        "",
        "## Highest-Risk Live Special Rows",
        "",
    ]
    risky_states = {
        "WEAK_PRIOR",
        "COUNT_THRESHOLD_HIGH_HETEROGENEITY",
        "COUNT_THRESHOLD_SCALED_FAR_FROM_BASE",
        "HIGH_HETEROGENEITY_PROXY",
        "SCALED_FAR_FROM_BASE",
        "UNMAPPED_PROXY",
    }
    risky = [row for row in live_rows if row.get("risk_state") in risky_states]
    lines += md_table(
        ["match", "entry", "risk_state", "evidence_key", "scaled_shift_pp", "action", "question"],
        [
            [
                row.get("match", ""),
                row.get("entry", ""),
                row.get("risk_state", ""),
                row.get("evidence_key", ""),
                row.get("scaled_shift_pp", ""),
                row.get("action", ""),
                row.get("question", ""),
            ]
            for row in risky
        ],
    )
    lines += [
        "",
        "## Official Special Families",
        "",
    ]
    lines += md_table(
        ["family", "n", "successes", "prior_mean", "edge_vs_50"],
        [
            [
                row.get("family", ""),
                row.get("n", ""),
                row.get("successes", ""),
                row.get("prior_mean", ""),
                row.get("edge_vs_50", ""),
            ]
            for row in official_family_rows
        ],
    )
    lines += [
        "",
        "## Interpretation",
        "",
        "- Special probabilities are proxy priors, not official SportsPredict-validated strategy promotions.",
        "- Raw proxy priors remain the default because automatic shrinkage and in-tournament adaptation are not promoted.",
        "- Weak near-coinflip rows and count-threshold rows with high heterogeneity or large scaling shifts are research/source checks before lock, not probability transforms.",
        "- Do not use field-relative or catch-up extremizing transforms on special markets without a new audit that clears the promotion gate.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write", action="store_true", help=f"write {OUT}")
    args = ap.parse_args()

    boundary, live_rows, official_family_rows = build_boundary()
    text = render(boundary, live_rows=live_rows, official_family_rows=official_family_rows)
    print(text)
    if args.write:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(text, encoding="utf-8")
        print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
