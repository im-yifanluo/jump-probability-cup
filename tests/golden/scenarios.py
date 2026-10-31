"""Scenario definitions for the golden tests: the fake outside world each CLI
command runs against, and how a scenario's data folder is laid out.

Everything is built from committed fixtures, so a scenario is identical every
time it runs. The clock is frozen at NOW: 50 minutes before the first two
kickoffs of the 2026-06-27 board, which puts those two matches inside the
lineup-pull window and the next five inside a 48-hour run.
"""
from __future__ import annotations

import json
import shutil
import unicodedata
from pathlib import Path

import pandas as pd
import yaml

REPO = Path(__file__).resolve().parents[2]
FIX = REPO / "tests" / "fixtures"
NOW = "2026-06-27T22:40:00+00:00"

KEYS = {"fake-key-bot-1": "1", "fake-key-bot-2": "2"}
ENV = {"SPORTSPREDICT_KEY_1": "fake-key-bot-1", "SPORTSPREDICT_KEY_2": "fake-key-bot-2",
       "API_FOOTBALL_KEY": "fake-key-api-football"}

# Where each data file lives: original flat layout -> restructured layout.
# Golden outputs are stored in the restructured layout's terms.
PATH_MAP = {
    "data/count_models.json": "data/models/count_models.json",
    "data/family_blend.json": "data/models/family_blend.json",
    "data/statsbomb_team_halves.parquet": "data/models/statsbomb_team_halves.parquet",
    "data/statsbomb_players.parquet": "data/models/statsbomb_players.parquet",
    "data/player_overrides.yaml": "data/inputs/player_overrides.yaml",
    "data/referee_overrides.yaml": "data/inputs/referee_overrides.yaml",
    "data/match_lineups.yaml": "data/inputs/match_lineups.yaml",
    "data/bot2_experiment.yaml": "data/inputs/bot2_experiment.yaml",
    "data/manual_odds.yaml": "data/inputs/manual_odds.yaml",
    "data/snapshot.json": "data/cache/snapshot.json",
    "data/club_matches.parquet": "data/cache/club_matches.parquet",
    "data/backtest_results.parquet": "data/cache/backtest_results.parquet",
    "data/backtest_report.csv": "data/cache/backtest_report.csv",
    "data/rbp_full.csv": "data/cache/rbp_full.csv",
    "docs/history.md": "docs/archive/crowd_history.md",
}
PREDICTIONS_CSV_OLD = "data/predictions_"
PREDICTIONS_CSV_NEW = "data/cache/predictions_"

# Tracked model files and researched inputs as of the final.
STATE_FILES = ["data/count_models.json", "data/family_blend.json",
               "data/statsbomb_team_halves.parquet", "data/statsbomb_players.parquet",
               "data/player_overrides.yaml", "data/referee_overrides.yaml",
               "data/match_lineups.yaml", "data/bot2_experiment.yaml"]


def new_path(old_rel: str) -> str:
    """Translate an original-layout relative path into the restructured one."""
    if old_rel in PATH_MAP:
        return PATH_MAP[old_rel]
    if old_rel.startswith(PREDICTIONS_CSV_OLD):
        return PREDICTIONS_CSV_NEW + old_rel[len(PREDICTIONS_CSV_OLD):]
    return old_rel


def translate_text(text: str) -> str:
    """Rewrite original-layout path mentions inside output text."""
    for old, new in sorted(PATH_MAP.items(), key=lambda kv: -len(kv[0])):
        text = text.replace(old, new)
    return text.replace(PREDICTIONS_CSV_OLD, PREDICTIONS_CSV_NEW)


# --------------------------------------------------------------------------- #
# the fake outside world

def _snapshot() -> dict:
    return json.loads((FIX / "platform_snapshot.json").read_text())


