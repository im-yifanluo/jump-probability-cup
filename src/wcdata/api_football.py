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
import unicodedata

import requests

API = "https://v3.football.api-sports.io"
LATEST_FREE_SEASON = 2024  # = 2024-25 club season, newest the free plan serves


_TRANSLITERATION = str.maketrans(
    {
        "ø": "o",
        "Ø": "O",
        "đ": "d",
        "Đ": "D",
        "ð": "d",
        "Ð": "D",
        "þ": "th",
        "Þ": "Th",
        "ł": "l",
        "Ł": "L",
        "ı": "i",
        "İ": "I",
        "ß": "ss",
        "æ": "ae",
        "Æ": "Ae",
        "œ": "oe",
        "Œ": "Oe",
    }
)


def _fold(value: str | None) -> str:
    if not value:
        return ""
    return unicodedata.normalize("NFKD", value.translate(_TRANSLITERATION)).encode("ascii", "ignore").decode().lower()


def _tokens(value: str | None) -> set[str]:
    return {part for part in _fold(value).replace("-", " ").replace(".", " ").split() if len(part) >= 2}


def _country_forms(value: str) -> set[str]:
    folded = _fold(value).replace("'", " ")
    compact = " ".join(folded.split())
    forms = {compact}
    aliases = {
        "ivory coast": {"ivory coast", "cote divoire", "cote d ivoire"},
        "cote d ivoire": {"ivory coast", "cote divoire", "cote d ivoire"},
        "cote divoire": {"ivory coast", "cote divoire", "cote d ivoire"},
        "bosnia and herzegovina": {"bosnia and herzegovina", "bosnia herzegovina", "bosnia"},
        "united states": {"united states", "usa", "united states of america"},
        "turkiye": {"turkiye", "turkey"},
        "curacao": {"curacao"},
    }
    return forms | aliases.get(compact, set())


def country_matches(candidate: str | None, accepted: set[str] | None) -> bool:
    if not accepted:
        return True
    if not candidate:
        return False
    candidate_forms = _country_forms(candidate)
    accepted_forms = set().union(*(_country_forms(value) for value in accepted))
    return bool(candidate_forms & accepted_forms)


def profile_match_score(query: str, player: dict) -> tuple[int, int, int, int]:
    """Rank API-Football profile hits for a human player query.

    The API often returns surname hits in arbitrary order. For example, searching
    "Ayase Ueda" via the fallback surname query returns Naomichi Ueda before the
    correct A. Ueda profile. Prefer profiles whose expanded first/last names
    contain the query tokens, then shorter token sets.
    """

    query_tokens = _tokens(query)
    names = [
        player.get("name"),
        player.get("firstname"),
        player.get("lastname"),
        " ".join(
            part for part in (player.get("firstname"), player.get("lastname")) if part
        ),
    ]
    candidate_tokens = set().union(*(_tokens(name) for name in names))
    exact_names = {_fold(name).strip() for name in names if name}
    exact = int(_fold(query).strip() in exact_names)
    contains_all = int(bool(query_tokens) and query_tokens <= candidate_tokens)
    overlap = len(query_tokens & candidate_tokens)
    extra = len(candidate_tokens - query_tokens)
    return (exact, contains_all, overlap, -extra)


def profile_is_acceptable_match(query: str, player: dict) -> bool:
    """Reject surname-only or near-name profile hits.

    API-Football profile search can return active but wrong players for common
    surnames. A global override should only be written when the profile has
    enough name evidence to identify the player, not merely the surname.
    """

    query_tokens = _tokens(query)
    if not query_tokens:
        return False
    exact, contains_all, overlap, _extra = profile_match_score(query, player)
    if exact or contains_all:
        return True
    if len(query_tokens) == 1:
        return overlap == 1
    return False


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
        folded = _fold(name)
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
            national = [h for h in hits if country_matches(h["player"].get("nationality"), nationalities)]
            hits = national or hits
        candidates = [h["player"] for h in hits if profile_is_acceptable_match(name, h["player"])]
        if not candidates:
            return None
        return max(candidates, key=lambda p: profile_match_score(name, p))

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
            "player_id": player["id"],
            "matched_name": player["name"],
            "firstname": player.get("firstname"),
            "lastname": player.get("lastname"),
            "nationality": player.get("nationality"),
            "season": season,
            "apps": apps,
            "sot_per_match": round(shots_on / apps, 3),
            "goals_per_match": round(goals / apps, 3),
            "involvements_per_match": round((goals + assists) / apps, 3),
        }
