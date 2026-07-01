"""Audit recent ESPN roster roles for open player props.

This report is for the manual-entry workflow. It uses read-only SportsPredict
GET market discovery and ESPN public scoreboard/summary JSON. It does not read
`/predictions` and never submits.

The goal is not to manufacture a new probability. It records whether current
open player props have recent starter/bench/not-listed evidence before the
final lineup is posted, so manual entries can treat stale minutes assumptions
as explicit risk.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import contextlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import io
from pathlib import Path
import re
import sys
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from manual.anchors import ESPN_SCOREBOARD, fetch_sports_predict_open_markets, get_json, sports_predict_key
from price_match import build_pricer
from pull_lineups import _index, _match
from questions.parser import parse
from questions.teams import ALL_TEAM_NAMES, names_match


ESPN_SUMMARY = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/summary"
LOG_PATH = ROOT / "data" / "logs" / "player_role_research_audit.md"


@dataclass(frozen=True)
class PlayerTarget:
    match: str
    kickoff: datetime
    market_id: str
    player: str
    team: str
    question: str
    flags: str
    model_probability: int


@dataclass(frozen=True)
class RoleObservation:
    kickoff: datetime
    event_name: str
    team: str
    role: str
    matched_name: str
    espn_event_id: str


def _clean_cell(value: str) -> str:
    return value.strip().strip("`")


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
    headers = [_clean_cell(part) for part in lines[start].strip().strip("|").split("|")]
    rows: list[dict[str, str]] = []
    for line in lines[start + 2 :]:
        if not line.lstrip().startswith("|"):
            break
        parts = [_clean_cell(part) for part in line.strip().strip("|").split("|")]
        if len(parts) == len(headers):
            rows.append(dict(zip(headers, parts)))
    return rows


def load_role_summary(path: Path = LOG_PATH) -> dict[str, dict[str, str]]:
    """Load the latest role audit by exact SportsPredict market id.

    The role audit is advisory. If the report is missing or from an older
    format without market ids, callers should simply continue without role
    annotations rather than inventing a match by fuzzy question text.
    """

    if not path.exists():
        return {}
    rows = _table_after(path.read_text(encoding="utf-8"), "Current Open Player Role Summary")
    out: dict[str, dict[str, str]] = {}
    for row in rows:
        market_id = (row.get("market_id") or "").strip()
        if market_id:
            out[market_id] = row
    return out


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _famkey(spec) -> str:
    out = spec.family
    if spec.stat:
        out += f"/{spec.stat}"
    if spec.period and spec.period != "match":
        out += f"/{spec.period}"
    return out


def _player_flags(match_name: str, kickoff: datetime, spec, pricer) -> str:
    flags: list[str] = []
    rates = pricer.players.rates(spec.player)
    if rates.source == "prior":
        flags.append("PRIOR_ONLY")
    elif rates.source == "statsbomb" and pricer.players.is_sparse(spec.player):
        flags.append("SPARSE_HISTORY")
    if pricer._player_side(spec.player, spec.params.get("player_team")) is None:
        flags.append("TEAM_UNKNOWN")
    # This script is a role audit. Exact final-lineup flags remain owned by
    # player_prop_diagnostics/full_manual_pricing, but unknown current lineups
    # should still be explicit in this report.
    flags.append("LINEUP_NOT_POSTED")
    return ",".join(flags)


def open_player_targets() -> list[PlayerTarget]:
    key = sports_predict_key()
    if not key:
        raise SystemExit("SportsPredict key missing from environment/.env")
    _matches, markets = fetch_sports_predict_open_markets(key)
    out: list[PlayerTarget] = []
    pricer_cache = {}
    for market in markets:
        question = market.get("question", "")
        try:
            spec = parse(question, teams=ALL_TEAM_NAMES)
        except Exception:
            continue
        if not spec.player:
            continue
        match = market.get("match") or {}
        match_name = match.get("name", "")
        kickoff = _dt(match.get("opening_time"))
        cache_key = (match_name, kickoff)
        if cache_key not in pricer_cache:
            # build_pricer prints odds anchors for interactive match pricing; this
            # audit records role evidence, so keep stdout clean and report-only.
            with contextlib.redirect_stdout(io.StringIO()):
                pricer_cache[cache_key] = build_pricer(
                    match_name,
                    kickoff,
                    include_global_scorer_props=False,
                    include_global_expected_minutes=False,
                )
        pricer = pricer_cache[cache_key]
        team = spec.params.get("player_team") or pricer.players.team_of(spec.player) or ""
        out.append(
            PlayerTarget(
                match=match_name,
                kickoff=kickoff,
                market_id=market.get("id", ""),
                player=spec.player,
                team=team,
                question=question,
                flags=_player_flags(match_name, kickoff, spec, pricer),
                model_probability=round(float(pricer.price(spec)) * 100),
            )
        )
    return sorted(out, key=lambda r: (r.kickoff, r.match, r.player, r.market_id))


def _event_team_names(event: dict) -> list[str]:
    competitions = event.get("competitions") or []
    if not competitions:
        return []
    return [c.get("team", {}).get("displayName", "") for c in competitions[0].get("competitors", [])]


def _team_roster(summary: dict, team: str) -> dict | None:
    for roster in summary.get("rosters") or []:
        espn_team = (roster.get("team") or {}).get("displayName") or (roster.get("team") or {}).get("name") or ""
        if names_match(team, espn_team):
            return roster
    return None


def _role_from_roster(player: str, roster: dict | None) -> tuple[str, str]:
    if not roster:
        return "no_roster", ""
    full, surname = _index([roster])
    rec = _match(player, full, surname)
    if rec is None:
        return "not_listed", ""
    display, starter = rec
    return ("starter" if starter else "bench"), display


def fetch_recent_role_observations(
    team: str,
    player: str,
    *,
    before: datetime,
    lookback_days: int,
    scoreboard_cache: dict[str, dict] | None = None,
    summary_cache: dict[str, dict] | None = None,
) -> list[RoleObservation]:
    if not team:
        return []
    scoreboard_cache = scoreboard_cache if scoreboard_cache is not None else {}
    summary_cache = summary_cache if summary_cache is not None else {}
    observations: list[RoleObservation] = []
    date = before - timedelta(days=lookback_days)
    seen_event_ids: set[str] = set()
    while date.date() <= before.date():
        date_key = date.strftime("%Y%m%d")
        if date_key not in scoreboard_cache:
            scoreboard_cache[date_key] = get_json(ESPN_SCOREBOARD, {"dates": date_key})
        board = scoreboard_cache[date_key]
        for event in board.get("events", []):
            event_id = str(event.get("id", ""))
            if not event_id or event_id in seen_event_ids:
                continue
            seen_event_ids.add(event_id)
            kickoff = _dt(event.get("date"))
            if kickoff >= before:
                continue
            if not any(names_match(team, name) for name in _event_team_names(event)):
                continue
            if event_id not in summary_cache:
                summary_cache[event_id] = get_json(ESPN_SUMMARY, {"event": event_id})
            summary = summary_cache[event_id]
            roster = _team_roster(summary, team)
            role, matched_name = _role_from_roster(player, roster)
            observations.append(
                RoleObservation(
                    kickoff=kickoff,
                    event_name=event.get("name", ""),
                    team=team,
                    role=role,
                    matched_name=matched_name,
                    espn_event_id=event_id,
                )
            )
        date += timedelta(days=1)
    return sorted(observations, key=lambda r: r.kickoff)


def classify_role(observations: list[RoleObservation]) -> str:
    useful = [obs for obs in observations if obs.role in {"starter", "bench", "not_listed"}]
    if not useful:
        return "unknown"
    starts = sum(obs.role == "starter" for obs in useful)
    last = useful[-1].role
    if last == "starter" and starts / len(useful) >= 0.67:
        return "likely_starter"
    if last != "starter":
        return "bench_or_rotation_risk"
    return "rotation_risk"


def role_action(target: PlayerTarget, role_class: str) -> str:
    flags = set(target.flags.split(",")) if target.flags else set()
    if role_class == "likely_starter":
        return "still recheck final XI; recent role supports starter minutes"
    if role_class in {"bench_or_rotation_risk", "rotation_risk"}:
        if "PRIOR_ONLY" in flags or "SPARSE_HISTORY" in flags:
            return "use exact prop odds unless final XI confirms starter role"
        return "do not rely on stale full-minutes prior; reprice after final XI or exact prop odds"
    return "no role evidence; use exact prop odds if available"


def build_report(*, lookback_days: int, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    targets = open_player_targets()
    by_key: dict[tuple[str, str, datetime], list[PlayerTarget]] = defaultdict(list)
    for target in targets:
        by_key[(target.team, target.player, target.kickoff)].append(target)

    role_cache: dict[tuple[str, str, datetime], list[RoleObservation]] = {}
    scoreboard_cache: dict[str, dict] = {}
    summary_cache: dict[str, dict] = {}
    for key in by_key:
        team, player, kickoff = key
        role_cache[key] = fetch_recent_role_observations(
            team,
            player,
            before=kickoff,
            lookback_days=lookback_days,
            scoreboard_cache=scoreboard_cache,
            summary_cache=summary_cache,
        )

    tz = ZoneInfo("America/Chicago")
    lines = [
        "# Player Role Research Audit",
        "",
        "- sports_predict: read_only GET /events, /lobbies, /matches, /markets only",
        "- bot_predictions: not_read",
        "- espn_sources: public scoreboard and summary JSON rosters",
        f"- run_utc: {now.isoformat(timespec='seconds')}",
        f"- lookback_days: {lookback_days}",
        "- promotion_rule: role history alone does not write player overrides; final lineups or exact prop odds dominate pre-lock manual entry because field averages are unavailable before lock",
        "",
        "## Current Open Player Role Summary",
        "",
        "| kickoff_local | match | market_id | player | team | model | role_class | recent_roles | action | question |",
        "| --- | --- | --- | --- | --- | ---: | --- | --- | --- | --- |",
    ]
    for target in targets:
        key = (target.team, target.player, target.kickoff)
        observations = role_cache.get(key, [])
        role_class = classify_role(observations)
        recent_roles = ", ".join(
            f"{obs.kickoff.astimezone(tz):%m-%d}:{obs.role}"
            for obs in observations
            if obs.role != "no_roster"
        ) or "-"
        lines.append(
            f"| {target.kickoff.astimezone(tz):%Y-%m-%d %H:%M %Z} | {target.match} | "
            f"{target.market_id} | {target.player} | {target.team or '-'} | {target.model_probability}% | "
            f"{role_class} | {recent_roles} | {role_action(target, role_class)} | "
            f"{target.question.replace('|', '/')} |"
        )

    lines.extend(
        [
            "",
            "## Role Observations",
            "",
            "| player | team | event_local | event | role | matched_name | espn_event_id |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    detail_rows = []
    for target in targets:
        key = (target.team, target.player, target.kickoff)
        for obs in role_cache.get(key, []):
            detail_rows.append((target.player, obs))
    for player, obs in sorted(detail_rows, key=lambda r: (r[1].kickoff, r[1].team, r[0])):
        lines.append(
            f"| {player} | {obs.team} | {obs.kickoff.astimezone(tz):%Y-%m-%d %H:%M %Z} | "
            f"{obs.event_name.replace('|', '/')} | {obs.role} | {obs.matched_name} | {obs.espn_event_id} |"
        )
    if not detail_rows:
        lines.append("| - | - | - | - | no observations | - | - |")
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--lookback-days", type=int, default=21)
    ap.add_argument("--write", action="store_true", help=f"write {LOG_PATH}")
    ap.add_argument("--log", type=Path, default=LOG_PATH, help="log path")
    args = ap.parse_args()

    report = build_report(lookback_days=args.lookback_days)
    print(report)
    if args.write:
        args.log.parent.mkdir(parents=True, exist_ok=True)
        args.log.write_text(report, encoding="utf-8")
        print(f"wrote {args.log}")


if __name__ == "__main__":
    main()
