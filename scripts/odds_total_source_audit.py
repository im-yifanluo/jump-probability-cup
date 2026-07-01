"""Audit current total-goals source quality for manual odds-goal rows.

This report is read-only. It does not call SportsPredict, does not read
`/predictions`, and does not submit anything. It reconciles the live manual
coverage report with exact Polymarket direct anchors for match/team total-goal
questions, so missing or thin exact total anchors are visible before manual
entry without creating a new probability transform.
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
COVERAGE = LOG_DIR / "manual_evidence_coverage.md"
DIRECT = LOG_DIR / "polymarket_direct_coverage_audit.md"
ODDS = LOG_DIR / "odds_anchor_audit.md"
OUT = LOG_DIR / "odds_total_source_audit.md"

sys.path.insert(0, str(ROOT / "src"))

from questions.parser import MATCH, MarketSpec, parse  # noqa: E402
from questions.teams import ALL_TEAM_NAMES  # noqa: E402


TOTAL_GOAL_FAMILIES = {"total_goals", "team_goals_ge"}


@dataclass(frozen=True)
class CoverageTotalRow:
    kickoff_local: str
    match: str
    entry: int
    evidence_area: str
    question: str
    spec: MarketSpec


@dataclass(frozen=True)
class DirectAnchorRow:
    status: str
    match: str
    kickoff_utc: str
    model: int
    anchor: int
    delta_pp: int
    volume: float
    question: str
    polymarket_question: str


@dataclass(frozen=True)
class AuditRow:
    total: CoverageTotalRow
    direct: DirectAnchorRow | None
    source_state: str
    action: str
    book_total_goals: str
    odds_source_state: str


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
    cleaned = value.strip().replace("%", "").replace("+", "").replace(",", "")
    if cleaned in {"", "-"}:
        return default
    return int(float(cleaned))


def _float(value: str | None, default: float = 0.0) -> float:
    if value is None:
        return default
    cleaned = value.strip().replace("$", "").replace(",", "")
    if cleaned in {"", "-"}:
        return default
    return float(cleaned)


def is_total_goal_spec(spec: MarketSpec) -> bool:
    return spec.family in TOTAL_GOAL_FAMILIES and spec.stat == "goals"


def _period_label(spec: MarketSpec) -> str:
    if spec.period == MATCH:
        return "match"
    return spec.period


def read_coverage_total_rows(path: Path = COVERAGE) -> list[CoverageTotalRow]:
    rows: list[CoverageTotalRow] = []
    for row in _table_after(_read(path), "Markets"):
        question = row.get("question", "")
        try:
            spec = parse(question, teams=ALL_TEAM_NAMES)
        except Exception:
            continue
        if not is_total_goal_spec(spec):
            continue
        rows.append(
            CoverageTotalRow(
                kickoff_local=row.get("kickoff_local", ""),
                match=row.get("match", ""),
                entry=_int(row.get("entry")),
                evidence_area=row.get("evidence_area", ""),
                question=question,
                spec=spec,
            )
        )
    return rows


def read_direct_total_rows(path: Path = DIRECT) -> list[DirectAnchorRow]:
    rows: list[DirectAnchorRow] = []
    for row in _table_after(_read(path), "Matched Markets"):
        question = row.get("question", "")
        try:
            spec = parse(question, teams=ALL_TEAM_NAMES)
        except Exception:
            continue
        if not is_total_goal_spec(spec):
            continue
        rows.append(
            DirectAnchorRow(
                status=row.get("status", ""),
                match=row.get("match", ""),
                kickoff_utc=row.get("kickoff_utc", ""),
                model=_int(row.get("model")),
                anchor=_int(row.get("anchor")),
                delta_pp=_int(row.get("delta_pp")),
                volume=_float(row.get("volume")),
                question=question,
                polymarket_question=row.get("polymarket_question", ""),
            )
        )
    return rows


def read_odds_match_rows(path: Path = ODDS) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for row in _table_after(_read(path), "Open Match Odds Sources"):
        match = row.get("match", "")
        if match:
            out[match] = row
    return out


def classify_source_state(
    direct: DirectAnchorRow | None,
    *,
    large_shift_pp: int,
    thin_volume: float,
) -> tuple[str, str]:
    if direct is None:
        return (
            "NO_DIRECT_TOTAL_ANCHOR",
            "use the odds-goal model and recheck public totals odds near lock; no exact direct total anchor is available",
        )
    if abs(direct.delta_pp) >= large_shift_pp:
        return (
            "DIRECT_TOTAL_LARGE_SHIFT",
            "verify exact wording, threshold, period, and an independent totals source before entry",
        )
    if direct.volume < thin_volume:
        return (
            "DIRECT_TOTAL_THIN",
            "exact direct total anchor is aligned but thin; recheck the market near lock",
        )
    return (
        "DIRECT_TOTAL_ALIGNED",
        "exact direct total anchor is aligned with the odds-goal model; keep current-source treatment",
    )


def build_rows(
    coverage_rows: list[CoverageTotalRow] | None = None,
    direct_rows: list[DirectAnchorRow] | None = None,
    odds_rows: dict[str, dict[str, str]] | None = None,
    *,
    large_shift_pp: int = 5,
    thin_volume: float = 5_000.0,
) -> list[AuditRow]:
    coverage_rows = read_coverage_total_rows() if coverage_rows is None else coverage_rows
    direct_rows = read_direct_total_rows() if direct_rows is None else direct_rows
    odds_rows = read_odds_match_rows() if odds_rows is None else odds_rows
    direct_by_key = {(row.match, row.question): row for row in direct_rows}
    out: list[AuditRow] = []
    for total in coverage_rows:
        direct = direct_by_key.get((total.match, total.question))
        source_state, action = classify_source_state(
            direct,
            large_shift_pp=large_shift_pp,
            thin_volume=thin_volume,
        )
        if (
            direct is not None
            and total.evidence_area == "exact_polymarket_anchors"
            and abs(total.entry - direct.anchor) > 1
        ):
            source_state = "DIRECT_TOTAL_ENTRY_MISMATCH"
            action = "regenerate direct coverage and manual pricing artifacts before using this row"
        odds = odds_rows.get(total.match, {})
        out.append(
            AuditRow(
                total=total,
                direct=direct,
                source_state=source_state,
                action=action,
                book_total_goals=odds.get("total_goals", "-"),
                odds_source_state=odds.get("state", "-"),
            )
        )
    return sorted(out, key=lambda row: (row.total.kickoff_local, row.total.match, row.total.question))


def render(
    rows: list[AuditRow],
    *,
    now: datetime | None = None,
    large_shift_pp: int = 5,
    thin_volume: float = 5_000.0,
) -> str:
    now = now or datetime.now(timezone.utc)
    counts = Counter(row.source_state for row in rows)
    direct_rows = [row for row in rows if row.direct is not None]
    max_abs_delta = max((abs(row.direct.delta_pp) for row in direct_rows if row.direct), default=0)
    lines = [
        "# Odds Total Source Audit",
        "",
        "Read-only analysis. `/predictions` was not read and no submissions were made.",
        "",
        f"- run_utc: {now.astimezone(timezone.utc).isoformat(timespec='seconds')}",
        "- sports_predict: not_called",
        "- decision: guardrail_only_no_probability_transform",
        f"- total_goal_rows: {len(rows)}",
        f"- direct_total_rows: {len(direct_rows)}",
        f"- missing_direct_total_rows: {counts.get('NO_DIRECT_TOTAL_ANCHOR', 0)}",
        f"- entry_mismatch_rows: {counts.get('DIRECT_TOTAL_ENTRY_MISMATCH', 0)}",
        f"- large_shift_threshold_pp: {large_shift_pp}",
        f"- large_shift_rows: {counts.get('DIRECT_TOTAL_LARGE_SHIFT', 0)}",
        f"- thin_volume_threshold: ${thin_volume:,.0f}",
        f"- thin_direct_rows: {counts.get('DIRECT_TOTAL_THIN', 0)}",
        f"- max_abs_direct_delta_pp: {max_abs_delta}",
        "",
        "## Source State Summary",
        "",
        "| source_state | markets |",
        "| --- | ---: |",
    ]
    for state in (
        "DIRECT_TOTAL_ALIGNED",
        "DIRECT_TOTAL_THIN",
        "DIRECT_TOTAL_ENTRY_MISMATCH",
        "DIRECT_TOTAL_LARGE_SHIFT",
        "NO_DIRECT_TOTAL_ANCHOR",
    ):
        if counts.get(state):
            lines.append(f"| {state} | {counts[state]} |")

    lines.extend(
        [
            "",
            "## Total Goal Markets",
            "",
            "| kickoff_local | match | source_state | entry | direct_anchor | delta_pp | volume | book_total_goals | odds_source_state | family | period | evidence_area | question | polymarket_question | action |",
            "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in rows:
        direct = row.direct
        direct_anchor = "-" if direct is None else f"{direct.anchor}%"
        delta = "-" if direct is None else f"{direct.delta_pp:+d}"
        volume = "-" if direct is None else f"${direct.volume:,.0f}"
        pm_question = "-" if direct is None else direct.polymarket_question.replace("|", "/")
        spec = row.total.spec
        family = (
            f"{spec.family}:{spec.team}:{spec.threshold}{spec.direction}"
            if spec.family == "team_goals_ge"
            else f"{spec.family}:{spec.threshold}{spec.direction}"
        )
        lines.append(
            f"| {row.total.kickoff_local} | {row.total.match} | {row.source_state} | "
            f"{row.total.entry}% | {direct_anchor} | {delta} | {volume} | "
            f"{row.book_total_goals} | {row.odds_source_state} | {family} | "
            f"{_period_label(spec)} | {row.total.evidence_area} | "
            f"{row.total.question.replace('|', '/')} | {pm_question} | {row.action} |"
        )

    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- `DIRECT_TOTAL_ALIGNED` means the exact direct total-goals market and the odds-goal model are within the configured large-shift threshold.",
            "- `DIRECT_TOTAL_THIN` means the exact direct market passed the live anchor minimum but is still low-liquidity enough to recheck near lock.",
            "- `DIRECT_TOTAL_ENTRY_MISMATCH` means the generated manual coverage entry and direct-anchor audit disagree beyond rounding; refresh artifacts before entry.",
            "- `DIRECT_TOTAL_LARGE_SHIFT` is a hard recheck flag, not an instruction to average or override blindly.",
            "- `NO_DIRECT_TOTAL_ANCHOR` is not a model failure; it means that row still depends on the public odds-goal engine and should be refreshed near lock.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write", action="store_true", help=f"write {OUT}")
    ap.add_argument("--log", type=Path, default=OUT, help="log path")
    ap.add_argument("--large-shift-pp", type=int, default=5)
    ap.add_argument("--thin-volume", type=float, default=5_000.0)
    args = ap.parse_args()

    report = render(
        build_rows(large_shift_pp=args.large_shift_pp, thin_volume=args.thin_volume),
        large_shift_pp=args.large_shift_pp,
        thin_volume=args.thin_volume,
    )
    print(report)
    if args.write:
        args.log.parent.mkdir(parents=True, exist_ok=True)
        args.log.write_text(report, encoding="utf-8")
        print(f"wrote {args.log}")


if __name__ == "__main__":
    main()
