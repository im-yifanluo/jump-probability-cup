"""Question-family strategy coverage audit for the manual workflow.

This is a local, read-only audit. It does not call SportsPredict, does not read
`/predictions`, and never submits. It answers a broader question than the live
market coverage report: every observed question family in history and the
latest open-market coverage log should have a documented manual strategy owner
and an explicit evidence status.
"""

from __future__ import annotations

import argparse
import ast
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from backtest.history import HistoryMarket, exact_rows, load_history  # noqa: E402
from manual.policy import is_count_market, is_odds_goal_market, is_special_market  # noqa: E402
from questions.parser import MarketSpec, parse  # noqa: E402
from questions.teams import ALL_TEAM_NAMES  # noqa: E402
from strategy_evidence_matrix import build_matrix  # noqa: E402


OUT = ROOT / "data" / "logs" / "question_family_coverage_audit.md"
HISTORY = ROOT / "docs" / "history.md"
OPEN_COVERAGE = ROOT / "data" / "logs" / "manual_evidence_coverage.md"
PARSER = ROOT / "src" / "questions" / "parser.py"


def _clean(value: str) -> str:
    return value.strip().strip("`")


def _table_after(text: str, heading: str) -> list[dict[str, str]]:
    lines = text.splitlines()
    start = None
    for idx, line in enumerate(lines):
        if line.strip() == f"## {heading}":
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


@dataclass(frozen=True)
class FamilyRecord:
    family_key: str
    family: str
    stat: str
    period: str
    strategy_area: str
    strategy_status: str
    history_rows: int = 0
    safe_exact_rows: int = 0
    open_rows: int = 0
    example_question: str = ""


def famkey(spec: MarketSpec) -> str:
    out = spec.family
    if spec.stat:
        out += f"/{spec.stat}"
    if spec.period and spec.period != "match":
        out += f"/{spec.period}"
    return out


def strategy_statuses(log_dir: Path = ROOT / "data" / "logs") -> dict[str, str]:
    return {row.area: row.status for row in build_matrix(log_dir)}


def strategy_area_for_spec(spec: MarketSpec) -> str:
    """Map a parsed question to its base manual strategy branch.

    This intentionally ignores opportunistic exact Polymarket anchors. Those
    are current-source overlays, not the underlying question-family owner.
    """

    if spec.player or spec.family.startswith("player_"):
        return "researched_player_props"
    if spec.family in {"penalty_awarded", "penalty_or_red", "red_card"}:
        return "penalty_red"
    if is_special_market(spec):
        return "special_markets"
    if is_odds_goal_market(spec):
        return "odds_goal_rows"
    if is_count_market(spec):
        return "count_field_policy"
    return "combined_manual_policy"


def support_class(status: str) -> str:
    if status == "PROMOTED":
        return "promoted"
    if status == "PROMOTED_LIMITED":
        return "limited"
    if status in {"CURRENT_SOURCE_RULE", "PROXY_ONLY", "GUARDRAIL_ONLY"}:
        return "covered_not_promoted"
    if status in {"NOT_PROMOTED", "REJECTED"}:
        return "not_promoted"
    return "unknown"


def live_rule(area: str, status: str) -> tuple[str, str]:
    if area == "odds_goal_rows":
        return (
            "Use current odds/Polymarket anchors and one coherent score grid.",
            "Refresh odds/source audit near lock; do not add unpromoted de-vig transforms.",
        )
    if area == "count_field_policy":
        return (
            "Use the promoted count policy only when its live inputs exist; without field use raw/count-source rules.",
            "Field is unavailable before lock; no broad no-field count fallback is promoted.",
        )
    if area == "researched_player_props":
        return (
            "Use exact current prop odds, confirmed lineup/minutes, then limited player policy.",
            "Player rows remain lineup/prop led; no-field support is bounded.",
        )
    if area == "penalty_red":
        return (
            "Use sparse base-rate guardrail and avoid pushing above the low-event base without direct evidence.",
            "No referee-specific penalty/red transform is promoted.",
        )
    if area == "special_markets":
        return (
            "Use raw special proxy priors with explicit weak/count-threshold guardrails.",
            "Needs official SportsPredict settlement or independent current sources for promotion.",
        )
    if area == "combined_manual_policy":
        return (
            "Use the no-field current-source/model value before lock; apply field-relative policy only for historical replay or a future UI behavior change.",
            "Fallback branch only after the question family is not captured by a more specific branch; field is unavailable before lock.",
        )
    return (
        f"Strategy area `{area}` has status `{status}`.",
        "Check the strategy matrix before using this branch live.",
    )


