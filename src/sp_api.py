"""Minimal client for the SportsPredict Model API (Jump Trading Probability Cup).

Auth: set SPORTSPREDICT_KEY in the environment or in a .env file at the repo root.
Rate limit: 60 req/min per IP. Use submit_batch (50 per call) for writes.
"""

import os
import time
from pathlib import Path

import requests

API = "https://api.sportspredict.com/api/v1"


def _load_key(bot: int = 1) -> str:
    # Each bot has its own key and its own leaderboard entry. Keys live in .env
    # at the repo root as SPORTSPREDICT_KEY_1 / SPORTSPREDICT_KEY_2 (env vars
    # with the same names also work and take precedence).
    name = f"SPORTSPREDICT_KEY_{bot}"
    key = os.environ.get(name)
    if not key:
        env_file = Path(__file__).resolve().parent.parent / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                var, _, value = line.strip().partition("=")
                if var == name and value:
                    key = value.strip().strip('"').strip("'")
                    break
    if not key:
        raise SystemExit(
            f"No API key found for bot {bot}. Generate one in the SportsPredict app "
            "(Probability Cup event -> Profile & History -> My Bots -> Generate New Bot), "
            f"then put {name}=sp_live_... in .env at the repo root."
        )
    return key


class Client:
    def __init__(self, bot: int = 1, key: str | None = None):
        """bot=1 or bot=2 selects which bot's key to use (each is a separate
        leaderboard entry). Pass `key` directly to bypass .env entirely."""
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {key or _load_key(bot)}",
                "Content-Type": "application/json",
            }
        )

    def _request(self, method: str, path: str, **kwargs):
        for attempt in range(5):
            resp = self.session.request(method, f"{API}{path}", timeout=30, **kwargs)
            if resp.status_code == 429:
                wait = 2**attempt * 5
                print(f"Rate limited (429), backing off {wait}s...")
                time.sleep(wait)
                continue
            if resp.status_code >= 500:
                wait = 2**attempt * 2
                print(f"Server error {resp.status_code}, retrying in {wait}s...")
                time.sleep(wait)
                continue
            return resp
        resp.raise_for_status()
        return resp

    def get(self, path: str, **params):
        resp = self._request("GET", path, params=params or None)
        resp.raise_for_status()
        return resp.json()

    # --- Discovery ---

    def events(self, limit: int = 100):
        return self.get("/events", limit=limit)

    def probability_cup_event(self) -> dict:
        # The docs describe type == "probability", but the live API returns an
        # opaque type UUID — match on the title instead.
        events = self.events()
        return next(e for e in events if "probability cup" in e.get("title", "").lower())

    def lobbies(self, event_id: str):
        return self.get("/lobbies", event_id=event_id)

    def join_lobby(self, lobby_id: str) -> bool:
        """Returns True if joined (or already a member)."""
        resp = self._request("POST", f"/lobbies/{lobby_id}/join")
        if resp.status_code == 409:  # already joined
            return True
        resp.raise_for_status()
        return True

    def matches(self, event_id: str | None = None, lobby_id: str | None = None):
        params = {}
        if event_id:
            params["event_id"] = event_id
        if lobby_id:
            params["lobby_id"] = lobby_id
        return self.get("/matches", **params)

    def markets(self, lobby_id: str | None = None, match_id: str | None = None):
        params = {}
        if lobby_id:
            params["lobby_id"] = lobby_id
        if match_id:
            params["match_id"] = match_id
        return self.get("/markets", **params)

    # --- Predictions ---

    def submit_batch(self, predictions: list[dict]) -> list[dict]:
        """Submit predictions: [{market_id, lobby_id, probability(int 1-99)}, ...].

        Chunks into batches of 50 (API limit). Returns per-market results.
        """
        all_results = []
        for i in range(0, len(predictions), 50):
            chunk = predictions[i : i + 50]
            resp = self._request("POST", "/predictions/batch", json={"predictions": chunk})
            resp.raise_for_status()
            result = resp.json()
            print(f"Batch {i // 50 + 1}: {result['succeeded']}/{result['total']} succeeded")
            all_results.extend(result["results"])
        return all_results

    def update_prediction(self, prediction_id: str, probability: int) -> dict:
        resp = self._request("PATCH", f"/predictions/{prediction_id}", json={"probability": probability})
        resp.raise_for_status()
        return resp.json()

    def predictions(self, lobby_id: str | None = None):
        return self.get("/predictions", **({"lobby_id": lobby_id} if lobby_id else {}))

    def results(self, lobby_id: str | None = None):
        """Settled predictions only, each with its brier_score."""
        return self.get("/results", **({"lobby_id": lobby_id} if lobby_id else {}))
