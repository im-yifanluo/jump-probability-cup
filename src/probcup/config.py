"""Every filesystem location the system reads or writes, in one place.

The project root defaults to the repository checkout; set PROBCUP_ROOT to run
against another data folder (the golden tests do this to work in a sandbox).

    data/models/   fitted model files the pricer loads           (tracked)
    data/inputs/   researched per-match inputs the agent edits    (tracked)
    data/cache/    snapshots, club data cache, backtest outputs  (regenerable)
    data/raw/      downloaded StatsBomb + football-data.co.uk    (regenerable)
    data/logs/     run log, submission audit trail, heartbeat    (runtime)
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(os.environ.get("PROBCUP_ROOT") or Path(__file__).resolve().parents[2])
ENV_FILE = ROOT / ".env"           # SPORTSPREDICT_KEY_1/2, API_FOOTBALL_KEY

DATA = ROOT / "data"
MODELS = DATA / "models"
INPUTS = DATA / "inputs"
CACHE = DATA / "cache"
RAW = DATA / "raw"
LOGS = DATA / "logs"

# fitted model files
COUNT_MODELS = MODELS / "count_models.json"
STATSBOMB_TEAM_HALVES = MODELS / "statsbomb_team_halves.parquet"
STATSBOMB_PLAYERS = MODELS / "statsbomb_players.parquet"

# researched per-match inputs (never raw probabilities)
PLAYER_OVERRIDES = INPUTS / "player_overrides.yaml"
REFEREE_OVERRIDES = INPUTS / "referee_overrides.yaml"
MATCH_LINEUPS = INPUTS / "match_lineups.yaml"
BOT2_EXPERIMENT = INPUTS / "bot2_experiment.yaml"
MANUAL_ODDS = INPUTS / "manual_odds.yaml"

# caches and analysis outputs
SNAPSHOT = CACHE / "snapshot.json"
CLUB_MATCHES = CACHE / "club_matches.parquet"
BACKTEST_RESULTS = CACHE / "backtest_results.parquet"
BACKTEST_REPORT = CACHE / "backtest_report.csv"
RBP_LEDGER = CACHE / "rbp_full.csv"

# raw downloads
FDUK_RAW = RAW / "fduk"
STATSBOMB_RAW = RAW / "statsbomb"
INTERNATIONALS_RAW = RAW / "internationals"

# runtime logs
RUNS_LOG = LOGS / "runs.log"
SUBMISSIONS_LOG = LOGS / "submissions.csv"
HEARTBEAT = LOGS / "agent_last_run"
BACKLOG_JSON = LOGS / "unparsed_backlog.json"
BACKLOG_MD = LOGS / "unparsed_backlog.md"
WATCHDOG_LOG = LOGS / "watchdog.log"
RUN_MISSED = LOGS / "RUN_MISSED.txt"

# the crowd's numbers, hand-copied from the platform during the tournament
CROWD_HISTORY = ROOT / "docs" / "archive" / "crowd_history.md"

# real boards + recovered odds for the offline demo; settled results per bot
EXAMPLES = ROOT / "examples"
RESULTS_DATA = ROOT / "results" / "data"
