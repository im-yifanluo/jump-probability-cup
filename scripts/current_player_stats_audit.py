"""Audit current-tournament ESPN player stats for open player props.

This is a source/research report, not a promoted probability model. It uses
ESPN public scoreboard/summary JSON to aggregate completed tournament player
stats, then attaches a shrunk current-form source probability to current open
SportsPredict player props.

It does not read `/predictions` and never submits. With `--sports-predict`, the
SportsPredict API is used only through read-only open-market discovery.
"""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import math
from pathlib import Path
import re
import sys
import unicodedata

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from manual.anchors import fetch_sports_predict_open_markets, sports_predict_key  # noqa: E402
from questions.parser import MarketSpec, parse  # noqa: E402
from questions.teams import ALL_TEAM_NAMES, names_match  # noqa: E402


OUT = ROOT / "data" / "logs" / "current_player_stats_audit.md"
PLAYER_PROP_TEMPLATE = ROOT / "data" / "player_prop_anchors_template.csv"
SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard"
SUMMARY = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/summary"
SHRINK_APPEARANCES = 4.0


@dataclass(frozen=True)
class PlayerLine:
    event_id: str
    kickoff: datetime
    match: str
    team: str
    player: str
    position: str
    starter: bool
    subbed_in: bool
    stats: dict[str, float]


@dataclass(frozen=True)
class OpenPlayerMarket:
    market_id: str
    match: str
    kickoff: datetime
    question: str
    spec: MarketSpec


@dataclass(frozen=True)
class PlayerAggregate:
    player: str
    team: str
    appearances: int
    starts: int
    sub_ins: int
    totals: dict[str, float]


@dataclass(frozen=True)
class CurrentPlayerSource:
    market: OpenPlayerMarket
    aggregate: PlayerAggregate | None
    stat_key: str
    threshold: int
    global_mean: float
    player_mean: float | None
    shrunk_mean: float | None
    probability: float | None
    source_state: str
    manual_entry: str = ""
    raw_model: str = ""


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def _fold(value: str) -> str:
    folded = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    folded = re.sub(r"[^a-z0-9 ]+", " ", folded.lower())
    return re.sub(r"\s+", " ", folded).strip()


def _norm_name(value: str) -> str:
    return " ".join(sorted(_fold(value).split()))


def _surname(value: str) -> str:
    parts = _fold(value).split()
    return parts[-1] if parts else ""


def _float(raw: str | int | float | None) -> float:
    if raw is None:
        return 0.0
    text = str(raw).strip().replace("%", "")
    return float(text) if text else 0.0


def _event_name(event: dict) -> str:
    comp = event.get("competitions", [{}])[0]
    competitors = comp.get("competitors") or []
    home = next((c for c in competitors if c.get("homeAway") == "home"), None)
    away = next((c for c in competitors if c.get("homeAway") == "away"), None)
    if not home or not away:
        return str(event.get("name") or "")
    home_name = home.get("team", {}).get("displayName") or ""
    away_name = away.get("team", {}).get("displayName") or ""
    return f"{home_name} vs {away_name}"


def _is_completed(event: dict) -> bool:
    return bool(event.get("status", {}).get("type", {}).get("completed"))


def canonical_team(external: str) -> str:
    for team in ALL_TEAM_NAMES:
        if names_match(team, external):
            return team
    return external


def _player_stats(player: dict) -> dict[str, float]:
    return {
        str(item.get("name")): _float(item.get("value", item.get("displayValue")))
        for item in player.get("stats") or []
        if item.get("name")
    }


def fetch_completed_events(start: date, end: date, session: requests.Session) -> list[dict]:
    events: list[dict] = []
    seen: set[str] = set()
    cur = start
    while cur <= end:
        board = session.get(SCOREBOARD, params={"dates": cur.strftime("%Y%m%d")}, timeout=20).json()
        for event in board.get("events") or []:
            event_id = str(event.get("id") or "")
            if event_id and event_id not in seen and _is_completed(event):
                seen.add(event_id)
                events.append(event)
        cur += timedelta(days=1)
    return events


