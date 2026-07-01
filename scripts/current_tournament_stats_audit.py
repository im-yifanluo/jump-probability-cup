"""Audit current-tournament count evidence from ESPN box scores.

This is a live-source research report for manual entries. It uses ESPN public
scoreboard/summary JSON to extract completed 2026 tournament box-score counts
and compares those current-tournament rates to open SportsPredict count-like
markets.

It does not read `/predictions` and never submits. When `--sports-predict` is
used, SportsPredict is only called through the approved read-only open-market
discovery path.
"""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import math
from pathlib import Path
import sys

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from manual.anchors import fetch_sports_predict_open_markets, sports_predict_key  # noqa: E402
from questions.parser import parse  # noqa: E402
from questions.teams import ALL_TEAM_NAMES, match_teams, names_match  # noqa: E402

OUT = ROOT / "data" / "logs" / "current_tournament_stats_audit.md"
SPECIAL_COUNT_SOURCE_CSV = ROOT / "data" / "special_count_sources_template.csv"
SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard"
SUMMARY = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/summary"

STAT_LABELS = {
    "shots": "totalShots",
    "sot": "shotsOnTarget",
    "corners": "wonCorners",
    "fouls": "foulsCommitted",
    "offsides": "offsides",
    "yellow_cards": "yellowCards",
    "red_cards": "redCards",
}
COUNT_STATS = ("shots", "sot", "corners", "fouls", "offsides", "cards", "red_cards")
SHRINK_STRENGTH = 4.0


@dataclass(frozen=True)
class TeamLine:
    event_id: str
    kickoff: datetime
    match: str
    team: str
    opponent: str
    stats: dict[str, float]


@dataclass(frozen=True)
class OpenMarket:
    market_id: str
    match: str
    kickoff: datetime
    question: str


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def _float_value(raw: str | int | float | None) -> float:
    if raw is None:
        return 0.0
    text = str(raw).strip().replace("%", "")
    if not text:
        return 0.0
    return float(text)


def _stats_from_team(team: dict) -> dict[str, float]:
    by_name = {
        item.get("name"): _float_value(item.get("displayValue"))
        for item in team.get("statistics") or []
    }
    out = {stat: by_name.get(field, 0.0) for stat, field in STAT_LABELS.items()}
    out["cards"] = out["yellow_cards"] + out["red_cards"]
    return out


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
    return bool(
        event.get("status", {})
        .get("type", {})
        .get("completed")
    )


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


def extract_team_lines(event: dict, summary: dict) -> list[TeamLine]:
    teams = (summary.get("boxscore") or {}).get("teams") or []
    if len(teams) != 2:
        return []
    kickoff = datetime.fromisoformat(str(event["date"]).replace("Z", "+00:00"))
    raw_names = [
        team.get("team", {}).get("displayName") or team.get("team", {}).get("name") or ""
        for team in teams
    ]
    lines: list[TeamLine] = []
    for idx, team in enumerate(teams):
        name = raw_names[idx]
        opponent = raw_names[1 - idx]
        lines.append(
            TeamLine(
                event_id=str(event.get("id") or ""),
                kickoff=kickoff,
                match=_event_name(event),
                team=name,
                opponent=opponent,
                stats=_stats_from_team(team),
            )
        )
    return lines


def fetch_team_lines(start: date, end: date, session: requests.Session | None = None) -> list[TeamLine]:
    session = session or requests.Session()
    lines: list[TeamLine] = []
    for event in fetch_completed_events(start, end, session):
        summary = session.get(SUMMARY, params={"event": event["id"]}, timeout=20).json()
        lines.extend(extract_team_lines(event, summary))
    return lines


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


@dataclass(frozen=True)
class StatAggregate:
    team_match_rows: int
    match_rows: int
    global_for_mean: dict[str, float]
    global_total_mean: dict[str, float]
    team_for: dict[str, dict[str, float]]
    team_against: dict[str, dict[str, float]]
    team_matches: dict[str, int]


def canonical_team_name(external: str) -> str:
    for team in ALL_TEAM_NAMES:
        if names_match(team, external):
            return team
    return external


def aggregate_lines(lines: list[TeamLine]) -> StatAggregate:
    team_values: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    against_values: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    all_values: dict[str, list[float]] = defaultdict(list)
    by_event: dict[str, list[TeamLine]] = defaultdict(list)

    for line in lines:
        team = canonical_team_name(line.team)
        opponent = canonical_team_name(line.opponent)
        by_event[line.event_id].append(line)
        for stat in COUNT_STATS:
            value = line.stats.get(stat, 0.0)
            team_values[team][stat].append(value)
            against_values[opponent][stat].append(value)
            all_values[stat].append(value)

    total_values: dict[str, list[float]] = defaultdict(list)
    for event_lines in by_event.values():
        if len(event_lines) != 2:
            continue
        for stat in COUNT_STATS:
            total_values[stat].append(sum(line.stats.get(stat, 0.0) for line in event_lines))

    team_for = {
        team: {stat: _mean(values) for stat, values in stat_values.items()}
        for team, stat_values in team_values.items()
    }
    team_against = {
        team: {stat: _mean(values) for stat, values in stat_values.items()}
        for team, stat_values in against_values.items()
    }
    return StatAggregate(
        team_match_rows=len(lines),
        match_rows=len(by_event),
        global_for_mean={stat: _mean(values) for stat, values in all_values.items()},
        global_total_mean={stat: _mean(values) for stat, values in total_values.items()},
        team_for=team_for,
        team_against=team_against,
        team_matches={team: len(next(iter(stat_values.values()), [])) for team, stat_values in team_values.items()},
    )


