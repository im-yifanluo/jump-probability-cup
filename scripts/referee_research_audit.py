"""Audit live referee availability for open SportsPredict card markets.

This is a read-only manual-entry tool. It uses SportsPredict only for GET
market discovery, checks ESPN's public scoreboard/summary payload for verified
officials, and writes a report explaining whether any referee/card adjustment is
evidence-backed for the currently open slate.

It intentionally does not read `/predictions` and never submits probabilities.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from manual.anchors import ESPN_SCOREBOARD, fetch_sports_predict_open_markets, get_json, sports_predict_key
from questions.teams import match_teams, names_match


ESPN_SUMMARY = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/summary"
REFEREE_OVERRIDES = ROOT / "data" / "referee_overrides.yaml"
LOG_PATH = ROOT / "data" / "logs" / "referee_research_audit.md"
TOURNAMENT_CARDS_PER_MATCH = 3.46


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def is_card_market(question: str) -> bool:
    text = question.lower()
    return "card" in text or "red card" in text


def summarize_local_override(match_name: str) -> tuple[str, str]:
    if not REFEREE_OVERRIDES.exists():
        return "no_file", "-"
    data = yaml.safe_load(REFEREE_OVERRIDES.read_text()) or {}
    entry = data.get(match_name)
    if not entry or "cards_per_game" not in entry:
        return "no_override", "-"
    cards = float(entry["cards_per_game"])
    factor = min(1.0, max(0.6, cards / TOURNAMENT_CARDS_PER_MATCH))
    ref = entry.get("referee", "unknown")
    return "override", f"{ref}; {cards:.2f} cards/g; factor {factor:.2f}"


def _extract_names(value) -> list[str]:
    names: list[str] = []
    if isinstance(value, list):
        for item in value:
            names.extend(_extract_names(item))
    elif isinstance(value, dict):
        for key in ("displayName", "fullName", "name", "shortName"):
            val = value.get(key)
            if isinstance(val, str) and val.strip():
                names.append(val.strip())
                break
        for child_key in ("officials", "referees", "referee"):
            child = value.get(child_key)
            if child:
                names.extend(_extract_names(child))
    elif isinstance(value, str) and value.strip() and len(value) < 120:
        names.append(value.strip())
    out: list[str] = []
    seen = set()
    for name in names:
        if name not in seen:
            out.append(name)
            seen.add(name)
    return out


def extract_summary_officials(summary: dict) -> list[str]:
    candidates = []
    header = summary.get("header") or {}
    for comp in header.get("competitions") or []:
        candidates.extend(_extract_names(comp.get("officials") or []))
    candidates.extend(_extract_names(summary.get("officials") or []))
    candidates.extend(_extract_names((summary.get("gameInfo") or {}).get("officials") or []))
    out: list[str] = []
    seen = set()
    for name in candidates:
        if name not in seen:
            out.append(name)
            seen.add(name)
    return out


def official_like_paths(value, path: str = "$") -> list[str]:
    paths: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            lower = key.lower()
            if "official" in lower or "referee" in lower:
                paths.append(child_path)
            paths.extend(official_like_paths(child, child_path))
    elif isinstance(value, list):
        for idx, child in enumerate(value):
            paths.extend(official_like_paths(child, f"{path}[{idx}]"))
    return paths


def fetch_espn_summary(match_name: str, kickoff: datetime) -> tuple[str, dict | None, str]:
    home_full, away_full = match_teams(match_name)
    dates = [
        kickoff.strftime("%Y%m%d"),
        (kickoff - timedelta(days=1)).strftime("%Y%m%d"),
        (kickoff + timedelta(days=1)).strftime("%Y%m%d"),
    ]
    seen_dates: set[str] = set()
    for date in dates:
        if date in seen_dates:
            continue
        seen_dates.add(date)
        board = get_json(ESPN_SCOREBOARD, {"dates": date})
        for event in board.get("events", []):
            competitions = event.get("competitions") or []
            if not competitions:
                continue
            competitors = competitions[0].get("competitors") or []
            team_names = [c.get("team", {}).get("displayName", "") for c in competitors]
            if (
                any(names_match(home_full, team) for team in team_names)
                and any(names_match(away_full, team) for team in team_names)
            ):
                summary = get_json(ESPN_SUMMARY, {"event": event["id"]})
                return "found", summary, f"espn_event_id={event['id']}"
    return "no_espn_event", None, "-"


def decision(local_status: str, officials: list[str], card_markets: int) -> str:
    if card_markets == 0:
        return "no_open_card_markets"
    if local_status == "override":
        return "use_existing_capped_override"
    if officials:
        return "needs_referee_card_rate_research_before_override"
    return "no_verified_referee; keep neutral card_factor=1.00; field is unavailable before lock"


def build_report(now: datetime | None = None) -> str:
    key = sports_predict_key()
    if not key:
        raise SystemExit("No SportsPredict key found in environment or .env")

    now = now or datetime.now(timezone.utc)
    matches, markets = fetch_sports_predict_open_markets(key)
    open_markets = [m for m in markets if m.get("status") == "open"]
    matches_by_id = {m["id"]: m for m in matches}
    open_by_match: dict[str, list[dict]] = defaultdict(list)
    cards_by_match: dict[str, list[dict]] = defaultdict(list)

    for market in open_markets:
        match = market.get("match") or {}
        match_id = match.get("id")
        match_row = matches_by_id.get(match_id, match)
        match_name = match_row.get("name") or match.get("name") or "unknown"
        open_by_match[match_name].append(market)
        if is_card_market(market.get("question", "")):
            cards_by_match[match_name].append(market)

    tz = ZoneInfo("America/Chicago")
    rows = []
    for match_name, part in open_by_match.items():
        match_id = (part[0].get("match") or {}).get("id")
        match_row = matches_by_id.get(match_id, part[0].get("match") or {})
        kickoff = _dt(match_row["opening_time"])
        local_status, local_detail = summarize_local_override(match_name)
        espn_status, summary, espn_detail = fetch_espn_summary(match_name, kickoff)
        officials = extract_summary_officials(summary or {})
        rows.append(
            {
                "kickoff": kickoff,
                "match": match_name,
                "card_markets": len(cards_by_match.get(match_name, [])),
                "local_status": local_status,
                "local_detail": local_detail,
                "espn_status": espn_status,
                "espn_detail": espn_detail,
                "officials": officials,
                "official_paths": official_like_paths(summary or {}),
                "decision": decision(local_status, officials, len(cards_by_match.get(match_name, []))),
            }
        )

    lines = [
        "# Referee Research Audit",
        "",
        "- sports_predict: read_only GET /events, /lobbies, /matches, /markets only",
        "- bot_predictions: not_read",
        f"- run_utc: {now.isoformat(timespec='seconds')}",
        "- espn_sources: public scoreboard and summary JSON",
        "- promotion_rule: do not add or change referee overrides unless a current match official is verified and an independent cards-per-game estimate is researched; strict-ref overrides remain capped at card_factor=1.00",
        "- card_policy: field averages are unavailable before lock; without verified current referee context, no-field card rows keep the neutral card model",
        "",
        "## Open Match Referee Availability",
        "",
        "| kickoff_local | match | card_markets | local_override | espn_status | espn_officials | decision |",
        "| --- | --- | ---: | --- | --- | --- | --- |",
    ]
    for row in sorted(rows, key=lambda r: (r["kickoff"], r["match"])):
        kickoff_local = row["kickoff"].astimezone(tz).strftime("%Y-%m-%d %H:%M %Z")
        officials = ", ".join(row["officials"]) if row["officials"] else "-"
        local = row["local_status"] if row["local_detail"] == "-" else f"{row['local_status']}: {row['local_detail']}"
        lines.append(
            f"| {kickoff_local} | {row['match']} | {row['card_markets']} | {local} | "
            f"{row['espn_status']} | {officials} | {row['decision']} |"
        )

    lines.extend(
        [
            "",
            "## Open Card Markets",
            "",
            "| kickoff_local | match | market_id | question |",
            "| --- | --- | --- | --- |",
        ]
    )
    card_rows = []
    for match_name, part in cards_by_match.items():
        match_id = (part[0].get("match") or {}).get("id")
        match_row = matches_by_id.get(match_id, part[0].get("match") or {})
        kickoff = _dt(match_row["opening_time"])
        for market in part:
            card_rows.append((kickoff, match_name, market))
    for kickoff, match_name, market in sorted(card_rows, key=lambda r: (r[0], r[1], r[2].get("id", ""))):
        kickoff_local = kickoff.astimezone(tz).strftime("%Y-%m-%d %H:%M %Z")
        lines.append(
            f"| {kickoff_local} | {match_name} | {market.get('id')} | "
            f"{market.get('question', '').replace('|', '/')} |"
        )
    if not card_rows:
        lines.append("| - | - | - | no open card markets |")

    lines.extend(
        [
            "",
            "## ESPN Official-Like JSON Paths",
            "",
            "These paths are diagnostic only. Empty or missing official-like paths are not evidence of a referee appointment.",
            "",
            "| match | paths |",
            "| --- | --- |",
        ]
    )
    for row in sorted(rows, key=lambda r: (r["kickoff"], r["match"])):
        paths = ", ".join(row["official_paths"]) if row["official_paths"] else "-"
        lines.append(f"| {row['match']} | {paths} |")

    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write", action="store_true", help=f"write {LOG_PATH}")
    ap.add_argument("--log", type=Path, default=LOG_PATH, help="log path")
    args = ap.parse_args()

    report = build_report()
    print(report)
    if args.write:
        args.log.parent.mkdir(parents=True, exist_ok=True)
        args.log.write_text(report)
        print(f"wrote {args.log}")


if __name__ == "__main__":
    main()