def extract_player_lines(event: dict, summary: dict) -> list[PlayerLine]:
    rosters = summary.get("rosters") or []
    if not rosters:
        return []
    kickoff = datetime.fromisoformat(str(event["date"]).replace("Z", "+00:00"))
    lines: list[PlayerLine] = []
    for team in rosters:
        team_name = canonical_team(team.get("team", {}).get("displayName") or "")
        for player in team.get("roster") or []:
            athlete = player.get("athlete") or {}
            name = athlete.get("displayName") or athlete.get("fullName") or ""
            if not name:
                continue
            stats = _player_stats(player)
            appeared = stats.get("appearances", 0.0) > 0
            if not appeared:
                continue
            position = (player.get("position") or {}).get("name") or ""
            lines.append(
                PlayerLine(
                    event_id=str(event.get("id") or ""),
                    kickoff=kickoff,
                    match=_event_name(event),
                    team=team_name,
                    player=name,
                    position=position,
                    starter=bool(player.get("starter")),
                    subbed_in=bool(player.get("subbedIn")) or stats.get("subIns", 0.0) > 0,
                    stats=stats,
                )
            )
    return lines


def fetch_player_lines(start: date, end: date, session: requests.Session | None = None) -> list[PlayerLine]:
    session = session or requests.Session()
    lines: list[PlayerLine] = []
    for event in fetch_completed_events(start, end, session):
        summary = session.get(SUMMARY, params={"event": event["id"]}, timeout=20).json()
        lines.extend(extract_player_lines(event, summary))
    return lines


def aggregate_player_lines(lines: list[PlayerLine]) -> tuple[dict[str, list[PlayerAggregate]], dict[str, float]]:
    grouped: dict[tuple[str, str], list[PlayerLine]] = defaultdict(list)
    global_totals = defaultdict(float)
    global_apps = 0
    for line in lines:
        if line.position.lower() == "goalkeeper":
            continue
        grouped[(_norm_name(line.player), line.team)].append(line)
        global_apps += 1
        global_totals["goals"] += line.stats.get("totalGoals", 0.0)
        global_totals["goal_involvements"] += line.stats.get("totalGoals", 0.0) + line.stats.get("goalAssists", 0.0)
        global_totals["sot"] += line.stats.get("shotsOnTarget", 0.0)
    aggregates: dict[str, list[PlayerAggregate]] = defaultdict(list)
    for (norm, team), part in grouped.items():
        totals = {
            "goals": sum(line.stats.get("totalGoals", 0.0) for line in part),
            "goal_involvements": sum(line.stats.get("totalGoals", 0.0) + line.stats.get("goalAssists", 0.0) for line in part),
            "sot": sum(line.stats.get("shotsOnTarget", 0.0) for line in part),
        }
        agg = PlayerAggregate(
            player=part[-1].player,
            team=team,
            appearances=len(part),
            starts=sum(1 for line in part if line.starter),
            sub_ins=sum(1 for line in part if line.subbed_in),
            totals=totals,
        )
        aggregates[norm].append(agg)
    global_means = {
        key: (value / global_apps if global_apps else 0.0)
        for key, value in global_totals.items()
    }
    return aggregates, global_means


def _open_markets_from_sports_predict() -> list[OpenPlayerMarket]:
    key = sports_predict_key()
    if not key:
        raise SystemExit("SportsPredict key missing from environment/.env")
    _matches, markets = fetch_sports_predict_open_markets(key)
    rows: list[OpenPlayerMarket] = []
    for market in markets:
        question = str(market.get("question") or "")
        try:
            spec = parse(question, teams=ALL_TEAM_NAMES)
        except Exception:
            continue
        if not spec.player:
            continue
        match = market.get("match") or {}
        rows.append(
            OpenPlayerMarket(
                market_id=str(market.get("id") or ""),
                match=str(match.get("name") or ""),
                kickoff=datetime.fromisoformat(str(match.get("opening_time")).replace("Z", "+00:00")),
                question=question,
                spec=spec,
            )
        )
    return sorted(rows, key=lambda row: (row.kickoff, row.match, row.question))


def read_manual_template(path: Path = PLAYER_PROP_TEMPLATE) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as handle:
        return {
            row.get("market_id", ""): row
            for row in csv.DictReader(handle)
            if row.get("market_id")
        }


def prop_stat_and_threshold(spec: MarketSpec) -> tuple[str, int] | None:
    if spec.family == "player_scores":
        return "goals", 1
    if spec.family == "player_scores_or_assists":
        return "goal_involvements", 1
    if spec.family == "player_sot_ge":
        return "sot", int(spec.threshold or 1)
    return None


