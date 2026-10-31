"""Domain layer for the Probability Cup — what analysis and model code imports.

Sits on top of the transport client (sp_api.Client) and hides every API
mechanic: event/lobby resolution, id plumbing, the one-prediction-per-market
rule (POST vs PATCH), unit conversion, and snapshot caching.

Convention: model code works in probability space — floats in (0, 1).
Only this layer converts to the API's integer 1-99 write format.

Typical use:

    from probcup.sportspredict.cup import ProbabilityCup

    pc = ProbabilityCup(bot=1)
    markets = pc.markets_df()               # one row per open market
    probs = {mid: 0.5 for mid in markets["market_id"]}   # <- your model here
    pc.submit(probs, dry_run=True)          # inspect the plan, then dry_run=False
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pandas as pd

from probcup import config
from probcup.sportspredict.client import Client

SNAPSHOT_PATH = config.SNAPSHOT
SNAPSHOT_MAX_AGE = timedelta(hours=12)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ProbabilityCup:
    """High-level interface to one bot's view of the Probability Cup."""

    def __init__(self, bot: int = 1):
        self.bot = bot
        self.api = Client(bot=bot)
        self._event: dict | None = None
        self._lobby: dict | None = None

    # ------------------------------------------------------------------ #
    # Context (resolved once, cached on the instance)
    # ------------------------------------------------------------------ #

    @property
    def event(self) -> dict:
        if self._event is None:
            self._event = self.api.probability_cup_event()
        return self._event

    @property
    def lobby(self) -> dict:
        """The shared Probability Cup lobby; joins it if we somehow aren't in."""
        if self._lobby is None:
            lobby = self.api.lobbies(self.event["id"])[0]
            if not lobby.get("joined"):
                self.api.join_lobby(lobby["id"])
            self._lobby = lobby
        return self._lobby

    # ------------------------------------------------------------------ #
    # Market data
    # ------------------------------------------------------------------ #

    def refresh_snapshot(self) -> dict:
        """Fetch all matches + their markets (one call per match, ~71 calls —
        the client absorbs 429s) and cache to data/cache/snapshot.json."""
        matches = self.api.matches(event_id=self.event["id"])
        markets = []
        for match in matches:
            markets += self.api.markets(lobby_id=self.lobby["id"], match_id=match["id"])
        snapshot = {
            "fetched_at": _now().isoformat(),
            "event": self.event,
            "lobby": self.lobby,
            "matches": matches,
            "markets": markets,
        }
        SNAPSHOT_PATH.parent.mkdir(exist_ok=True)
        SNAPSHOT_PATH.write_text(json.dumps(snapshot, indent=2))
        return snapshot

    def snapshot(self, max_age: timedelta = SNAPSHOT_MAX_AGE) -> dict:
        """Cached snapshot, refreshed automatically when older than max_age.
        Pass max_age=timedelta(0) to force a refresh."""
        if SNAPSHOT_PATH.exists():
            snapshot = json.loads(SNAPSHOT_PATH.read_text())
            age = _now() - datetime.fromisoformat(snapshot["fetched_at"])
            if age <= max_age:
                return snapshot
        return self.refresh_snapshot()

    def matches_df(self, max_age: timedelta = SNAPSHOT_MAX_AGE) -> pd.DataFrame:
        """One row per match, sorted by kickoff.

        Note: in the live system `opening_time` is the kickoff (= prediction
        deadline) and `closing_time` is ~match end — the docs PDF has this wrong.
        """
        df = pd.DataFrame(self.snapshot(max_age)["matches"])
        df["kickoff"] = pd.to_datetime(df["opening_time"])
        return df.sort_values("kickoff").reset_index(drop=True)

    def markets_df(self, open_only: bool = True, max_age: timedelta = SNAPSHOT_MAX_AGE) -> pd.DataFrame:
        """One row per market with its match context — the modeling input table.

        Columns: market_id, question, status, match_id, match_name, kickoff.
        """
        snapshot = self.snapshot(max_age)
        matches = {m["id"]: m for m in snapshot["matches"]}
        rows = []
        for market in snapshot["markets"]:
            match = matches.get(market["match"]["id"], market["match"])
            rows.append(
                {
                    "market_id": market["id"],
                    "question": market["question"],
                    "status": market["status"],
                    "match_id": match["id"],
                    "match_name": match["name"],
                    "kickoff": match.get("opening_time"),
                }
            )
        if not rows:
            # Empty snapshot (between rounds) — return a typed empty frame so
            # callers' column access (kickoff/status/...) degrades gracefully.
            return pd.DataFrame(columns=["market_id", "question", "status",
                                         "match_id", "match_name", "kickoff"])
        df = pd.DataFrame(rows)
        df["kickoff"] = pd.to_datetime(df["kickoff"])
        if open_only:
            df = df[df["status"] == "open"]
        return df.sort_values(["kickoff", "match_name"]).reset_index(drop=True)

    # ------------------------------------------------------------------ #
    # Predictions
    # ------------------------------------------------------------------ #

    @staticmethod
    def to_api_prob(p: float) -> int:
        """Model space (0-1 float) -> API write format (int 1-99)."""
        if not 0 <= p <= 1:
            raise ValueError(f"probability must be a 0-1 float, got {p}")
        return min(99, max(1, round(p * 100)))

    @staticmethod
    def stored_prob_to_int(p: float) -> int:
        """Stored prediction -> int 1-99. The docs say reads return 0-1
        decimals, but the live API returns percentages (40, not 0.40);
        accept either."""
        return round(p * 100) if p <= 1 else round(p)

    def predictions_df(self) -> pd.DataFrame:
        """Everything submitted so far (open + settled). `probability` is 0-1."""
        return pd.DataFrame(self.api.predictions(lobby_id=self.lobby["id"]))

    def results_df(self) -> pd.DataFrame:
        """Settled predictions only, each with its brier_score (ground truth
        for tracking calibration)."""
        return pd.DataFrame(self.api.results(lobby_id=self.lobby["id"]))

    def submit(self, probs: dict[str, float] | pd.Series, dry_run: bool = False) -> pd.DataFrame:
        """Upsert predictions: {market_id: probability in (0,1)}.

        Two phases: plan (pure, what would happen) and execute (API writes).
        New markets get POSTed in batches of 50; already-predicted markets get
        PATCHed; markets at/past kickoff or with an unchanged value are skipped.
        With dry_run=True only the plan is returned.

        Returns one row per market: market_id, probability (1-99 as sent),
        action (create / update / unchanged / skip_kickoff / error), error.
        """
        if isinstance(probs, pd.Series):
            probs = probs.to_dict()

        plan = self._plan(probs)
        if not dry_run:
            self._execute(plan)
            self._log_submission(plan)
        return pd.DataFrame(plan)

    def _log_submission(self, plan: list[dict]) -> None:
        """Append an audit trail of every real submission to
        data/logs/submissions.csv (timestamp, market, value, action, error)."""
        log_path = config.SUBMISSIONS_LOG
        log_path.parent.mkdir(parents=True, exist_ok=True)
        stamp = _now().isoformat(timespec="seconds")
        rows = pd.DataFrame(plan).assign(submitted_at=stamp, bot=self.bot)
        rows = rows[["submitted_at", "bot", "market_id", "probability", "action", "error"]]
        rows.to_csv(log_path, mode="a", header=not log_path.exists(), index=False)

    def _plan(self, probs: dict[str, float]) -> list[dict]:
        """Decide an action per market without performing any writes."""
        existing = {p["market_id"]: p for p in self.api.predictions(lobby_id=self.lobby["id"])}
        markets = self.markets_df(open_only=False)
        kickoffs = dict(zip(markets["market_id"], markets["kickoff"]))
        now = _now()

        plan = []
        for market_id, p in probs.items():
            value = self.to_api_prob(p)
            kickoff = kickoffs.get(market_id)
            previous = existing.get(market_id)
            if kickoff is not None and kickoff <= now:
                action = "skip_kickoff"
            elif previous is None:
                action = "create"
            elif self.stored_prob_to_int(previous["probability"]) == value:
                action = "unchanged"
            else:
                action = "update"
            plan.append(
                {
                    "market_id": market_id,
                    "probability": value,
                    "action": action,
                    "error": None,
                    "prediction_id": previous["id"] if previous else None,
                }
            )
        return plan

    def _execute(self, plan: list[dict]) -> None:
        """Perform the writes for a plan, downgrading failures to action='error'."""
        for entry in (e for e in plan if e["action"] == "update"):
            try:
                self.api.update_prediction(entry["prediction_id"], entry["probability"])
            except Exception as exc:
                entry["action"], entry["error"] = "error", str(exc)

        creates = [e for e in plan if e["action"] == "create"]
        if creates:
            results = self.api.submit_batch(
                [
                    {"market_id": e["market_id"], "lobby_id": self.lobby["id"], "probability": e["probability"]}
                    for e in creates
                ]
            )
            outcome_by_market = {r["market_id"]: r for r in results}
            for entry in creates:
                result = outcome_by_market.get(entry["market_id"], {})
                if not result.get("success"):
                    entry["action"] = "error"
                    entry["error"] = result.get("error", "no response for market")