def _platform() -> dict:
    snap = _snapshot()
    markets = list(snap["markets"])
    # One question no parser rule knows, on a far-off match, so the backlog
    # scanner's "unparseable" path runs in every pipeline scenario.
    far = next(m for m in snap["matches"] if m["name"] == "ARG vs CPV")
    markets.append({"id": "golden-unparseable-market",
                    "question": "Will the referee wear a yellow shirt?",
                    "event_type": "probability", "status": "open",
                    "match": {"id": far["id"], "name": far["name"],
                              "opening_time": far["opening_time"],
                              "closing_time": far["closing_time"]},
                    "lobby_id": snap["lobby"]["id"]})
    return {"keys": KEYS, "events": [snap["event"]], "lobbies": [snap["lobby"]],
            "matches": snap["matches"], "markets": markets}


def _market_ids(match_name: str) -> list[str]:
    snap = _snapshot()
    mid = next(m["id"] for m in snap["matches"] if m["name"] == match_name)
    return [m["id"] for m in snap["markets"] if m["match"]["id"] == mid]


def _roster(starters: list[str], bench: list[str]) -> dict:
    return {"roster": [{"starter": True, "athlete": {"displayName": n}} for n in starters]
                      + [{"starter": False, "athlete": {"displayName": n}} for n in bench]}


def _event(eid, home, away, total_odds=None):
    comp = {"competitors": [{"homeAway": "home", "team": {"displayName": home}},
                            {"homeAway": "away", "team": {"displayName": away}}]}
    if total_odds:
        comp["odds"] = [{"total": {"over": {"close": {"odds": total_odds[0]}},
                                   "under": {"close": {"odds": total_odds[1]}}}}]
    return {"id": eid, "name": f"{away} at {home}", "competitions": [comp]}


def _pick(home_ml, draw_ml, away_ml, ou):
    return {"pickcenter": [{"provider": {"name": "DraftKings"}, "overUnder": ou,
                            "homeTeamOdds": {"moneyLine": home_ml},
                            "drawOdds": {"moneyLine": draw_ml},
                            "awayTeamOdds": {"moneyLine": away_ml}}]}


def _espn() -> dict:
    col = [f"Colombia Starter {i}" for i in range(1, 11)] + ["James Rodríguez"]
    por = [f"Portugal Starter {i}" for i in range(1, 11)] + ["Gonçalo Inácio"]
    summaries = {
        # posted XI: Díaz on the bench (20 min), Gonçalo Ramos absent (UNMATCHED)
        "espn-col-por": _pick(210, 230, 125, 2.5) | {"rosters": [
            _roster(col, ["Luis Díaz", "Jhon Arias"]),
            _roster(por, ["Portugal Sub 1", "Portugal Sub 2"])]},
        # only 7 starters each: lineups not out yet
        "espn-cod-uzb": _pick(150, 210, 190, 2.5) | {"rosters": [
            _roster([f"DR Congo Starter {i}" for i in range(7)], []),
            _roster([f"Uzbekistan Starter {i}" for i in range(7)], [])]},
        # ESPN lists Argentina as home: the odds must be re-oriented
        "espn-jor-arg": _pick(-800, 900, 2000, 3.5),
        # no closing totals block on the scoreboard: p_over falls back to 0.5
        "espn-rsa-can": _pick(260, 220, 110, 2.5),
        "espn-ger-par": _pick(-300, 400, 900, 2.5),
    }
    scoreboards = {
        "20260627": {"events": [_event("espn-cod-uzb", "DR Congo", "Uzbekistan", ("+105", "-125")),
                                _event("espn-col-por", "Colombia", "Portugal", ("-110", "-110")),
                                # 19:00Z on 06-28 sits on ESPN's previous-day board
                                _event("espn-rsa-can", "South Africa", "Canada")]},
        "20260628": {"events": [_event("espn-jor-arg", "Argentina", "Jordan", ("-105", "-115"))]},
        "20260629": {"events": [_event("espn-ger-par", "Germany", "Paraguay", ("-150", "+125"))]},
    }
    return {"scoreboards": scoreboards, "summaries": summaries}