def records_from_history(rows: list[HistoryMarket], statuses: dict[str, str]) -> dict[str, FamilyRecord]:
    safe_keys = Counter(famkey_from_history(row) for row in exact_rows(rows))
    all_keys = Counter(famkey_from_history(row) for row in rows)
    examples: dict[str, str] = {}
    specs: dict[str, MarketSpec] = {}
    for row in rows:
        spec = MarketSpec(row.family, stat=row.stat or None, period=row.period)
        key = famkey(spec)
        examples.setdefault(key, row.question)
        specs.setdefault(key, spec)
    return {
        key: _record_for_spec(
            specs[key],
            statuses,
            history_rows=count,
            safe_exact_rows=safe_keys.get(key, 0),
            example_question=examples.get(key, ""),
        )
        for key, count in all_keys.items()
    }


def famkey_from_history(row: HistoryMarket) -> str:
    spec = MarketSpec(row.family, stat=row.stat or None, period=row.period)
    return famkey(spec)


def open_questions(path: Path = OPEN_COVERAGE) -> list[str]:
    if not path.exists():
        return []
    rows = _table_after(path.read_text(encoding="utf-8"), "Markets")
    return [row["question"] for row in rows if row.get("question")]


def records_from_open_questions(questions: list[str], statuses: dict[str, str]) -> dict[str, FamilyRecord]:
    counts: Counter[str] = Counter()
    specs: dict[str, MarketSpec] = {}
    examples: dict[str, str] = {}
    for question in questions:
        spec = parse(question, teams=ALL_TEAM_NAMES)
        key = famkey(spec)
        counts[key] += 1
        specs.setdefault(key, spec)
        examples.setdefault(key, question)
    return {
        key: _record_for_spec(
            specs[key],
            statuses,
            open_rows=count,
            example_question=examples.get(key, ""),
        )
        for key, count in counts.items()
    }


def parser_family_names(path: Path = PARSER) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func_name = getattr(node.func, "id", getattr(node.func, "attr", ""))
        if func_name != "MarketSpec" or not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            names.add(first.value)
    return names


def parser_family_area_status(statuses: dict[str, str]) -> list[dict[str, str]]:
    rows = []
    for family in sorted(parser_family_names()):
        # Family-only specs are enough for ownership except stat-dependent count
        # families. Mark those as stat-dependent so the observed-family table
        # remains the authority for exact count/special routing.
        if family in {"total_count_ge", "total_count_before_hydration"}:
            area = "stat_dependent_count_or_special"
            status = "SEE_OBSERVED_FAMILY_KEYS"
        elif family in {"team_count_ge", "both_teams_count_ge", "team_more_count_than"}:
            area = "count_field_policy"
            status = statuses.get(area, "UNKNOWN")
        else:
            area = strategy_area_for_spec(MarketSpec(family))
            status = statuses.get(area, "UNKNOWN")
        rows.append({"family": family, "strategy_area": area, "strategy_status": status})
    return rows


def _record_for_spec(
    spec: MarketSpec,
    statuses: dict[str, str],
    *,
    history_rows: int = 0,
    safe_exact_rows: int = 0,
    open_rows: int = 0,
    example_question: str = "",
) -> FamilyRecord:
    area = strategy_area_for_spec(spec)
    return FamilyRecord(
        family_key=famkey(spec),
        family=spec.family,
        stat=spec.stat or "",
        period=spec.period or "match",
        strategy_area=area,
        strategy_status=statuses.get(area, "UNKNOWN"),
        history_rows=history_rows,
        safe_exact_rows=safe_exact_rows,
        open_rows=open_rows,
        example_question=example_question,
    )


def merge_records(*parts: dict[str, FamilyRecord]) -> list[FamilyRecord]:
    merged: dict[str, FamilyRecord] = {}
    for part in parts:
        for key, record in part.items():
            prev = merged.get(key)
            if prev is None:
                merged[key] = record
                continue
            merged[key] = FamilyRecord(
                family_key=key,
                family=record.family,
                stat=record.stat,
                period=record.period,
                strategy_area=record.strategy_area,
                strategy_status=record.strategy_status,
                history_rows=prev.history_rows + record.history_rows,
                safe_exact_rows=prev.safe_exact_rows + record.safe_exact_rows,
                open_rows=prev.open_rows + record.open_rows,
                example_question=prev.example_question or record.example_question,
            )
    return sorted(merged.values(), key=lambda r: (r.strategy_area, r.family_key))


