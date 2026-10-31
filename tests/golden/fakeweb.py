"""Offline stand-in for every HTTP endpoint the system talks to.

Patches `requests.sessions.Session.request`, which every client in the code
base goes through (SportsPredict, ESPN, Polymarket, API-Football), and serves
canned JSON from a scenario spec. Every request is recorded so a scenario's
full conversation with the outside world can be compared byte for byte.

The SportsPredict part is stateful: POSTed predictions appear in later GETs
and PATCHes update them, per bot (bots are told apart by their API key).
"""
from __future__ import annotations

import copy
import json
from urllib.parse import parse_qsl, urlsplit

import requests

SP_HOST = "api.sportspredict.com"
ESPN_HOST = "site.api.espn.com"
PM_HOST = "gamma-api.polymarket.com"
AF_HOST = "v3.football.api-sports.io"


def _response(status: int, body, url: str) -> requests.Response:
    resp = requests.models.Response()
    resp.status_code = status
    resp._content = json.dumps(body).encode()
    resp.headers["Content-Type"] = "application/json"
    resp.encoding = "utf-8"
    resp.url = url
    resp.reason = "OK" if status < 400 else "Fake Error"
    return resp


class FakeWeb:
    def __init__(self, spec: dict):
        self.spec = spec
        self.calls: list[dict] = []
        sp = spec.get("sportspredict", {})
        self.keys = sp.get("keys", {})              # api key -> bot number (str)
        self.predictions = {bot: {p["market_id"]: copy.deepcopy(p) for p in preds}
                            for bot, preds in sp.get("predictions", {}).items()}
        self._seq = sp.get("seq_start", 0)   # prediction ids continue across steps

    # ------------------------------------------------------------------ #

    def request(self, session, method, url, params=None, data=None, headers=None,
                json=None, **_):  # noqa: A002 - mirrors requests' signature
        parts = urlsplit(url)
        query = dict(parse_qsl(parts.query))
        query.update({k: str(v) for k, v in (params or {}).items() if v is not None})
        merged = dict(session.headers)
        merged.update(headers or {})
        auth = merged.get("Authorization", "")
        bot = self.keys.get(auth.removeprefix("Bearer ").strip())
        method = method.upper()
        status, body = self._route(method, parts.netloc, parts.path, query, json, bot)
        self.calls.append({"method": method, "host": parts.netloc, "path": parts.path,
                           "params": dict(sorted(query.items())), "json": json,
                           "bot": bot, "status": status})
        return _response(status, body, url)

    # ------------------------------------------------------------------ #

    def _route(self, method, host, path, query, body, bot):
        if host == SP_HOST:
            return self._sportspredict(method, path.removeprefix("/api/v1"), query, body, bot)
        if host == ESPN_HOST:
            espn = self.spec.get("espn", {})
            if path.endswith("/scoreboard"):
                return 200, espn.get("scoreboards", {}).get(query.get("dates", ""), {"events": []})
            if path.endswith("/summary"):
                return 200, espn.get("summaries", {}).get(query.get("event", ""), {})
        if host == PM_HOST and path == "/events":
            return 200, self.spec.get("polymarket", {}).get("events", [])
        if host == AF_HOST:
            return self._api_football(path, query)
        return 404, {"error": f"fakeweb: unhandled {method} {host}{path}"}

    def _sportspredict(self, method, path, query, body, bot):
        sp = self.spec["sportspredict"]
        if bot is None:
            return 401, {"error": "unknown API key"}
        if method == "GET" and path == "/events":
            return 200, sp["events"]
        if method == "GET" and path == "/lobbies":
            return 200, sp["lobbies"]
        if method == "POST" and path.startswith("/lobbies/") and path.endswith("/join"):
            return 409, {"error": "already joined"}
        if method == "GET" and path == "/matches":
            return 200, sp["matches"]
        if method == "GET" and path == "/markets":
            mid = query.get("match_id")
            return 200, [m for m in sp["markets"] if mid is None or m["match"]["id"] == mid]
        if method == "GET" and path == "/predictions":
            return 200, list(self.predictions.get(bot, {}).values())
        if method == "GET" and path == "/results":
            return 200, sp.get("results", {}).get(bot, [])
        if method == "POST" and path == "/predictions/batch":
            open_ids = {m["id"] for m in sp["markets"] if m.get("status") == "open"}
            results, ok = [], 0
            for p in body.get("predictions", []):
                if p["market_id"] not in open_ids:
                    results.append({"market_id": p["market_id"], "success": False,
                                    "error": "Market not found"})
                    continue
                self._seq += 1
                pred = {"id": f"pred-{bot}-{self._seq:04d}", "market_id": p["market_id"],
                        "probability": p["probability"], "market_status": "open"}
                self.predictions.setdefault(bot, {})[p["market_id"]] = pred
                results.append({"market_id": p["market_id"], "success": True, "trade": pred})
                ok += 1
            return 200, {"total": len(results), "succeeded": ok, "results": results}
        if method == "PATCH" and path.startswith("/predictions/"):
            pid = path.rsplit("/", 1)[-1]
            for pred in self.predictions.get(bot, {}).values():
                if pred["id"] == pid:
                    pred["probability"] = body["probability"]
                    return 200, pred
            return 404, {"error": "prediction not found"}
        return 404, {"error": f"fakeweb: unhandled SportsPredict {method} {path}"}

    def _api_football(self, path, query):
        af = self.spec.get("api_football", {})
        if path == "/status":
            return 200, {"errors": [], "response": {"requests": {"limit_day": 100,
                                                                 "current": af.get("used", 10)}}}
        if path == "/players/profiles":
            hits = af.get("profiles", {}).get(query.get("search", ""), [])
            return 200, {"errors": [], "response": hits}
        if path == "/players":
            stats = af.get("stats", {}).get(query.get("id", ""), [])
            return 200, {"errors": [], "response": stats}
        return 404, {"errors": {"path": f"fakeweb: unhandled {path}"}}


def install(fake: FakeWeb) -> None:
    """Route every requests call in this process through `fake`."""
    def _request(self, method, url, **kwargs):
        return fake.request(self, method, url, **kwargs)
    requests.sessions.Session.request = _request
