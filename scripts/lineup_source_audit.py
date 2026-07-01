"""Audit live ESPN lineup availability for open player-prop markets.

This is a read-only manual-entry execution audit. It uses SportsPredict only to
read open market metadata, then checks ESPN's public summary endpoint through
`pull_lineups.py`. It does not read `/predictions` and never submits.

The audit is deliberately separate from pricing: a pricing run may keep the
safe all-90 default when lineups are not posted, while this report makes the
remaining lineup risk explicit for readiness and scheduled pre-match checks.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from typing import Callable
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from full_manual_pricing import MarketRow, market_players, read_open_markets, selected_markets  # noqa: E402
from pull_lineups import pull_lineup, write_lineup  # noqa: E402
from questions.parser import parse  # noqa: E402
from questions.teams import ALL_TEAM_NAMES  # noqa: E402


OUT = ROOT / "data" / "logs" / "lineup_source_audit.md"


@dataclass(frozen=True)
class MatchLineupRow:
    kickoff: datetime
    match_name: str
    player_rows: int
    players: tuple[str, ...]
    status: str
    risk_state: str
    minutes_players: int
    starter_players: int
    bench_players: int
    unmatched: tuple[str, ...]
    minutes_to_kickoff: int
    in_pull_window: bool
    error: str = ""


@dataclass(frozen=True)
class LineupAuditResult:
    status: str
    rows: tuple[MatchLineupRow, ...]
    all_open: bool
    include_started: bool
    local_date: str
    pull_window_minutes: int


Puller = Callable[[str, datetime, set[str]], dict]


def _group(rows: list[MarketRow]) -> dict[str, list[MarketRow]]:
    grouped: dict[str, list[MarketRow]] = {}
    for row in rows:
        grouped.setdefault(row.match_name, []).append(row)
    return grouped


def _player_market_count(rows: list[MarketRow]) -> int:
    count = 0
    for row in rows:
        try:
            if parse(row.question, teams=ALL_TEAM_NAMES).player:
                count += 1
        except Exception:
            continue
    return count


def _select_rows(
    rows: list[MarketRow],
    *,
    local_date: str,
    tz: ZoneInfo,
    all_open: bool,
    include_started: bool,
    matches: set[str] | None,
    now: datetime,
) -> list[MarketRow]:
    if all_open:
        grace = timedelta(minutes=10)
        selected = []
        for row in rows:
            if matches and row.match_name not in matches:
                continue
            if not include_started and row.kickoff < now.astimezone(timezone.utc) - grace:
                continue
            selected.append(row)
        return sorted(selected, key=lambda row: (row.kickoff, row.match_name, row.market_id))
    return selected_markets(
        rows,
        local_date=local_date,
        tz=tz,
        remaining_only=not include_started,
        now=now,
        matches=matches,
    )


def audit_lineups(
    rows: list[MarketRow],
    *,
    local_date: str,
    tz: ZoneInfo,
    all_open: bool,
    include_started: bool = False,
    matches: set[str] | None = None,
    pull_window_minutes: int = 100,
    now: datetime | None = None,
    puller: Puller = pull_lineup,
    write: bool = False,
) -> LineupAuditResult:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    selected = _select_rows(
        rows,
        local_date=local_date,
        tz=tz,
        all_open=all_open,
        include_started=include_started,
        matches=matches,
        now=now,
    )
    players_by_match = market_players(selected)
    rows_by_match = _group(selected)
    out: list[MatchLineupRow] = []
    for match_name in sorted(players_by_match, key=lambda value: rows_by_match[value][0].kickoff):
        players = players_by_match[match_name]
        if not players:
            continue
        kickoff = rows_by_match[match_name][0].kickoff.astimezone(timezone.utc)
        minutes_to_kickoff = int(round((kickoff - now).total_seconds() / 60.0))
        in_window = 0 <= minutes_to_kickoff <= pull_window_minutes
        try:
            result = puller(match_name, kickoff, players)
        except Exception as exc:  # pragma: no cover - live network failures only.
            result = {"status": "error", "error": f"{type(exc).__name__}: {exc}", "minutes": {}, "unmatched": sorted(players), "detail": []}
        if write and result.get("status") == "pulled":
            write_lineup(match_name, kickoff, result)
        minutes = result.get("minutes") or {}
        detail = result.get("detail") or []
        starter_players = sum(1 for _player, _espn, role, _mins in detail if str(role).upper().startswith("START"))
        bench_players = sum(1 for _player, _espn, role, _mins in detail if str(role).lower() == "bench")
        status = str(result.get("status") or "unknown")
        unmatched = tuple(sorted(result.get("unmatched") or ())) if status == "pulled" else ()
        out.append(
            MatchLineupRow(
                kickoff=kickoff,
                match_name=match_name,
                player_rows=_player_market_count(rows_by_match[match_name]),
                players=tuple(sorted(players)),
                status=status,
                risk_state=_risk_state(status, unmatched=unmatched, in_pull_window=in_window),
                minutes_players=len(minutes),
                starter_players=starter_players,
                bench_players=bench_players,
                unmatched=unmatched,
                minutes_to_kickoff=minutes_to_kickoff,
                in_pull_window=in_window,
                error=str(result.get("error") or ""),
            )
        )
    return LineupAuditResult(
        status=_audit_status(out),
        rows=tuple(out),
        all_open=all_open,
        include_started=include_started,
        local_date=local_date,
        pull_window_minutes=pull_window_minutes,
    )


def _risk_state(status: str, *, unmatched: tuple[str, ...], in_pull_window: bool) -> str:
    if status == "pulled" and unmatched:
        return "PULLED_WITH_UNMATCHED_PLAYERS"
    if status == "pulled":
        return "PULLED_CONFIRMED"
    if status == "not_posted" and in_pull_window:
        return "NOT_POSTED_IN_PULL_WINDOW"
    if status == "not_posted":
        return "NOT_POSTED_PRE_WINDOW"
    if status == "no_event":
        return "NO_ESPN_EVENT"
    return "SOURCE_ERROR"


def _audit_status(rows: list[MatchLineupRow]) -> str:
    if not rows:
        return "NO_OPEN_PLAYER_ROWS"
    if any(row.risk_state in {"SOURCE_ERROR", "NO_ESPN_EVENT", "PULLED_WITH_UNMATCHED_PLAYERS"} for row in rows):
        return "LINEUP_SOURCE_ISSUES"
    if any(row.risk_state == "NOT_POSTED_IN_PULL_WINDOW" for row in rows):
        return "LINEUP_NOT_POSTED_IN_PULL_WINDOW"
    if all(row.risk_state == "PULLED_CONFIRMED" for row in rows):
        return "COMPLETE"
    if any(row.risk_state == "PULLED_CONFIRMED" for row in rows):
        return "PARTIAL_PENDING"
    return "PENDING_NOT_IN_WINDOW"


def _fmt_bool(value: bool) -> str:
    return str(value).lower()


def render(result: LineupAuditResult, *, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    counts = Counter(row.risk_state for row in result.rows)
    pulled = counts.get("PULLED_CONFIRMED", 0) + counts.get("PULLED_WITH_UNMATCHED_PLAYERS", 0)
    not_posted = counts.get("NOT_POSTED_IN_PULL_WINDOW", 0) + counts.get("NOT_POSTED_PRE_WINDOW", 0)
    unmatched = sum(len(row.unmatched) for row in result.rows)
    player_rows = sum(row.player_rows for row in result.rows)
    lines = [
        "# Lineup Source Audit",
        "",
        "Read-only analysis. `/predictions` was not read and no submissions were made.",
        "",
        f"- run_utc: {now.astimezone(timezone.utc).isoformat(timespec='seconds')}",
        "- sports_predict: read_only GET /events, /lobbies, /matches, /markets, /results only",
        "- bot_predictions: not_read",
        f"- local_date: {'all-open' if result.all_open else result.local_date}",
        f"- include_started: {_fmt_bool(result.include_started)}",
        f"- pull_window_minutes: {result.pull_window_minutes}",
        f"- lineup_source_status: {result.status}",
        f"- player_matches: {len(result.rows)}",
        f"- player_market_rows: {player_rows}",
        f"- pulled_matches: {pulled}",
        f"- not_posted_matches: {not_posted}",
        f"- no_event_matches: {counts.get('NO_ESPN_EVENT', 0)}",
        f"- source_error_matches: {counts.get('SOURCE_ERROR', 0)}",
        f"- in_window_not_posted_matches: {counts.get('NOT_POSTED_IN_PULL_WINDOW', 0)}",
        f"- unmatched_players: {unmatched}",
        "",
        "## Status Summary",
        "",
        "| risk_state | matches |",
        "| --- | ---: |",
    ]
    for status, count in sorted(counts.items()):
        lines.append(f"| {status} | {count} |")
    lines.extend(
        [
            "",
            "## Match Lineups",
            "",
            "| kickoff_utc | match | minutes_to_kickoff | in_window | status | risk_state | player_rows | players | starters | bench | unmatched | error |",
            "| --- | --- | ---: | --- | --- | --- | ---: | --- | ---: | ---: | --- | --- |",
        ]
    )
    for row in result.rows:
        lines.append(
            f"| {row.kickoff.isoformat(timespec='minutes')} | {row.match_name} | {row.minutes_to_kickoff} | "
            f"{_fmt_bool(row.in_pull_window)} | {row.status} | {row.risk_state} | {row.player_rows} | "
            f"{', '.join(row.players)} | {row.starter_players} | {row.bench_players} | "
            f"{', '.join(row.unmatched)} | {row.error} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- `COMPLETE` means ESPN posted verified lineups for every open player-prop match and all tracked players matched.",
            "- `PENDING_NOT_IN_WINDOW` is normal before the final pre-match window; keep scheduled rechecks active.",
            "- `LINEUP_NOT_POSTED_IN_PULL_WINDOW`, `LINEUP_SOURCE_ISSUES`, and `PULLED_WITH_UNMATCHED_PLAYERS` block player-prop lock until resolved or exact current prop odds supersede them.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sports-predict", action="store_true", help="Acknowledge read-only SportsPredict open-market fetch.")
    parser.add_argument("--tz", default="America/Chicago")
    parser.add_argument("--local-date", help="Local date to audit, YYYY-MM-DD. Default: today in --tz.")
    parser.add_argument("--all-open", action="store_true", help="Audit all currently open future player-prop matches.")
    parser.add_argument("--include-started", action="store_true")
    parser.add_argument("--match", action="append", help="Restrict to an exact SportsPredict match name. Repeatable.")
    parser.add_argument("--pull-window-minutes", type=int, default=100)
    parser.add_argument("--lineup-write", action="store_true", help="Persist pulled ESPN lineup minutes if lineups are posted.")
    parser.add_argument("--write", action="store_true", help=f"write {OUT}")
    args = parser.parse_args()

    if not args.sports_predict:
        raise SystemExit("--sports-predict is required to make the read-only API use explicit")
    tz = ZoneInfo(args.tz)
    now = datetime.now(timezone.utc)
    local_date = args.local_date or now.astimezone(tz).date().isoformat()
    result = audit_lineups(
        read_open_markets(),
        local_date=local_date,
        tz=tz,
        all_open=args.all_open,
        include_started=args.include_started,
        matches=set(args.match) if args.match else None,
        pull_window_minutes=args.pull_window_minutes,
        now=now,
        write=args.lineup_write,
    )
    text = render(result, now=now)
    print(text)
    if args.write:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(text, encoding="utf-8")
        print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