def _pm_market(question, yes):
    return {"question": question, "outcomes": json.dumps(["Yes", "No"]),
            "outcomePrices": json.dumps([f"{yes:.3f}", f"{1 - yes:.3f}"])}


def _polymarket() -> dict:
    return {"events": [
        {"title": "Colombia vs. Portugal", "volume": 2_400_000, "markets": [
            _pm_market("Will Colombia win on 2026-06-27?", 0.29),
            _pm_market("Will Colombia vs. Portugal end in a draw?", 0.30),
            _pm_market("Will Portugal win on 2026-06-27?", 0.42)]},
        # disagrees with the bookmaker by more than 0.10 on the home win
        {"title": "Jordan vs. Argentina", "volume": 3_100_000, "markets": [
            _pm_market("Will Jordan win on 2026-06-28?", 0.21),
            _pm_market("Will Jordan vs. Argentina end in a draw?", 0.12),
            _pm_market("Will Argentina win on 2026-06-28?", 0.68)]},
        # incomplete (no draw market): no blend for this fixture
        {"title": "South Africa vs. Canada", "volume": 900_000, "markets": [
            _pm_market("Will South Africa win on 2026-06-28?", 0.27),
            _pm_market("Will Canada win on 2026-06-28?", 0.45)]},
        {"title": "Premier League Winner 2027", "volume": 5_000_000, "markets": []},
    ]}


def _fold(name: str) -> str:
    return unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()


def _api_football() -> dict:
    """Profiles and season stats for the COL vs POR players: one regular
    starter, one with too few appearances, the rest not found."""
    def _profile(pid, name, nat):
        return [{"player": {"id": pid, "name": name, "nationality": nat}}]

    def _stats(apps, sot, goals, assists):
        return [{"statistics": [{"games": {"appearences": apps}, "shots": {"on": sot},
                                 "goals": {"total": goals, "assists": assists}}]}]
    return {
        "used": 12,
        "profiles": {_fold("Luis Díaz"): _profile(5001, "L. Díaz", "Colombia"),
                     "Ramos": _profile(5002, "G. Ramos", "Portugal")},
        "stats": {"5001": _stats(31, 38, 13, 6), "5002": _stats(6, 5, 2, 0)},
    }


def _results() -> dict:
    out = {}
    for bot in ("1", "2"):
        df = pd.read_csv(REPO / "results" / "data" / f"settled_markets_bot{bot}.csv")
        out[bot] = json.loads(df.to_json(orient="records"))
    return out


def _predictions(kind: str) -> dict:
    col_por, jor_arg, cod_uzb = (_market_ids(n) for n in ("COL vs POR", "JOR vs ARG", "COD vs UZB"))
    if kind == "verify":
        return {"1": [{"id": f"pre-1-{i:03d}", "market_id": m, "probability": 40 + i,
                       "market_status": "open"} for i, m in enumerate(col_por)]}
    return {
        "1": [{"id": f"pre-1-{i:03d}", "market_id": m, "probability": 50,
               "market_status": "open"} for i, m in enumerate(col_por + jor_arg)],
        "2": [{"id": f"pre-2-{i:03d}", "market_id": m, "probability": 40,
               "market_status": "open"} for i, m in enumerate(cod_uzb)],
    }


def web_spec(predictions: str = "pipeline") -> dict:
    platform = _platform() | {"predictions": _predictions(predictions), "results": _results()}
    return {"now": NOW, "sportspredict": platform, "espn": _espn(),
            "polymarket": _polymarket(), "api_football": _api_football()}


# --------------------------------------------------------------------------- #
# scenario data folders

MANUAL_ODDS = {
    "ALG vs AUT": {"p_home": 0.23, "p_draw": 0.27, "total_goals": 2.6},
    # tagged for a different date: expires loudly and falls back to live odds
    "GER vs PAR": {"p_home": 0.70, "p_draw": 0.19, "total_goals": 2.9,
                   "kickoff_date": "2026-06-01"},
}


