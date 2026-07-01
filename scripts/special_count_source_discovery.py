"""Discover exact public-market sources for special count-threshold rows.

This audit checks whether current Polymarket soccer markets expose the exact
same count market as an unresolved SportsPredict special count question.  It
is intentionally stricter than a broad "related market" search: first-half
corner totals are not a valid source for corner kicks before the first
hydration break, and ordinary goal totals are not a source for total shots.

The script is read-only. It does not call SportsPredict, does not read
`/predictions`, and never submits anything.
"""

from __future__ import annotations

import argparse
import ast
import csv
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from typing import Iterable


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from manual.polymarket_direct import fetch_polymarket_events  # noqa: E402
from questions.parser import MATCH, MarketSpec, parse  # noqa: E402
from questions.teams import ALL_TEAM_NAMES, match_teams, names_match  # noqa: E402


DEFAULT_TEMPLATE = ROOT / "data" / "special_count_sources_template.csv"
OUT = ROOT / "data" / "logs" / "special_count_source_discovery.md"

STATUS_PRIORITY = {
    "EXACT_USABLE": 0,
    "EXACT_BELOW_VOLUME": 1,
    "NEAR_PERIOD_MISMATCH": 2,
    "RELATED_COUNT_MARKET": 3,
    "GOAL_MARKET_ONLY": 4,
    "NO_EXACT_COUNT_MARKET": 5,
    "NO_MATCH_EVENT": 6,
    "UNPARSED_ROW": 7,
}


@dataclass(frozen=True)
class CountSourceRow:
    market_id: str
    match_name: str
    kickoff_utc: str
    risk_state: str
    evidence_key: str
    question: str
    spec: MarketSpec | None


@dataclass(frozen=True)
class DiscoveryRow:
    market_id: str
    match_name: str
    evidence_key: str
    status: str
    reason: str
    event_title: str
    market_question: str
    market_volume: float
    probability: float | None
    source_usable: bool


@dataclass(frozen=True)
class DiscoveryResult:
    decision: str
    rows: tuple[DiscoveryRow, ...]
    source_csv: Path
    min_market_volume: float
    polymarket_status: str
    fetch_error: str


def load_source_rows(path: Path = DEFAULT_TEMPLATE) -> list[CountSourceRow]:
    if not path.exists():
        return []
    rows: list[CountSourceRow] = []
    with path.open(newline="", encoding="utf-8") as fh:
        for raw in csv.DictReader(fh):
            question = (raw.get("question") or "").strip()
            try:
                spec = parse(question, teams=ALL_TEAM_NAMES)
            except Exception:
                spec = None
            rows.append(
                CountSourceRow(
                    market_id=(raw.get("market_id") or "").strip(),
                    match_name=(raw.get("match") or "").strip(),
                    kickoff_utc=(raw.get("kickoff_utc") or "").strip(),
                    risk_state=(raw.get("risk_state") or "").strip(),
                    evidence_key=(raw.get("evidence_key") or "").strip(),
                    question=question,
                    spec=spec,
                )
            )
    return rows


def discover_sources(
    source_rows: Iterable[CountSourceRow],
    events: list[dict],
    *,
    source_csv: Path = DEFAULT_TEMPLATE,
    min_market_volume: float = 1000.0,
    polymarket_status: str = "FETCHED",
    fetch_error: str = "",
) -> DiscoveryResult:
    rows: list[DiscoveryRow] = []
    for source_row in source_rows:
        rows.append(_discover_row(source_row, events, min_market_volume=min_market_volume))

    if fetch_error:
        decision = "DISCOVERY_FETCH_FAILED"
    elif not rows:
        decision = "NO_SPECIAL_COUNT_ROWS"
    elif any(row.status == "EXACT_USABLE" for row in rows):
        decision = "DISCOVERY_HAS_USABLE_EXACT_SOURCES"
    else:
        decision = "DISCOVERY_COMPLETE_NO_EXACT_SOURCES"

    return DiscoveryResult(
        decision=decision,
        rows=tuple(rows),
        source_csv=source_csv,
        min_market_volume=min_market_volume,
        polymarket_status=polymarket_status,
        fetch_error=fetch_error,
    )