def shrink(value: float, global_value: float, n: int, strength: float = SHRINK_STRENGTH) -> float:
    return (value * n + global_value * strength) / (n + strength) if n + strength > 0 else global_value


def team_expected_mean(team: str, opponent: str, stat: str, agg: StatAggregate) -> float:
    global_mean = agg.global_for_mean.get(stat, 0.0)
    team_n = agg.team_matches.get(team, 0)
    opp_n = agg.team_matches.get(opponent, 0)
    team_for = shrink(agg.team_for.get(team, {}).get(stat, global_mean), global_mean, team_n)
    opp_against = shrink(agg.team_against.get(opponent, {}).get(stat, global_mean), global_mean, opp_n)
    return (team_for + opp_against) / 2


def matchup_means(match: str, stat: str, agg: StatAggregate) -> dict[str, float]:
    left, right = match_teams(match)
    left_mean = team_expected_mean(left, right, stat, agg)
    right_mean = team_expected_mean(right, left, stat, agg)
    return {
        "left": left_mean,
        "right": right_mean,
        "total": left_mean + right_mean,
        "left_team": left,
        "right_team": right,
    }


def poisson_cdf(k: int, mean: float) -> float:
    if k < 0:
        return 0.0
    term = math.exp(-mean)
    total = term
    for i in range(1, k + 1):
        term *= mean / i
        total += term
    return min(1.0, total)


def poisson_ge(mean: float, threshold: int) -> float:
    return 1.0 - poisson_cdf(threshold - 1, mean)


def poisson_more(mean_a: float, mean_b: float, cap: int = 60) -> float:
    pmf_a = []
    term = math.exp(-mean_a)
    pmf_a.append(term)
    for i in range(1, cap + 1):
        term *= mean_a / i
        pmf_a.append(term)
    pmf_b = []
    term = math.exp(-mean_b)
    pmf_b.append(term)
    for i in range(1, cap + 1):
        term *= mean_b / i
        pmf_b.append(term)
    cum_b = []
    running = 0.0
    for value in pmf_b:
        running += value
        cum_b.append(running)
    return sum(pmf_a[i] * (cum_b[i - 1] if i else 0.0) for i in range(len(pmf_a)))


def probability_for_market(match: str, question: str, agg: StatAggregate) -> tuple[float | None, float | None, str]:
    spec = parse(question, teams=ALL_TEAM_NAMES)
    if spec.stat not in COUNT_STATS:
        return None, None, "not_count_stat"
    means = matchup_means(match, spec.stat, agg)
    if spec.family == "total_count_ge":
        mean = means["total"]
        return mean, poisson_ge(mean, spec.threshold or 0), "total_mean_poisson"
    if spec.family == "team_count_ge" and spec.team:
        team = means["left"] if names_match(means["left_team"], spec.team) else means["right"]
        return team, poisson_ge(team, spec.threshold or 0), "team_mean_poisson"
    if spec.family == "both_teams_count_ge":
        threshold = spec.threshold or 1
        prob = poisson_ge(means["left"], threshold) * poisson_ge(means["right"], threshold)
        return means["total"], prob, "both_teams_independent_poisson"
    if spec.family == "team_more_count_than" and spec.team:
        if names_match(means["left_team"], spec.team):
            return means["left"] - means["right"], poisson_more(means["left"], means["right"]), "team_more_poisson"
        return means["right"] - means["left"], poisson_more(means["right"], means["left"]), "team_more_poisson"
    return None, None, "unsupported_count_family"


def read_open_markets() -> list[OpenMarket]:
    key = sports_predict_key()
    if not key:
        raise SystemExit("SportsPredict key missing from environment/.env")
    _matches, markets = fetch_sports_predict_open_markets(key)
    out = []
    for market in markets:
        match = market.get("match") or {}
        out.append(
            OpenMarket(
                market_id=str(market.get("id") or ""),
                match=str(match.get("name") or ""),
                kickoff=datetime.fromisoformat(str(match.get("opening_time")).replace("Z", "+00:00")),
                question=str(market.get("question") or ""),
            )
        )
    return sorted(out, key=lambda row: (row.kickoff, row.match, row.question))


def current_open_count_rows(markets: list[OpenMarket], agg: StatAggregate) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for market in markets:
        try:
            spec = parse(market.question, teams=ALL_TEAM_NAMES)
        except Exception:
            continue
        if spec.stat not in COUNT_STATS:
            continue
        mean, probability, method = probability_for_market(market.match, market.question, agg)
        if probability is None:
            continue
        rows.append(
            {
                "market_id": market.market_id,
                "match": market.match,
                "kickoff": market.kickoff,
                "family": spec.family,
                "stat": spec.stat,
                "threshold": spec.threshold or "",
                "mean": mean,
                "probability": probability,
                "method": method,
                "question": market.question,
            }
        )
    return rows


