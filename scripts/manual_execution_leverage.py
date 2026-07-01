"""Rank live manual-entry work by historical execution leverage.

This report is read-only over existing logs. It takes the row-level evidence
coverage audit and annotates each open market with conservative historical
RBP-per-row context from the manual policy backtests. The output is a worklist,
not a probability model: it tells which unresolved gates are likely worth the
most attention before lock.

It does not call SportsPredict, does not read `/predictions`, and never
submits.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from questions.parser import parse  # noqa: E402
from questions.teams import ALL_TEAM_NAMES  # noqa: E402


LOG_DIR = ROOT / "data" / "logs"
COVERAGE_LOG = LOG_DIR / "manual_evidence_coverage.md"
POLICY_LOG = LOG_DIR / "manual_policy_backtest.md"
OUT = LOG_DIR / "manual_execution_leverage.md"


@dataclass(frozen=True)
class CoverageRow:
    kickoff_local: str
    match: str
    entry: str
    risk: str
    evidence_status: str
    coverage_state: str
    evidence_area: str
    question: str
    gate: str


def _clean(value: str) -> str:
    return value.strip().strip("`")


def _float(value: str | None, default: float = 0.0) -> float:
    if value is None:
        return default
    value = value.strip().replace(",", "")
    if value in {"", "-"}:
        return default
    return float(value)


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
        if len(parts) == len(headers):
            rows.append(dict(zip(headers, parts)))
    return rows


def load_coverage_rows(path: Path = COVERAGE_LOG) -> list[CoverageRow]:
    table = _table_after(path.read_text(encoding="utf-8"), "Markets")
    out = []
    for row in table:
        out.append(
            CoverageRow(
                kickoff_local=row["kickoff_local"],
                match=row["match"],
                entry=row["entry"],
                risk=row["risk"],
                evidence_status=row["evidence_status"],
                coverage_state=row["coverage_state"],
                evidence_area=row["evidence_area"],
                question=row["question"],
                gate=row["gate"],
            )
        )
    return out


def category_per_row_lift(path: Path = POLICY_LOG) -> dict[str, float]:
    """Historical combined-policy RBP lift per settled row by broad category."""

    table = _table_after(path.read_text(encoding="utf-8"), "Category Contribution")
    out = {}
    for row in table:
        n = _float(row.get("n"))
        if n <= 0:
            continue
        out[row["category"]] = _float(row.get("combined_delta")) / n
    return out


def row_category(row: CoverageRow) -> str:
    spec = parse(row.question, teams=ALL_TEAM_NAMES)
    if spec.player:
        return "player"
    if row.evidence_area == "penalty_red":
        return "penalty_red"
    if row.evidence_area == "special_markets":
        return "special_proxy"
    if row.evidence_area == "odds_goal_rows":
        return "odds_goal"
    if row.evidence_area == "exact_polymarket_anchors":
        if spec.player:
            return "player"
        if spec.stat in {"cards", "corners", "sot", "offsides", "fouls"}:
            return f"count_{spec.stat}"
        return "odds_goal"
    if spec.stat in {"cards", "corners", "sot", "offsides", "fouls"}:
        return f"count_{spec.stat}"
    return row.evidence_area


def priority_band(row: CoverageRow, lift_per_row: float | None) -> str:
    if row.coverage_state == "PLAYER_RESEARCH_REQUIRED":
        return "HIGH"
    if row.coverage_state == "SPECIAL_COUNT_RESEARCH_REQUIRED":
        return "HIGH"
    if row.coverage_state == "FIELD_UNAVAILABLE_PRELOCK" and row_category(row) == "count_cards":
        return "MONITOR"
    if row.coverage_state in {"PLAYER_RECHECK", "SPECIAL_COUNT_RECHECK"}:
        return "MEDIUM"
    if row.coverage_state in {"FIELD_UNAVAILABLE_PRELOCK", "PROXY_ONLY", "GUARDRAIL_ONLY", "CURRENT_ANCHOR", "CURRENT_ANCHOR_RECHECK"}:
        return "MONITOR"
    if lift_per_row is not None and lift_per_row >= 1.0:
        return "MEDIUM"
    return "LOW"


def leverage_score(row: CoverageRow, lift_per_row: float | None) -> float:
    base = max(0.0, lift_per_row or 0.0)
    state_weight = {
        "PLAYER_RESEARCH_REQUIRED": 1.25,
        "SPECIAL_COUNT_RESEARCH_REQUIRED": 0.80,
        "SPECIAL_COUNT_RECHECK": 0.45,
        "FIELD_UNAVAILABLE_PRELOCK": 0.15,
        "PLAYER_RECHECK": 0.70,
        "CURRENT_ANCHOR": 0.35,
        "CURRENT_ANCHOR_RECHECK": 0.35,
        "PROXY_ONLY": 0.20,
        "GUARDRAIL_ONLY": 0.15,
        "COVERED": 0.00,
    }.get(row.coverage_state, 0.25)
    risk_boost = {"HIGH": 0.50, "MEDIUM": 0.20, "LOW": 0.0}.get(row.risk, 0.0)
    if lift_per_row is None:
        return risk_boost
    return base * state_weight + risk_boost


def build_leverage(rows: list[CoverageRow], lifts: dict[str, float]) -> list[dict]:
    out = []
    for row in rows:
        category = row_category(row)
        lift = None if category == "special_proxy" else lifts.get(category)
        out.append(
            {
                "row": row,
                "category": category,
                "lift_per_row": lift,
                "priority": priority_band(row, lift),
                "score": leverage_score(row, lift),
            }
        )
    return sorted(
        out,
        key=lambda item: (
            {"HIGH": 0, "MEDIUM": 1, "MONITOR": 2, "LOW": 3}.get(item["priority"], 9),
            -item["score"],
            item["row"].kickoff_local,
            item["row"].match,
            item["row"].question,
        ),
    )


def _fmt_lift(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.2f}"


def render(items: list[dict], *, now: datetime) -> str:
    priority_counts = Counter(item["priority"] for item in items)
    state_counts = Counter(item["row"].coverage_state for item in items)
    category_counts = Counter(item["category"] for item in items)
    lines = [
        "# Manual Execution Leverage Audit",
        "",
        f"- run_utc: {now.astimezone(timezone.utc).isoformat(timespec='seconds')}",
        "- bot_predictions: not_read",
        "- sports_predict: not_called",
        "- source_coverage_log: data/logs/manual_evidence_coverage.md",
        "- source_policy_log: data/logs/manual_policy_backtest.md",
        "- purpose: prioritize live manual-entry research/execution work from historical RBP leverage",
        "- interpretation: scores rank work; they are not expected-value forecasts and do not change probabilities",
        "",
        "## Summary",
        "",
        "| priority | markets |",
        "| --- | ---: |",
    ]
    for priority in ("HIGH", "MEDIUM", "MONITOR", "LOW"):
        lines.append(f"| {priority} | {priority_counts.get(priority, 0)} |")
    lines.extend(["", "| coverage_state | markets |", "| --- | ---: |"])
    for state, count in sorted(state_counts.items()):
        lines.append(f"| {state} | {count} |")
    lines.extend(["", "| category | markets |", "| --- | ---: |"])
    for category, count in sorted(category_counts.items()):
        lines.append(f"| {category} | {count} |")

    lines.extend(
        [
            "",
            "## Worklist",
            "",
            "| priority | score | hist_lift_per_row | category | coverage_state | match | kickoff_local | entry | question | gate |",
            "| --- | ---: | ---: | --- | --- | --- | --- | ---: | --- | --- |",
        ]
    )
    for item in items:
        row: CoverageRow = item["row"]
        if item["priority"] == "LOW":
            continue
        lines.append(
            f"| {item['priority']} | {item['score']:.2f} | {_fmt_lift(item['lift_per_row'])} | "
            f"{item['category']} | {row.coverage_state} | {row.match} | {row.kickoff_local} | "
            f"{row.entry} | {row.question} | {row.gate} |"
        )

    by_match: dict[str, list[dict]] = defaultdict(list)
    for item in items:
        if item["priority"] in {"HIGH", "MEDIUM"}:
            by_match[item["row"].match].append(item)
    lines.extend(["", "## Match Checklist", ""])
    for match, match_items in by_match.items():
        lines.extend([f"### {match}", ""])
        for item in match_items:
            row: CoverageRow = item["row"]
            lines.append(
                f"- [{item['priority']}] {row.coverage_state} {item['category']} "
                f"(hist_lift_per_row={_fmt_lift(item['lift_per_row'])}, score={item['score']:.2f}): "
                f"{row.gate} -- {row.question}"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--coverage-log", default=str(COVERAGE_LOG))
    ap.add_argument("--policy-log", default=str(POLICY_LOG))
    ap.add_argument("--write", action="store_true", help=f"write {OUT}")
    args = ap.parse_args()

    rows = load_coverage_rows(Path(args.coverage_log))
    lifts = category_per_row_lift(Path(args.policy_log))
    items = build_leverage(rows, lifts)
    text = render(items, now=datetime.now(timezone.utc))
    print(text)
    if args.write:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(text, encoding="utf-8")
        print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