def _discover_row(row: CountSourceRow, events: list[dict], *, min_market_volume: float) -> DiscoveryRow:
    if row.spec is None:
        return _row_result(row, "UNPARSED_ROW", "question did not parse as a known market")
    if row.spec.family not in {"total_count_ge", "total_count_before_hydration"}:
        return _row_result(row, "UNPARSED_ROW", f"not a special count-threshold family: {row.spec.family}")

    matched_events = [event for event in events if _event_matches_match(event, row.match_name)]
    if not matched_events:
        return _row_result(row, "NO_MATCH_EVENT", "no same-match Polymarket event was found")

    candidates: list[DiscoveryRow] = []
    for event in matched_events:
        event_title = str(event.get("title") or "")
        for market in event.get("markets") or []:
            candidate = _candidate_from_market(row, event_title, market, min_market_volume=min_market_volume)
            if candidate:
                candidates.append(candidate)

    if not candidates:
        return _row_result(
            row,
            "NO_EXACT_COUNT_MARKET",
            "same-match events exist, but no goal or count markets were exposed in the fetched payload",
            event_title=str(matched_events[0].get("title") or ""),
        )

    return sorted(
        candidates,
        key=lambda item: (STATUS_PRIORITY[item.status], -item.market_volume, item.market_question),
    )[0]


def _candidate_from_market(
    row: CountSourceRow,
    event_title: str,
    market: dict,
    *,
    min_market_volume: float,
) -> DiscoveryRow | None:
    question = str(market.get("question") or "")
    q = _norm(question)
    if "o/u" not in q and "over/under" not in q:
        return None

    if _is_goal_total_market(q):
        return _row_result(
            row,
            "GOAL_MARKET_ONLY",
            "same-match market is an ordinary goal total, not a count source",
            event_title=event_title,
            market_question=question,
            market_volume=_volume(market),
            probability=_outcome_probability(market, "Over"),
        )

    spec = row.spec
    if spec is None:
        return None

    if spec.family == "total_count_ge" and spec.stat == "shots":
        return _shots_candidate(row, event_title, market, question, q, min_market_volume=min_market_volume)

    if spec.family == "total_count_before_hydration" and spec.stat == "corners":
        return _hydration_corner_candidate(row, event_title, market, question, q, min_market_volume=min_market_volume)

    if _is_count_market(q):
        return _row_result(
            row,
            "RELATED_COUNT_MARKET",
            f"same-match count market is for a different stat/market than {row.evidence_key}",
            event_title=event_title,
            market_question=question,
            market_volume=_volume(market),
            probability=_outcome_probability(market, "Over"),
        )
    return None


def _shots_candidate(
    row: CountSourceRow,
    event_title: str,
    market: dict,
    question: str,
    q: str,
    *,
    min_market_volume: float,
) -> DiscoveryRow | None:
    if "shot" not in q:
        return None
    volume = _volume(market)
    probability = _outcome_probability(market, "Over")
    line = (row.spec.threshold or 0) - 0.5 if row.spec else 0.0
    is_exact = (
        "total shots" in q
        and "shots on target" not in q
        and _period_is_match(q)
        and _line_matches(q, line)
    )
    if is_exact:
        status = "EXACT_USABLE" if volume >= min_market_volume else "EXACT_BELOW_VOLUME"
        reason = (
            f"exact total-shots O/U line with volume ${volume:,.0f}"
            if volume >= min_market_volume
            else f"exact total-shots O/U line but volume ${volume:,.0f} < ${min_market_volume:,.0f}"
        )
        return _row_result(row, status, reason, event_title, question, volume, probability)
    return _row_result(
        row,
        "RELATED_COUNT_MARKET",
        "same-match shots market is not the exact total-shots threshold/period",
        event_title,
        question,
        volume,
        probability,
    )