def _submissions_log() -> str:
    col_por = _market_ids("COL vs POR")
    lines = ["submitted_at,market_id,probability,action,error",
             f"2026-06-27T10:00:00+00:00,{col_por[0]},40,create,",            # old 5-field schema
             f"2026-06-27T10:00:00+00:00,{col_por[1]},41,create,",
             "submitted_at,bot,market_id,probability,action,error",
             f"2026-06-27T12:00:00+00:00,1,{col_por[2]},99,update,",           # mismatch
             f"2026-06-27T12:00:00+00:00,1,{col_por[3]},43,unchanged,",
             f"2026-06-27T12:00:00+00:00,1,missing-market-id,55,create,",      # missing
             f"2026-06-27T12:00:00+00:00,2,{col_por[4]},20,create,"]           # other bot
    return "\n".join(lines) + "\n"


SCENARIOS: dict[str, dict] = {
    "run_submit": {"steps": [["run", "--hours", "48", "--submit", "--agent"]]},
    "run_dry": {"steps": [["run", "--hours", "48", "--dry-run"]]},
    "run_noop": {"steps": [["run", "--hours", "0.5", "--submit", "--agent"]]},
    "run_twice": {"steps": [["run", "--hours", "5", "--submit", "--agent"],
                            ["run", "--hours", "5", "--submit", "--agent"]]},
    "price": {"steps": [["price", "COL vs POR"], ["price", "ALG vs AUT", "--save"]]},
    "lineups": {"steps": [["lineups", "COL vs POR", "--dry-run"], ["lineups", "COL vs POR"],
                          ["lineups", "COD vs UZB"]]},
    "schedule": {"steps": [["schedule"]]},
    "watchdog_clear": {"heartbeat_age_min": 30, "snapshot": True, "steps": [["watchdog"]]},
    "watchdog_alert": {"snapshot": True, "steps": [["watchdog"]]},
    "backlog": {"steps": [["backlog"]]},
    "verify": {"predictions": "verify", "submissions_log": True,
               "steps": [["verify", "--bot", "1"]]},
    "player_rates": {"steps": [["player-rates", "COL vs POR"]]},
    "evaluation": {"steps": [["ledger"], ["replay", "policies"], ["replay", "alpha"],
                             ["replay", "policies", "--recent", "30"], ["replay", "ab"],
                             ["drift"], ["drift", "--bot", "2"]]},
}


def materialize(layout: str, root: Path, scenario: dict, source_root: Path,
                probe: dict | None = None) -> None:
    """Lay out a scenario's data folder under `root` in the given layout.

    old: `root` is already a copy of the original tree (code + data).
    new: `root` is empty; final-state model files and inputs are copied from
         `source_root` (the restructured repo).
    """
    def place(old_rel: str, content: bytes | str) -> None:
        rel = old_rel if layout == "old" else new_path(old_rel)
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        (path.write_bytes if isinstance(content, bytes) else path.write_text)(content)

    if layout == "new":
        for old_rel in STATE_FILES:
            place(old_rel, (source_root / new_path(old_rel)).read_bytes())
        place("docs/history.md", (source_root / new_path("docs/history.md")).read_bytes())

    place("data/bot2_experiment.yaml",
          yaml.safe_dump(probe or yaml.safe_load((FIX / "experiment_probe.yaml").read_text()),
                         sort_keys=False))
    place("data/manual_odds.yaml", yaml.safe_dump(MANUAL_ODDS, sort_keys=True))
    (root / "data" / "logs").mkdir(parents=True, exist_ok=True)
    if scenario.get("snapshot"):
        snap = _snapshot() | {"fetched_at": NOW}
        place("data/snapshot.json", json.dumps(snap, indent=2))
    if scenario.get("submissions_log"):
        place("data/logs/submissions.csv", _submissions_log())
    if "heartbeat_age_min" in scenario:
        place("data/logs/agent_last_run", NOW)


def copy_tree(src: Path, dst: Path) -> None:
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