def update_special_count_source_csv(path: Path, source_rows: list[dict[str, object]]) -> int:
    if not path.exists():
        return 0
    by_id = {str(row["market_id"]): row for row in source_rows}
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        columns = list(reader.fieldnames or [])
        rows = list(reader)
    changed = 0
    for row in rows:
        source = by_id.get((row.get("market_id") or "").strip())
        if not source:
            continue
        if row.get("source_1"):
            continue
        row["source_1"] = "ESPN current tournament boxscore shrink"
        row["source_1_probability"] = f"{float(source['probability']):.3f}"
        if float(source["mean"]) >= 0:
            row["source_1_count_mean"] = f"{float(source['mean']):.2f}"
        note = row.get("notes", "")
        addition = f"ESPN boxscore source method={source['method']}; still needs independent second source."
        row["notes"] = f"{note}; {addition}".strip("; ")
        changed += 1
    if changed:
        with path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)
    return changed


def status_for(agg: StatAggregate, source_rows: list[dict[str, object]], *, sports_predict: bool) -> str:
    if agg.match_rows == 0:
        return "NO_COMPLETED_MATCH_STATS"
    if sports_predict and not source_rows:
        return "NO_OPEN_COUNT_ROWS"
    return "COMPLETE"


def render(lines: list[TeamLine], agg: StatAggregate, source_rows: list[dict[str, object]], *, sports_predict: bool, updated_csv_rows: int) -> str:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    status = status_for(agg, source_rows, sports_predict=sports_predict)
    source_counts = defaultdict(int)
    for row in source_rows:
        source_counts[str(row["stat"])] += 1
    out = [
        "# Current Tournament Stats Audit",
        "",
        "Read-only analysis. SportsPredict is used only for GET open-market discovery when requested. `/predictions` was not read and no submissions were made.",
        "",
        f"- run_utc: {now}",
        f"- sports_predict: {'read_only_open_markets' if sports_predict else 'not_called'}",
        "- public_source: ESPN scoreboard + ESPN summary boxscore",
        f"- current_tournament_stats_status: {status}",
        f"- completed_matches: {agg.match_rows}",
        f"- team_match_rows: {agg.team_match_rows}",
        f"- shrink_strength_team_matches: {SHRINK_STRENGTH:.1f}",
        f"- open_count_source_rows: {len(source_rows)}",
        f"- special_count_csv_source1_rows_written: {updated_csv_rows}",
        "",
        "## Current Tournament Means",
        "",
        "| stat | team-match mean | match-total mean |",
        "| --- | ---: | ---: |",
    ]
    for stat in COUNT_STATS:
        out.append(
            f"| {stat} | {agg.global_for_mean.get(stat, 0.0):.2f} | {agg.global_total_mean.get(stat, 0.0):.2f} |"
        )
    out.extend(
        [
            "",
            "## Open Count Source Rows",
            "",
            "| match | kickoff_utc | stat | family | threshold | espn_mean | espn_probability | method | market_id | question |",
            "| --- | --- | --- | --- | ---: | ---: | ---: | --- | --- | --- |",
        ]
    )
    for row in source_rows:
        threshold = row["threshold"] if row["threshold"] != "" else "-"
        out.append(
            f"| {row['match']} | {row['kickoff'].isoformat(timespec='minutes')} | {row['stat']} | "
            f"{row['family']} | {threshold} | {float(row['mean']):.2f} | "
            f"{float(row['probability']):.3f} | {row['method']} | {row['market_id']} | {row['question']} |"
        )
    if not source_rows:
        out.append("| - | - | - | - | - | - | - | - | - | - |")
    out.extend(
        [
            "",
            "## Interpretation",
            "",
            "- This is a current-tournament source check, not a promoted replacement for the pricing model.",
            "- Team rates are shrunk toward the current tournament mean because each team has only a small number of completed matches.",
            "- Source rows can clear one research source for count-threshold markets; high-risk rows still need a second independent source before promotion.",
        ]
    )
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start-date", default="2026-06-11")
    ap.add_argument("--end-date", default=datetime.now(timezone.utc).date().isoformat())
    ap.add_argument("--sports-predict", action="store_true", help="Attach current open SportsPredict count markets.")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--update-special-count-csv", action="store_true")
    ap.add_argument("--special-count-csv", default=str(SPECIAL_COUNT_SOURCE_CSV))
    args = ap.parse_args()

    lines = fetch_team_lines(parse_date(args.start_date), parse_date(args.end_date))
    agg = aggregate_lines(lines)
    source_rows: list[dict[str, object]] = []
    if args.sports_predict:
        source_rows = current_open_count_rows(read_open_markets(), agg)
    updated = 0
    if args.update_special_count_csv:
        updated = update_special_count_source_csv(Path(args.special_count_csv), source_rows)
    text = render(lines, agg, source_rows, sports_predict=args.sports_predict, updated_csv_rows=updated)
    print(text)
    if args.write:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(text + "\n")
        print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