def _hydration_corner_candidate(
    row: CountSourceRow,
    event_title: str,
    market: dict,
    question: str,
    q: str,
    *,
    min_market_volume: float,
) -> DiscoveryRow | None:
    if "corner" not in q:
        return None
    volume = _volume(market)
    probability = _outcome_probability(market, "Over")
    line = (row.spec.threshold or 0) - 0.5 if row.spec else 0.0
    mentions_hydration = "hydration" in q or "cooling break" in q or "drinks break" in q
    if mentions_hydration and _line_matches(q, line):
        status = "EXACT_USABLE" if volume >= min_market_volume else "EXACT_BELOW_VOLUME"
        reason = (
            f"exact hydration-window corner O/U line with volume ${volume:,.0f}"
            if volume >= min_market_volume
            else f"exact hydration-window corner O/U line but volume ${volume:,.0f} < ${min_market_volume:,.0f}"
        )
        return _row_result(row, status, reason, event_title, question, volume, probability)
    if "1st half" in q or "first half" in q:
        return _row_result(
            row,
            "NEAR_PERIOD_MISMATCH",
            "first-half corner total is related but not the first-hydration-break period",
            event_title,
            question,
            volume,
            probability,
        )
    return _row_result(
        row,
        "RELATED_COUNT_MARKET",
        "same-match corner market is not the exact hydration-window threshold/period",
        event_title,
        question,
        volume,
        probability,
    )


def _row_result(
    row: CountSourceRow,
    status: str,
    reason: str,
    event_title: str = "",
    market_question: str = "",
    market_volume: float = 0.0,
    probability: float | None = None,
) -> DiscoveryRow:
    return DiscoveryRow(
        market_id=row.market_id,
        match_name=row.match_name,
        evidence_key=row.evidence_key,
        status=status,
        reason=reason,
        event_title=event_title,
        market_question=market_question,
        market_volume=market_volume,
        probability=probability,
        source_usable=status == "EXACT_USABLE",
    )


def _event_matches_match(event: dict, match_name: str) -> bool:
    try:
        first, second = match_teams(match_name)
    except Exception:
        return False
    title = str(event.get("title") or "")
    base = title.split(" - ", 1)[0]
    if " vs" not in base.lower():
        return False
    left, _, right = base.partition(" vs")
    pair = {left.strip().rstrip("."), right.lstrip(". ").strip()}
    return any(names_match(first, team) for team in pair) and any(names_match(second, team) for team in pair)


