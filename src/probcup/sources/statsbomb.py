"""Aggregate StatsBomb open-data events into the tables our models need.

From each international-tournament match we extract:
- per team & half: goals, corners, cards, offsides, shots on target, fouls
- match flags: penalty awarded, first scorer team, first scorer of 2nd half
- per player: minutes, shots, shots on target, goals (excl. own goals), assists

Aggregates are cached to data/models/statsbomb_team_halves.parquet and
data/models/statsbomb_players.parquet.
"""

from __future__ import annotations

import json

import pandas as pd
from tqdm import tqdm

from probcup import config

SB_DIR = config.STATSBOMB_RAW
TEAM_CACHE = config.STATSBOMB_TEAM_HALVES
PLAYER_CACHE = config.STATSBOMB_PLAYERS

# StatsBomb period 1 = first half, 2 = second half (3+ = extra time, ignored
# for half-based markets; regulation only mirrors our market definitions).
HALVES = (1, 2)

# Within-match time windows for the timing markets, from the StatsBomb match
# `minute` (period distinguishes first/second-half added time). Hydration breaks
# are the standard ~30' and ~75' marks. We measure the empirical share of each
# event type in each window so a market's windowed rate = match rate x share.
TIMING_WINDOWS = ("before30", "after75", "h1stop", "h2stop")
TIMING_STATS = ("goals", "cards", "corners", "offsides")


def _event_windows(minute, period) -> list[str]:
    """Which timing windows a StatsBomb event falls in (before the 1st hydration
    break <30'; after the 2nd >=75'; first/second-half stoppage >=45'/>=90')."""
    if minute is None:
        return []
    if period == 1:
        return (["before30"] if minute < 30 else []) + (["h1stop"] if minute >= 45 else [])
    if period == 2:
        return (["after75"] if minute >= 75 else []) + (["h2stop"] if minute >= 90 else [])
    return []


def _team_half_rows(tournament: str, match_id: int, events: list[dict]) -> list[dict]:
    teams = sorted({e["team"]["name"] for e in events if "team" in e})
    base = {"goals": 0, "corners": 0, "cards": 0, "reds": 0,
            "offsides": 0, "sot": 0, "fouls": 0}
    base.update({f"{s}_{w}": 0 for s in TIMING_STATS for w in TIMING_WINDOWS})
    counters = {(t, h): dict(base) for t in teams for h in HALVES}
    penalty_awarded = False
    first_scorer, first_scorer_2h = None, None

    def bump(c, stat, wins):
        """Increment the stat AND its per-window timing counters."""
        c[stat] += 1
        for w in wins:
            c[f"{stat}_{w}"] += 1

    for e in events:
        period = e.get("period")
        if period not in HALVES or "team" not in e:
            continue
        team, etype = e["team"]["name"], e["type"]["name"]
        c = counters[(team, period)]
        wins = _event_windows(e.get("minute"), period)

        if etype == "Shot":
            shot = e.get("shot", {})
            outcome = shot.get("outcome", {}).get("name")
            if outcome in ("Goal", "Saved", "Saved To Post"):
                c["sot"] += 1
            if outcome == "Goal":
                bump(c, "goals", wins)
                if first_scorer is None:
                    first_scorer = team
                if period == 2 and first_scorer_2h is None:
                    first_scorer_2h = team
            if shot.get("type", {}).get("name") == "Penalty":
                penalty_awarded = True
        elif etype == "Own Goal For":
            bump(c, "goals", wins)
            if first_scorer is None:
                first_scorer = team
            if period == 2 and first_scorer_2h is None:
                first_scorer_2h = team
        elif etype == "Pass":
            pass_data = e.get("pass", {})
            if pass_data.get("type", {}).get("name") == "Corner":
                bump(c, "corners", wins)
            # Most offsides are recorded as a pass outcome against the
            # passing team; the standalone "Offside" event is the rare rest.
            if pass_data.get("outcome", {}).get("name") == "Pass Offside":
                bump(c, "offsides", wins)
        elif etype == "Foul Committed":
            c["fouls"] += 1
            card = e.get("foul_committed", {}).get("card", {}).get("name")
            if card:
                bump(c, "cards", wins)
                if card in ("Red Card", "Second Yellow"):
                    c["reds"] += 1
            if e.get("foul_committed", {}).get("penalty"):
                penalty_awarded = True
        elif etype == "Bad Behaviour":
            card = e.get("bad_behaviour", {}).get("card", {}).get("name")
            if card:
                bump(c, "cards", wins)
                if card in ("Red Card", "Second Yellow"):
                    c["reds"] += 1
        elif etype == "Offside":
            bump(c, "offsides", wins)
        elif etype == "Foul Won":
            if e.get("foul_won", {}).get("penalty"):
                penalty_awarded = True

    rows = []
    for (team, half), c in counters.items():
        opponent = next(t for t in teams if t != team)
        rows.append({
            "tournament": tournament, "match_id": match_id, "team": team,
            "opponent": opponent, "half": half, **c,
            "penalty_awarded": penalty_awarded,
            "first_scorer": first_scorer, "first_scorer_2h": first_scorer_2h,
        })
    return rows


