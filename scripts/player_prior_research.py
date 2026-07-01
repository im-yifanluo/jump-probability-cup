"""Structured research pass for PRIOR_ONLY open player props.

This is part of the manual-entry workflow. It uses only read-only SportsPredict
GET endpoints to discover open player markets, then API-Football season-rate
lookups to replace unsupported global player priors with auditable club-rate
evidence. It never reads `/predictions` and never submits.

Default mode is report-only. Pass `--apply` to write high-confidence API
matches into `data/player_overrides.yaml`.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import yaml

from manual.anchors import fetch_sports_predict_open_markets, sports_predict_key
from price_match import build_pricer
from questions.parser import parse
from questions.teams import ALL_TEAM_NAMES, match_teams
from wcdata.api_football import ApiFootball


OUT = ROOT / "data" / "logs" / "player_prior_research.md"
OVERRIDES = ROOT / "data" / "player_overrides.yaml"
CLUB_TEAM_GOALS = 1.45
MIN_APPS = 10


@dataclass(frozen=True)
class ResearchTarget:
    match: str
    kickoff: datetime
    market_id: str
    player: str
    team_hint: str
    family: str
    question: str


def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text()) or {} if path.exists() else {}


def _famkey(spec) -> str:
    out = spec.family
    if spec.stat:
        out += f"/{spec.stat}"
    if spec.period and spec.period != "match":
        out += f"/{spec.period}"
    return out


def open_prior_targets() -> list[ResearchTarget]:
    key = sports_predict_key()
    if not key:
        raise SystemExit("SportsPredict key missing from environment/.env")
    _matches, markets = fetch_sports_predict_open_markets(key)
    out: list[ResearchTarget] = []
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
        kickoff = datetime.fromisoformat(match.get("opening_time").replace("Z", "+00:00"))
        pricer = build_pricer(match_name, kickoff)
        rates = pricer.players.rates(spec.player)
        if rates.source != "prior":
            continue
        out.append(
            ResearchTarget(
                match=match_name,
                kickoff=kickoff,
                market_id=market.get("id", ""),
                player=spec.player,
                team_hint=spec.params.get("player_team") or "",
                family=_famkey(spec),
                question=question,
            )
        )
    return sorted(out, key=lambda r: (r.kickoff, r.match, r.player, r.market_id))


def candidate_nationalities(target: ResearchTarget) -> set[str]:
    if target.team_hint:
        return {target.team_hint}
    try:
        return set(match_teams(target.match))
    except Exception:
        return set()


def override_entry(target: ResearchTarget, rates: dict) -> dict:
    return {
        "team": target.team_hint or rates.get("nationality") or "",
        "sot_per_match": rates["sot_per_match"],
        "goal_share": round(rates["goals_per_match"] / CLUB_TEAM_GOALS, 3),
        "involvement_share": round(rates["involvements_per_match"] / CLUB_TEAM_GOALS, 3),
        "source": (
            f"api-football {rates['season']} ({rates['apps']} apps, "
            f"matched {rates['matched_name']} #{rates['player_id']}): "
            f"{rates['sot_per_match']:.3f} SOT/match, "
            f"{rates['goals_per_match']:.3f} goals/match, "
            f"{rates['involvements_per_match']:.3f} G+A/match -> shares use "
            f"/{CLUB_TEAM_GOALS:.2f} club-goal denominator. Updated 2026-06-28 "
            "from player_prior_research; lineup minutes still require confirmed XI."
        ),
    }


def run_research(*, apply: bool) -> tuple[str, list[dict]]:
    targets = open_prior_targets()
    api = ApiFootball()
    overrides = _load_yaml(OVERRIDES)
    rows: list[dict] = []
    written: list[str] = []
    for target in targets:
        nationalities = candidate_nationalities(target)
        row = {
            "status": "unresolved",
            "match": target.match,
            "kickoff": target.kickoff,
            "market_id": target.market_id,
            "player": target.player,
            "team_hint": target.team_hint,
            "nationalities": ", ".join(sorted(nationalities)),
            "family": target.family,
            "question": target.question,
            "matched": "",
            "apps": "",
            "sot_per_match": "",
            "goals_per_match": "",
            "involvements_per_match": "",
            "action": "leave PRIOR_ONLY",
            "reason": "",
        }
        try:
            rates = api.player_season_rates(target.player, nationalities)
        except Exception as exc:
            row["reason"] = f"api_error: {exc}"
            rows.append(row)
            continue
        if rates is None:
            row["reason"] = "no acceptable API-Football profile/stat match"
            rows.append(row)
            continue
        row.update(
            {
                "matched": f"{rates['matched_name']} #{rates['player_id']} ({rates.get('nationality') or ''})",
                "apps": rates["apps"],
                "sot_per_match": rates["sot_per_match"],
                "goals_per_match": rates["goals_per_match"],
                "involvements_per_match": rates["involvements_per_match"],
            }
        )
        if int(rates["apps"]) < MIN_APPS:
            row["reason"] = f"sample below MIN_APPS={MIN_APPS}"
            rows.append(row)
            continue
        entry = override_entry(target, rates)
        row["status"] = "accepted"
        row["action"] = "write override" if apply else "candidate override; rerun with --apply to promote"
        row["reason"] = entry["source"]
        rows.append(row)
        if apply:
            overrides[target.player] = {**(overrides.get(target.player) or {}), **entry}
            written.append(target.player)

    if apply and written:
        OVERRIDES.write_text(yaml.safe_dump(overrides, allow_unicode=True, sort_keys=True), encoding="utf-8")

    return render(rows, written=written, apply=apply), rows


def render(rows: list[dict], *, written: list[str], apply: bool, tz_name: str = "America/Chicago") -> str:
    tz = ZoneInfo(tz_name)
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    lines = [
        "# Player Prior Research",
        "",
        "Read-only SportsPredict market discovery; API-Football player stats lookup. `/predictions` was not read.",
        "",
        f"- run_time_utc: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        f"- apply: {str(apply).lower()}",
        f"- targets: {len(rows)}",
        f"- accepted: {counts.get('accepted', 0)}",
        f"- unresolved: {counts.get('unresolved', 0)}",
        f"- overrides_written: {len(written)}",
        "",
        "| status | match | kickoff_local | player | family | candidate_nationalities | matched | apps | SOT/match | goals/match | G+A/match | action | reason |",
        "| --- | --- | --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | --- | --- |",
    ]
    for row in rows:
        kickoff = row["kickoff"].astimezone(tz).strftime("%Y-%m-%d %H:%M %Z")
        reason = str(row["reason"]).replace("|", "/")
        lines.append(
            f"| {row['status']} | {row['match']} | {kickoff} | {row['player']} | {row['family']} | "
            f"{row['nationalities']} | {row['matched']} | {row['apps']} | {row['sot_per_match']} | "
            f"{row['goals_per_match']} | {row['involvements_per_match']} | {row['action']} | {reason} |"
        )
    if written:
        lines.extend(["", "## Overrides Written", ""])
        for player in sorted(written):
            lines.append(f"- {player}")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help=f"write accepted matches to {OVERRIDES}")
    ap.add_argument("--write", action="store_true", help=f"write report to {OUT}")
    args = ap.parse_args()

    text, _rows = run_research(apply=args.apply)
    print(text)
    if args.write:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(text, encoding="utf-8")
        print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