def _loads_maybe_json(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return ast.literal_eval(value)
    return value


def _outcome_probability(market: dict, target: str) -> float | None:
    outcomes = _loads_maybe_json(market.get("outcomes"))
    prices = _loads_maybe_json(market.get("outcomePrices"))
    if not outcomes or not prices:
        return None
    pairs: list[tuple[str, float]] = []
    for outcome, price in zip(outcomes, prices):
        try:
            pairs.append((str(outcome), float(price)))
        except (TypeError, ValueError):
            continue
    total = sum(price for _outcome, price in pairs)
    if total <= 0:
        return None
    for outcome, price in pairs:
        if outcome.strip().lower() == target.strip().lower():
            return price / total
    return None


def _volume(market: dict) -> float:
    for key in ("volumeNum", "volume", "liquidityNum", "liquidity"):
        if key not in market:
            continue
        try:
            return float(market.get(key) or 0.0)
        except (TypeError, ValueError):
            continue
    return 0.0


def _period_is_match(q: str) -> bool:
    return "1st half" not in q and "first half" not in q and "2nd half" not in q and "second half" not in q


def _line_matches(q: str, line: float) -> bool:
    target = f"{line:.1f}"
    return re.search(rf"\b{re.escape(target)}\b", q) is not None


def _is_goal_total_market(q: str) -> bool:
    return "o/u" in q and not _is_count_market(q)


def _is_count_market(q: str) -> bool:
    return any(term in q for term in ("corner", "shot", "card", "foul", "offside"))


def _norm(value: str) -> str:
    return re.sub(r"\s+", " ", value.lower().replace(" vs. ", " vs ")).strip()


def _fmt_probability(value: float | None) -> str:
    return "" if value is None else f"{value:.3f}"


def _fmt_volume(value: float) -> str:
    return f"{value:.0f}"


def render(result: DiscoveryResult, *, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    counts = Counter(row.status for row in result.rows)
    exact_usable = counts.get("EXACT_USABLE", 0)
    exact_below = counts.get("EXACT_BELOW_VOLUME", 0)
    near = counts.get("NEAR_PERIOD_MISMATCH", 0) + counts.get("RELATED_COUNT_MARKET", 0)
    no_exact = sum(
        counts.get(status, 0)
        for status in ("GOAL_MARKET_ONLY", "NO_EXACT_COUNT_MARKET", "NO_MATCH_EVENT", "UNPARSED_ROW")
    )
    lines = [
        "# Special Count Source Discovery",
        "",
        "Read-only analysis. SportsPredict was not called, `/predictions` was not read, and no submissions were made.",
        "",
        f"- run_utc: {now.astimezone(timezone.utc).isoformat(timespec='seconds')}",
        "- sports_predict: not_called",
        "- bot_predictions: not_read",
        f"- source_csv: {result.source_csv}",
        f"- min_market_volume: ${result.min_market_volume:,.0f}",
        f"- polymarket_status: {result.polymarket_status}",
        f"- fetch_error: {result.fetch_error or '-'}",
        f"- decision: {result.decision}",
        f"- rows: {len(result.rows)}",
        f"- exact_usable_rows: {exact_usable}",
        f"- exact_below_volume_rows: {exact_below}",
        f"- near_miss_rows: {near}",
        f"- no_exact_market_rows: {no_exact}",
        "",
        "## Status Summary",
        "",
        "| status | rows |",
        "| --- | ---: |",
    ]
    for status in sorted(counts, key=lambda value: STATUS_PRIORITY.get(value, 99)):
        lines.append(f"| {status} | {counts[status]} |")

    lines.extend(
        [
            "",
            "## Row Discovery",
            "",
            "| market_id | match | evidence_key | status | usable | probability | volume | event | market | reason |",
            "| --- | --- | --- | --- | --- | ---: | ---: | --- | --- | --- |",
        ]
    )
    for row in result.rows:
        lines.append(
            f"| {row.market_id} | {row.match_name} | {row.evidence_key} | {row.status} | "
            f"{str(row.source_usable).lower()} | {_fmt_probability(row.probability)} | {_fmt_volume(row.market_volume)} | "
            f"{row.event_title} | {row.market_question} | {row.reason} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- `EXACT_USABLE` is the only status that may be used as an independent current source without further transformation.",
            "- `NEAR_PERIOD_MISMATCH` and `RELATED_COUNT_MARKET` are research leads only; they must not fill `special_count_sources_template.csv` as exact sources.",
            "- `GOAL_MARKET_ONLY` confirms that the match is present on Polymarket but its fetched markets do not support the special count row.",
            "",
        ]
    )
    return "\n".join(lines)


def _load_events_from_json(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-csv", type=Path, default=DEFAULT_TEMPLATE)
    parser.add_argument("--events-json", type=Path, help="Use a saved Polymarket events JSON fixture instead of fetching.")
    parser.add_argument("--min-market-volume", type=float, default=1000.0)
    parser.add_argument("--write", action="store_true", help=f"write {OUT}")
    args = parser.parse_args()

    source_rows = load_source_rows(args.source_csv)
    fetch_error = ""
    if args.events_json:
        events = _load_events_from_json(args.events_json)
        polymarket_status = f"LOADED_JSON {args.events_json}"
    else:
        try:
            events = fetch_polymarket_events()
            polymarket_status = "FETCHED"
        except Exception as exc:  # pragma: no cover - exercised only on live network failures.
            events = []
            polymarket_status = "FETCH_FAILED"
            fetch_error = f"{type(exc).__name__}: {exc}"

    result = discover_sources(
        source_rows,
        events,
        source_csv=args.source_csv,
        min_market_volume=args.min_market_volume,
        polymarket_status=polymarket_status,
        fetch_error=fetch_error,
    )
    text = render(result)
    print(text)
    if args.write:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(text, encoding="utf-8")
        print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