def coverage_status(records: list[FamilyRecord], parser_rows: list[dict[str, str]]) -> str:
    if any(row.strategy_status == "UNKNOWN" for row in records):
        return "INCOMPLETE_UNKNOWN_OBSERVED_FAMILY"
    parser_unknown = [
        row
        for row in parser_rows
        if row["strategy_status"] == "UNKNOWN" and row["strategy_area"] != "stat_dependent_count_or_special"
    ]
    if parser_unknown:
        return "INCOMPLETE_UNKNOWN_PARSER_FAMILY"
    if any(support_class(row.strategy_status) != "promoted" for row in records):
        return "COMPLETE_WITH_LIMITED_PROXY_OR_GUARDRAIL_ROWS"
    return "COMPLETE_PROMOTED_OBSERVED_FAMILIES"


def render(records: list[FamilyRecord], parser_rows: list[dict[str, str]], *, now: datetime) -> str:
    status_counts = Counter(row.strategy_status for row in records)
    area_counts = Counter(row.strategy_area for row in records)
    support_counts = Counter(support_class(row.strategy_status) for row in records)
    status = coverage_status(records, parser_rows)
    non_promoted = [row for row in records if support_class(row.strategy_status) != "promoted"]

    lines = [
        "# Question Family Strategy Coverage Audit",
        "",
        "Local read-only analysis. `/predictions` was not read and no submissions were made.",
        "",
        f"- run_utc: {now.astimezone(timezone.utc).isoformat(timespec='seconds')}",
        f"- question_family_coverage_status: {status}",
        f"- observed_family_keys: {len(records)}",
        f"- parser_family_names: {len(parser_rows)}",
        f"- non_promoted_observed_family_keys: {len(non_promoted)}",
        "",
        "## Summary",
        "",
        "| strategy_status | family_keys |",
        "| --- | ---: |",
    ]
    for key, count in sorted(status_counts.items()):
        lines.append(f"| {key} | {count} |")
    lines.extend(["", "| support_class | family_keys |", "| --- | ---: |"])
    for key, count in sorted(support_counts.items()):
        lines.append(f"| {key} | {count} |")
    lines.extend(["", "| strategy_area | family_keys |", "| --- | ---: |"])
    for key, count in sorted(area_counts.items()):
        lines.append(f"| {key} | {count} |")

    lines.extend(
        [
            "",
            "## Observed Family Keys",
            "",
            "| family_key | strategy_area | status | history_rows | safe_exact_rows | open_rows | live_rule | remaining_gate | example |",
            "| --- | --- | --- | ---: | ---: | ---: | --- | --- | --- |",
        ]
    )
    for row in records:
        rule, gate = live_rule(row.strategy_area, row.strategy_status)
        lines.append(
            f"| {row.family_key} | {row.strategy_area} | {row.strategy_status} | "
            f"{row.history_rows} | {row.safe_exact_rows} | {row.open_rows} | "
            f"{rule} | {gate} | {row.example_question} |"
        )

    lines.extend(
        [
            "",
            "## Parser Family Owners",
            "",
            "| parser_family | strategy_area | status |",
            "| --- | --- | --- |",
        ]
    )
    for row in parser_rows:
        lines.append(f"| {row['family']} | {row['strategy_area']} | {row['strategy_status']} |")

    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- `COMPLETE_WITH_LIMITED_PROXY_OR_GUARDRAIL_ROWS` means every observed family has a strategy owner, but not every owner is a promoted historical edge.",
            "- `PROXY_ONLY`, `GUARDRAIL_ONLY`, `CURRENT_SOURCE_RULE`, and `PROMOTED_LIMITED` rows are deliberately not upgraded to full promotions.",
            "- Stat-dependent parser families such as `total_count_ge` are routed by observed `family_key` because shots/hydration counts are special markets while cards/corners/SOT/offside counts are ordinary count markets.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--history", default=str(HISTORY))
    ap.add_argument("--open-coverage", default=str(OPEN_COVERAGE))
    ap.add_argument("--write", action="store_true", help=f"write {OUT}")
    args = ap.parse_args()

    statuses = strategy_statuses()
    history_records = records_from_history(load_history(args.history), statuses)
    open_records = records_from_open_questions(open_questions(Path(args.open_coverage)), statuses)
    records = merge_records(history_records, open_records)
    parser_rows = parser_family_area_status(statuses)
    text = render(records, parser_rows, now=datetime.now(timezone.utc))
    print(text)
    if args.write:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(text, encoding="utf-8")
        print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