def poisson_ge(threshold: int, mean: float) -> float:
    if threshold <= 0:
        return 1.0
    term = math.exp(-mean)
    total = term
    for k in range(1, threshold):
        term *= mean / k
        total += term
    return max(0.0, min(1.0, 1.0 - total))


def find_player_aggregate(
    market: OpenPlayerMarket,
    aggregates: dict[str, list[PlayerAggregate]],
) -> PlayerAggregate | None:
    candidates = aggregates.get(_norm_name(market.spec.player or ""), [])
    hint = market.spec.params.get("player_team") if market.spec.params else None
    if hint:
        hinted = [agg for agg in candidates if names_match(agg.team, hint)]
        if len(hinted) == 1:
            return hinted[0]
    if len(candidates) == 1:
        return candidates[0]

    surname = _surname(market.spec.player or "")
    surname_matches = [
        agg
        for aggs in aggregates.values()
        for agg in aggs
        if _surname(agg.player) == surname
    ]
    if hint:
        surname_matches = [agg for agg in surname_matches if names_match(agg.team, hint)]
    if len(surname_matches) == 1:
        return surname_matches[0]
    return None


def source_for_market(
    market: OpenPlayerMarket,
    aggregates: dict[str, list[PlayerAggregate]],
    global_means: dict[str, float],
    template_rows: dict[str, dict[str, str]] | None = None,
) -> CurrentPlayerSource:
    prop = prop_stat_and_threshold(market.spec)
    template = (template_rows or {}).get(market.market_id, {})
    if prop is None:
        return CurrentPlayerSource(
            market=market,
            aggregate=None,
            stat_key="unsupported",
            threshold=0,
            global_mean=0.0,
            player_mean=None,
            shrunk_mean=None,
            probability=None,
            source_state="UNSUPPORTED_PLAYER_PROP",
            manual_entry=template.get("manual_entry", ""),
            raw_model=template.get("raw_model_probability", ""),
        )
    stat_key, threshold = prop
    aggregate = find_player_aggregate(market, aggregates)
    global_mean = global_means.get(stat_key, 0.0)
    if aggregate is None:
        return CurrentPlayerSource(
            market=market,
            aggregate=None,
            stat_key=stat_key,
            threshold=threshold,
            global_mean=global_mean,
            player_mean=None,
            shrunk_mean=None,
            probability=None,
            source_state="NO_CURRENT_PLAYER_MATCH",
            manual_entry=template.get("manual_entry", ""),
            raw_model=template.get("raw_model_probability", ""),
        )
    player_mean = aggregate.totals.get(stat_key, 0.0) / aggregate.appearances if aggregate.appearances else 0.0
    shrunk = (
        aggregate.totals.get(stat_key, 0.0) + global_mean * SHRINK_APPEARANCES
    ) / (aggregate.appearances + SHRINK_APPEARANCES)
    probability = poisson_ge(threshold, shrunk)
    return CurrentPlayerSource(
        market=market,
        aggregate=aggregate,
        stat_key=stat_key,
        threshold=threshold,
        global_mean=global_mean,
        player_mean=player_mean,
        shrunk_mean=shrunk,
        probability=probability,
        source_state="CURRENT_PLAYER_STATS_SOURCE",
        manual_entry=template.get("manual_entry", ""),
        raw_model=template.get("raw_model_probability", ""),
    )


def build_sources(
    markets: list[OpenPlayerMarket],
    lines: list[PlayerLine],
    template_rows: dict[str, dict[str, str]] | None = None,
) -> tuple[list[CurrentPlayerSource], dict[str, float]]:
    aggregates, global_means = aggregate_player_lines(lines)
    sources = [
        source_for_market(market, aggregates, global_means, template_rows)
        for market in markets
    ]
    return sources, global_means


def _pct(value: float | None) -> str:
    return "" if value is None else f"{round(100 * value):d}%"


def _fmt_float(value: float | None) -> str:
    return "" if value is None else f"{value:.2f}"