def _player_rows(tournament: str, match_id: int, events: list[dict]) -> list[dict]:
    stats: dict[str, dict] = {}

    def entry(player: str, team: str) -> dict:
        return stats.setdefault(player, {
            "tournament": tournament, "match_id": match_id, "player": player,
            "team": team, "shots": 0, "sot": 0, "goals": 0, "assists": 0,
        })

    # Appearances first — starters from the Starting XI lineups, plus anyone
    # who came on as a substitute. One row per player per match *played*, so
    # downstream per-appearance rates are honest (a striker with no shots in
    # a match still counts that match in his denominator).
    for e in events:
        etype = e["type"]["name"]
        if etype == "Starting XI":
            for slot in e.get("tactics", {}).get("lineup", []):
                entry(slot["player"]["name"], e["team"]["name"])
        elif etype == "Substitution":
            replacement = e.get("substitution", {}).get("replacement", {}).get("name")
            if replacement:
                entry(replacement, e["team"]["name"])

    for e in events:
        if "player" not in e:
            continue
        if e["type"]["name"] == "Shot":
            s = entry(e["player"]["name"], e["team"]["name"])
            s["shots"] += 1
            outcome = e["shot"].get("outcome", {}).get("name")
            if outcome in ("Goal", "Saved", "Saved To Post"):
                s["sot"] += 1
            if outcome == "Goal":
                s["goals"] += 1
        elif e["type"]["name"] == "Pass" and e.get("pass", {}).get("goal_assist"):
            entry(e["player"]["name"], e["team"]["name"])["assists"] += 1
    return list(stats.values())


def build_aggregates(refresh: bool = False) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Parse all downloaded tournaments -> (team_halves, players) DataFrames."""
    if TEAM_CACHE.exists() and PLAYER_CACHE.exists() and not refresh:
        return pd.read_parquet(TEAM_CACHE), pd.read_parquet(PLAYER_CACHE)

    team_rows, player_rows = [], []
    for tournament_dir in sorted(SB_DIR.iterdir()):
        events_dir = tournament_dir / "events"
        if not events_dir.exists():
            continue
        for path in tqdm(sorted(events_dir.glob("*.json")), desc=tournament_dir.name):
            events = json.loads(path.read_text())
            match_id = int(path.stem)
            team_rows += _team_half_rows(tournament_dir.name, match_id, events)
            player_rows += _player_rows(tournament_dir.name, match_id, events)

    team_df = pd.DataFrame(team_rows)
    player_df = pd.DataFrame(player_rows)
    team_df.to_parquet(TEAM_CACHE)
    player_df.to_parquet(PLAYER_CACHE)
    return team_df, player_df
