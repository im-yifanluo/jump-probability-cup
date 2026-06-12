"""API-Football (api-sports.io) client — free-plan aware.

Free plan (verified 2026-06-12): all endpoints work but only for seasons
2022-2024 and 100 requests/day. That makes it our source for *player season
rates* (latest completed season, 2024-25). 2026 fixtures/lineups/refs and
odds need a paid plan — until then those come from web research.

Used by scripts/update_player_rates.py to populate data/player_overrides.yaml.
"""

from __future__ import annotations

import os
from pathlib import Path

import requests

API = "https://v3.football.api-sports.io"
LATEST_FREE_SEASON = 2024  # = 2024-25 club season, newest the free plan serves


def _key() -> str:
    key = os.environ.get("API_FOOTBALL_KEY")
    if not key:
        for line in (Path(__file__).resolve().parent.parent.parent / ".env").read_text().splitlines():
            name, _, value = line.strip().partition("=")
            if name == "API_FOOTBALL_KEY" and value:
                key = value.strip()
    if not key:
        raise SystemExit("API_FOOTBALL_KEY missing from .env")
    return key


class ApiFootball:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers["x-apisports-key"] = _key()

    def get(self, path: str, **params) -> dict:
        # Free plan: 100 requests/day AND ~10/minute — pace politely, back off on 429.
        import time
        for attempt in range(4):
            resp = self.session.get(f"{API}/{path}", params=params, timeout=20)
            if resp.status_code == 429:
                time.sleep(20 * (attempt + 1))
                continue
            resp.raise_for_status()
            body = resp.json()
            if body.get("errors"):
                raise RuntimeError(f"{path}: {body['errors']}")
            time.sleep(6.5)
            return body
        raise RuntimeError(f"{path}: rate-limited after retries")

    def remaining_today(self) -> int:
        status = self.get("status")["response"]
        req = status["requests"]
        return req["limit_day"] - req["current"]

    def find_player(self, name: str, nationalities: set[str] | None = None) -> dict | None:
        """Global player search (1 request). The API only accepts plain
        alphanumeric search strings, so we ASCII-fold and use the last name;
        among hits, prefer a nationality from the fixture's two countries."""
        import unicodedata
        folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
        last = [w for w in folded.replace("-", " ").split() if len(w) >= 3][-1]
        # Full name first (most precise), then last name (handles middle names
        # and API's different name forms). Common last names need the
        # nationality filter below to land on the right player.
        hits = self.get("players/profiles", search=folded)["response"]
        if not hits:
            hits = self.get("players/profiles", search=last)["response"]
        if not hits:
            return None
        if nationalities:
            national = [h for h in hits if h["player"].get("nationality") in nationalities]
            hits = national or hits
        return hits[0]["player"]

    def player_season_rates(self, name: str, nationalities: set[str] | None = None,
                            season: int = LATEST_FREE_SEASON) -> dict | None:
        """Latest-season per-match rates aggregated across competitions.
        None if not found or no appearances. Costs 2 requests."""
        player = self.find_player(name, nationalities)
        if player is None:
            return None
        body = self.get("players", id=player["id"], season=season)
        if not body["response"]:
            return None
        apps = shots_on = goals = assists = 0
        for stats in body["response"][0]["statistics"]:
            apps += stats["games"]["appearences"] or 0
            shots_on += stats["shots"]["on"] or 0
            goals += stats["goals"]["total"] or 0
            assists += stats["goals"]["assists"] or 0
        if apps == 0:
            return None
        return {
            "matched_name": player["name"],
            "nationality": player.get("nationality"),
            "season": season,
            "apps": apps,
            "sot_per_match": round(shots_on / apps, 3),
            "goals_per_match": round(goals / apps, 3),
            "involvements_per_match": round((goals + assists) / apps, 3),
        }
