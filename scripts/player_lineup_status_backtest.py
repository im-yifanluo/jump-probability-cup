"""Backtest settled player props by historical ESPN lineup status.

This is a read-only manual-strategy diagnostic. It does not read
SportsPredict `/predictions` and never submits.

The goal is narrower than `player_policy_backtest.py`: measure whether settled
player-prop performance changes when the player was a confirmed starter, bench
player, or not listed in ESPN's historical match rosters.

Usage:
    uv run --with requests python scripts/player_lineup_status_backtest.py --sports-predict --write
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import sys
from typing import Callable, Iterable

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from backtest.history import (  # noqa: E402
    HistoryMarket,
    asymmetric_field_policy,
    brier,
    clip_prob,
    official_delta_rbp,
)
from player_policy_backtest import load_player_rows  # noqa: E402
from questions.parser import parse  # noqa: E402
from questions.teams import ALL_TEAM_NAMES, CODE_TO_NAME, names_match  # noqa: E402


OUT = ROOT / "data" / "logs" / "player_lineup_status_backtest.md"
MIN_STARTERS = 11
SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard"
SUMMARY = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/summary"


@dataclass(frozen=True)
class LineupRecord:
    display: str
    starter: bool
    team: str


@dataclass(frozen=True)
class LineupStatus:
    status: str
    espn_player: str = ""
    espn_team: str = ""
    reason: str = ""


@dataclass(frozen=True)
class EventContext:
    match: str
    event_id: str
    kickoff: datetime
    rosters: list


@dataclass(frozen=True)
class ClassifiedPlayerRow:
    row: HistoryMarket
    status: LineupStatus
    player: str
    team_hint: str


Strategy = Callable[[ClassifiedPlayerRow], float]


def _event_date(event: dict) -> datetime:
    return datetime.fromisoformat(str(event["date"]).replace("Z", "+00:00"))


def _fold(name: str) -> str:
    import unicodedata

    return (
        unicodedata.normalize("NFKD", name)
        .encode("ascii", "ignore")
        .decode()
        .lower()
        .replace("-", " ")
    )


def _normalize_player_name(name: str) -> str:
    import re

    return " ".join(sorted(re.sub(r"[^a-z0-9 ]+", " ", _fold(name)).split()))


def _squash_repeats(value: str) -> str:
    out = []
    for ch in value:
        if not out or out[-1] != ch:
            out.append(ch)
    return "".join(out)


def verify_rosters(summary: dict) -> list | None:
    rosters = summary.get("rosters")
    if not rosters or len(rosters) != 2:
        return None
    for team in rosters:
        if sum(1 for p in team.get("roster", []) if p.get("starter")) < MIN_STARTERS:
            return None
    return rosters


def canonical_team(name: str) -> str:
    for team in sorted(ALL_TEAM_NAMES):
        if names_match(team, name):
            return team
    raise ValueError(f"no canonical team match for {name!r}")


def split_match(match: str) -> tuple[str, str]:
    left, sep, right = match.partition(" vs ")
    if not sep:
        raise ValueError(f"cannot split match name {match!r}")
    left = left.strip()
    right = right.strip()
    return (
        CODE_TO_NAME[left] if left in CODE_TO_NAME else canonical_team(left),
        CODE_TO_NAME[right] if right in CODE_TO_NAME else canonical_team(right),
    )


def _event_teams(event: dict) -> tuple[str, str]:
    comp = event["competitions"][0]
    home = next(c for c in comp["competitors"] if c["homeAway"] == "home")["team"]["displayName"]
    away = next(c for c in comp["competitors"] if c["homeAway"] == "away")["team"]["displayName"]
    return home, away


def event_matches(event: dict, left: str, right: str) -> bool:
    home, away = _event_teams(event)
    return (
        (names_match(left, home) and names_match(right, away))
        or (names_match(left, away) and names_match(right, home))
    )


def fetch_scoreboard_events(start: date, end: date, session: requests.Session) -> list[dict]:
    events: list[dict] = []
    seen: set[str] = set()
    cur = start
    while cur <= end:
        board = session.get(SCOREBOARD, params={"dates": cur.strftime("%Y%m%d")}, timeout=20).json()
        for event in board.get("events", []):
            event_id = str(event.get("id"))
            if event_id not in seen:
                seen.add(event_id)
                events.append(event)
        cur += timedelta(days=1)
    return events


def build_contexts(
    matches: Iterable[str],
    *,
    start: date,
    end: date,
    session: requests.Session | None = None,
) -> tuple[dict[str, EventContext], list[str]]:
    """Map SportsPredict match names to ESPN events and verified rosters."""

    session = session or requests.Session()
    events = fetch_scoreboard_events(start, end, session)
    contexts: dict[str, EventContext] = {}
    missing: list[str] = []
    for match in sorted(set(matches)):
        left, right = split_match(match)
        event = next((event for event in events if event_matches(event, left, right)), None)
        if event is None:
            missing.append(f"{match}: no ESPN event")
            continue
        summary = session.get(SUMMARY, params={"event": event["id"]}, timeout=20).json()
        rosters = verify_rosters(summary)
        if rosters is None:
            missing.append(f"{match}: no verified ESPN roster")
            continue
        contexts[match] = EventContext(
            match=match,
            event_id=str(event["id"]),
            kickoff=_event_date(event),
            rosters=rosters,
        )
    return contexts, missing


def _team_name(roster: dict) -> str:
    team = roster.get("team") or {}
    return team.get("displayName") or team.get("name") or team.get("shortDisplayName") or ""


def _records(rosters: list, team_hint: str | None = None) -> tuple[list[LineupRecord], str]:
    records: list[LineupRecord] = []
    matched_team = ""
    for roster in rosters:
        team = _team_name(roster)
        if team_hint and not names_match(team_hint, team):
            continue
        if team_hint:
            matched_team = team
        for player in roster.get("roster", []):
            athlete = player.get("athlete") or {}
            display = athlete.get("displayName") or athlete.get("fullName") or ""
            if not display:
                continue
            records.append(LineupRecord(display=display, starter=bool(player.get("starter")), team=team))
    return records, matched_team


def _find_player(player: str, records: list[LineupRecord]) -> tuple[str, list[LineupRecord]]:
    """Return a match-level reason plus matching records.

    Matching mirrors the live lineup puller: exact normalized full name first,
    then repeated-letter squashing, then unambiguous surname fallback.
    """

    normalized = _normalize_player_name(player)
    exact = [rec for rec in records if _normalize_player_name(rec.display) == normalized]
    if len(exact) == 1:
        return "exact_full_name", exact
    if len(exact) > 1:
        return "ambiguous_exact_full_name", exact

    squashed = _squash_repeats(normalized)
    squashed_hits = [
        rec
        for rec in records
        if _squash_repeats(_normalize_player_name(rec.display)) == squashed
    ]
    if len(squashed_hits) == 1:
        return "squashed_full_name", squashed_hits
    if len(squashed_hits) > 1:
        return "ambiguous_squashed_full_name", squashed_hits

    toks = _fold(player).split()
    if not toks:
        return "empty_player_name", []
    surname = toks[-1]
    surname_hits = [rec for rec in records if (_fold(rec.display).split() or [""])[-1] == surname]
    if len(surname_hits) == 1:
        return "unambiguous_surname", surname_hits
    if len(surname_hits) > 1:
        return "ambiguous_surname", surname_hits

    squashed_surname = _squash_repeats(surname)
    squashed_surname_hits = [
        rec
        for rec in records
        if _squash_repeats((_fold(rec.display).split() or [""])[-1]) == squashed_surname
    ]
    if len(squashed_surname_hits) == 1:
        return "squashed_unambiguous_surname", squashed_surname_hits
    if len(squashed_surname_hits) > 1:
        return "ambiguous_squashed_surname", squashed_surname_hits
    return "not_found", []


def classify_player(player: str, rosters: list, team_hint: str | None = None) -> LineupStatus:
    records, matched_team = _records(rosters, team_hint)
    if team_hint and not matched_team:
        return LineupStatus("team_mismatch", reason=f"no ESPN roster matched {team_hint}")
    if not records:
        return LineupStatus("missing_roster", reason="no lineup records")

    reason, matches = _find_player(player, records)
    if not matches:
        return LineupStatus("not_listed", reason=reason)
    if len(matches) > 1:
        preview = ", ".join(f"{rec.display} ({rec.team})" for rec in matches[:4])
        return LineupStatus("ambiguous", reason=f"{reason}: {preview}")

    rec = matches[0]
    return LineupStatus(
        "starter" if rec.starter else "bench",
        espn_player=rec.display,
        espn_team=rec.team,
        reason=reason,
    )


def classify_rows(
    rows: list[HistoryMarket],
    contexts: dict[str, EventContext],
) -> tuple[list[ClassifiedPlayerRow], list[str]]:
    classified: list[ClassifiedPlayerRow] = []
    skipped: list[str] = []
    for row in rows:
        ctx = contexts.get(row.match)
        if ctx is None:
            skipped.append(f"{row.match}: no lineup context")
            continue
        try:
            spec = parse(row.question, teams=ALL_TEAM_NAMES)
        except Exception as exc:
            skipped.append(f"{row.match}: parse failed: {row.question}: {exc}")
            continue
        if not spec.player:
            skipped.append(f"{row.match}: non-player row reached classifier: {row.question}")
            continue
        team_hint = spec.params.get("player_team") or ""
        status = classify_player(spec.player, ctx.rosters, team_hint=team_hint or None)
        classified.append(
            ClassifiedPlayerRow(
                row=row,
                status=status,
                player=spec.player,
                team_hint=team_hint,
            )
        )
    return classified, skipped


def _asym_player(row: ClassifiedPlayerRow) -> float:
    return asymmetric_field_policy(above_k=0.0, below_k=0.75, max_abs_dev=0.10)(row.row)


def policy_specs() -> list[tuple[str, Strategy]]:
    return [
        ("submitted", lambda r: r.row.you),
        ("field", lambda r: r.row.crowd),
        ("player_asym075_cap10", _asym_player),
        (
            "starter_asym_else_field",
            lambda r: _asym_player(r) if r.status.status == "starter" else r.row.crowd,
        ),
        (
            "confirmed_starter_model_else_field",
            lambda r: r.row.you if r.status.status == "starter" else r.row.crowd,
        ),
    ]


def _score(rows: list[ClassifiedPlayerRow], strategy: Strategy) -> dict[str, float]:
    if not rows:
        return {"n": 0, "delta": 0.0, "brier": float("nan"), "mean_p": float("nan")}
    preds = [clip_prob(strategy(row)) for row in rows]
    return {
        "n": len(rows),
        "delta": sum(official_delta_rbp(row.row, p) for row, p in zip(rows, preds)),
        "brier": sum(brier(p, row.row.outcome or 0) for row, p in zip(rows, preds)) / len(rows),
        "mean_p": sum(preds) / len(preds),
    }


def _status_summary(rows: list[ClassifiedPlayerRow]) -> list[dict[str, float | str]]:
    grouped: dict[str, list[ClassifiedPlayerRow]] = defaultdict(list)
    for row in rows:
        grouped[row.status.status].append(row)
    out = []
    for status, part in sorted(grouped.items()):
        out.append(
            {
                "status": status,
                "n": len(part),
                "skill_vs_field": sum(
                    100 * (brier(r.row.crowd, r.row.outcome or 0) - brier(r.row.you, r.row.outcome or 0))
                    for r in part
                ),
                "brier_model": sum(brier(r.row.you, r.row.outcome or 0) for r in part) / len(part),
                "brier_field": sum(brier(r.row.crowd, r.row.outcome or 0) for r in part) / len(part),
                "model": sum(r.row.you for r in part) / len(part),
                "field": sum(r.row.crowd for r in part) / len(part),
                "hit": sum(r.row.outcome or 0 for r in part) / len(part),
            }
        )
    return out


def _policy_summary(rows: list[ClassifiedPlayerRow]) -> list[dict[str, float | str]]:
    return [
        {"policy": name, **_score(rows, strategy)}
        for name, strategy in policy_specs()
    ]


def _policy_by_status(rows: list[ClassifiedPlayerRow]) -> dict[str, list[dict[str, float | str]]]:
    grouped: dict[str, list[ClassifiedPlayerRow]] = defaultdict(list)
    for row in rows:
        grouped[row.status.status].append(row)
    return {status: _policy_summary(part) for status, part in sorted(grouped.items())}


def _md_table(headers: list[str], rows: list[list[str]]) -> list[str]:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return lines


def render(
    rows: list[HistoryMarket],
    contexts: dict[str, EventContext],
    missing_contexts: list[str],
    classified: list[ClassifiedPlayerRow],
    skipped: list[str],
    *,
    now: datetime,
) -> str:
    lines = [
        "# Player Lineup Status Backtest",
        "",
        "Read-only analysis. `/predictions` was not read and no submissions were made.",
        "",
        f"- run_utc: {now.astimezone(timezone.utc).isoformat(timespec='seconds')}",
        f"- settled_player_rows_requested: {len(rows)}",
        f"- matched_match_contexts: {len(contexts)}",
        f"- missing_match_contexts: {len(missing_contexts)}",
        f"- classified_player_rows: {len(classified)}",
        "- lineup_source: ESPN historical summary rosters; accepted only when both teams have at least 11 starters",
        "- scoring: exact official-RBP deltas from displayed market RBP and realized Brier",
        "- limitation: this validates lineup-status handling of submitted historical probabilities; it is not a full time-machine rerun of every old player model input",
        "",
        "## Status Diagnostics",
        "",
    ]
    lines += _md_table(
        ["status", "n", "skill_vs_field", "brier_model", "brier_field", "model", "field", "hit"],
        [
            [
                str(r["status"]),
                str(r["n"]),
                f"{float(r['skill_vs_field']):.2f}",
                f"{float(r['brier_model']):.4f}",
                f"{float(r['brier_field']):.4f}",
                f"{float(r['model']):.3f}",
                f"{float(r['field']):.3f}",
                f"{float(r['hit']):.3f}",
            ]
            for r in _status_summary(classified)
        ],
    )
    lines += ["", "## Overall Policy Replay", ""]
    lines += _md_table(
        ["policy", "n", "delta_vs_submitted", "brier", "mean_p"],
        [
            [
                str(r["policy"]),
                str(r["n"]),
                f"{float(r['delta']):.2f}",
                f"{float(r['brier']):.4f}",
                f"{float(r['mean_p']):.3f}",
            ]
            for r in sorted(_policy_summary(classified), key=lambda item: float(item["delta"]), reverse=True)
        ],
    )
    lines += ["", "## Policy Replay By Status", ""]
    by_status = _policy_by_status(classified)
    lines += _md_table(
        ["status", "policy", "n", "delta_vs_submitted", "brier", "mean_p"],
        [
            [
                status,
                str(r["policy"]),
                str(r["n"]),
                f"{float(r['delta']):.2f}",
                f"{float(r['brier']):.4f}",
                f"{float(r['mean_p']):.3f}",
            ]
            for status, policy_rows in by_status.items()
            for r in sorted(policy_rows, key=lambda item: float(item["delta"]), reverse=True)
        ],
    )
    examples = [
        row
        for row in classified
        if row.status.status in {"bench", "not_listed", "ambiguous", "team_mismatch"}
    ][:20]
    if examples:
        lines += [
            "",
            "## Non-Starter / Unresolved Examples",
            "",
            "| match | player | status | espn_match | model | field | outcome | question |",
            "| --- | --- | --- | --- | ---: | ---: | ---: | --- |",
        ]
        for row in examples:
            espn = row.status.espn_player or row.status.reason
            lines.append(
                f"| {row.row.match} | {row.player} | {row.status.status} | {espn} | "
                f"{row.row.you:.0%} | {row.row.crowd:.0%} | {row.row.outcome or 0} | {row.row.question} |"
            )
    if missing_contexts:
        lines += [
            "",
            "## Missing Match Contexts",
            "",
            *[f"- {item}" for item in missing_contexts[:30]],
        ]
    if skipped:
        lines += [
            "",
            "## Skipped Rows",
            "",
            *[f"- {item}" for item in skipped[:30]],
        ]
    lines += [
        "",
        "## Interpretation",
        "",
        "- Confirmed lineups are useful only if they change the probability with known direction; otherwise the promoted player field policy remains the safer default.",
        "- If bench/not-listed rows show negative model skill, the live workflow should use confirmed lineup status as a hard research flag and avoid model-above-field player entries for those rows.",
        "- Because the sample is small, lineup-status rules should be directional guardrails, not a new aggressively tuned policy.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sports-predict", action="store_true", help="GET-only settlement for older V1 history rows")
    ap.add_argument("--start", default="2026-06-11", help="first ESPN scoreboard date, YYYY-MM-DD")
    ap.add_argument("--end", default="2026-06-28", help="last ESPN scoreboard date, YYYY-MM-DD")
    ap.add_argument("--write", action="store_true", help=f"write {OUT}")
    args = ap.parse_args()

    rows = load_player_rows(sports_predict=args.sports_predict)
    contexts, missing_contexts = build_contexts(
        [row.match for row in rows],
        start=date.fromisoformat(args.start),
        end=date.fromisoformat(args.end),
    )
    classified, skipped = classify_rows(rows, contexts)
    text = render(
        rows,
        contexts,
        missing_contexts,
        classified,
        skipped,
        now=datetime.now(timezone.utc),
    )
    print(text)
    if args.write:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(text, encoding="utf-8")
        print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