def status_for_sources(markets: list[OpenPlayerMarket], lines: list[PlayerLine], sources: list[CurrentPlayerSource]) -> str:
    if not markets:
        return "NO_OPEN_PLAYER_ROWS"
    if not lines:
        return "NO_COMPLETED_PLAYER_STATS"
    matched = sum(1 for source in sources if source.source_state == "CURRENT_PLAYER_STATS_SOURCE")
    if matched == len(sources):
        return "COMPLETE"
    if matched > 0:
        return "PARTIAL"
    return "NO_MATCHED_CURRENT_PLAYER_STATS"


def render(
    sources: list[CurrentPlayerSource],
    *,
    lines: list[PlayerLine],
    global_means: dict[str, float],
    sports_predict: bool,
    now: datetime,
) -> str:
    status = status_for_sources([source.market for source in sources], lines, sources)
    state_counts = defaultdict(int)
    for source in sources:
        state_counts[source.source_state] += 1
    lines_out = [
        "# Current Player Stats Audit",
        "",
        "Read-only source analysis. `/predictions` was not read and no submissions were made.",
        "",
        f"- run_utc: {now.astimezone(timezone.utc).isoformat(timespec='seconds')}",
        f"- sports_predict: {'read_only_open_markets' if sports_predict else 'not_called'}",
        "- public_source: ESPN scoreboard + ESPN summary rosters/player stats",
        f"- current_player_stats_status: {status}",
        f"- completed_player_appearance_rows: {len(lines)}",
        f"- open_player_prop_rows: {len(sources)}",
        f"- shrink_strength_appearances: {SHRINK_APPEARANCES:g}",
        "",
        "## Global Current-Tournament Player Means",
        "",
        "| stat | mean_per_outfield_appearance |",
        "| --- | ---: |",
    ]
    for stat in ("goals", "goal_involvements", "sot"):
        lines_out.append(f"| {stat} | {global_means.get(stat, 0.0):.3f} |")
    lines_out.extend(["", "## Source State Summary", "", "| source_state | rows |", "| --- | ---: |"])
    for state, count in sorted(state_counts.items()):
        lines_out.append(f"| {state} | {count} |")
    lines_out.extend(
        [
            "",
            "## Open Player Prop Source Rows",
            "",
            "| match | kickoff_utc | player | prop | apps | starts | stat_total | player_mean | shrunk_mean | current_probability | manual_entry | raw_model | source_state | market_id | question |",
            "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | --- | --- | --- |",
        ]
    )
    for source in sources:
        agg = source.aggregate
        stat_total = agg.totals.get(source.stat_key, 0.0) if agg else None
        lines_out.append(
            f"| {source.market.match} | {source.market.kickoff.isoformat(timespec='minutes')} | "
            f"{source.market.spec.player or ''} | {source.stat_key}>={source.threshold} | "
            f"{agg.appearances if agg else 0} | {agg.starts if agg else 0} | {_fmt_float(stat_total)} | "
            f"{_fmt_float(source.player_mean)} | {_fmt_float(source.shrunk_mean)} | {_pct(source.probability)} | "
            f"{source.manual_entry} | {source.raw_model} | {source.source_state} | {source.market.market_id} | {source.market.question} |"
        )
    lines_out.extend(
        [
            "",
            "## Interpretation",
            "",
            "- This is current-player-form source evidence, not a promoted replacement for exact prop odds or confirmed lineups.",
            "- Probabilities use a simple Poisson threshold calculation with player means shrunk toward the current tournament outfield-player mean.",
            "- Rows with `NO_CURRENT_PLAYER_MATCH` need exact prop odds or lineup/manual research before being treated as resolved.",
            "",
        ]
    )
    return "\n".join(lines_out)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start-date", default="2026-06-11")
    ap.add_argument("--end-date", default=datetime.now(timezone.utc).date().isoformat())
    ap.add_argument("--sports-predict", action="store_true", help="Read open player prop markets from SportsPredict GET endpoints.")
    ap.add_argument("--write", action="store_true", help=f"write {OUT}")
    args = ap.parse_args()

    session = requests.Session()
    player_lines = fetch_player_lines(parse_date(args.start_date), parse_date(args.end_date), session)
    markets = _open_markets_from_sports_predict() if args.sports_predict else []
    sources, global_means = build_sources(markets, player_lines, read_manual_template())
    text = render(
        sources,
        lines=player_lines,
        global_means=global_means,
        sports_predict=args.sports_predict,
        now=datetime.now(timezone.utc),
    )
    print(text)
    if args.write:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(text, encoding="utf-8")
        print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
