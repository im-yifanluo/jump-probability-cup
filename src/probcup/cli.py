"""The `probcup` command: one entry point for every tool in the system.

    probcup <command> [options]      (or: python -m probcup <command> ...)

Each command is a module with a `main(argv)`; imports are lazy so a quick
command (watchdog, schedule) never loads the modelling stack.
"""
from __future__ import annotations

import importlib
import sys

from probcup import __version__

# name -> (module, one-line summary)
COMMANDS: dict[str, tuple[str, str]] = {
    "demo": ("probcup.demo", "price a real tournament board offline (no API keys needed)"),
    "run": ("probcup.pipeline.run", "price + submit every match kicking off within --hours (both bots)"),
    "price": ("probcup.pipeline.build", "price one live match and print the full table"),
    "lineups": ("probcup.sources.espn_lineups", "pull a confirmed starting XI from ESPN"),
    "player-rates": ("probcup.sources.player_rates", "refresh player rates from API-Football"),
    "verify": ("probcup.sportspredict.verify", "check every logged submission landed on the platform"),
    "backlog": ("probcup.pipeline.backlog", "record every live question the parser can't price yet"),
    "schedule": ("probcup.ops.schedule", "print today's pre-kickoff pass times (for the agent's crons)"),
    "watchdog": ("probcup.ops.watchdog", "alert if a scheduled agent run was missed (never submits)"),
    "ledger": ("probcup.evaluation.ledger", "rebuild the crowd ledger from the hand-copied history"),
    "replay": ("probcup.evaluation.replay", "replay settled markets: policy grid, walk-forward, bot 2 vs bot 1"),
    "drift": ("probcup.evaluation.drift", "per-family calibration drift over settled markets"),
    "backtest": ("probcup.evaluation.club_backtest", "walk-forward backtest on ~116k club matches"),
    "download-data": ("probcup.sources.download", "download the raw StatsBomb + football-data.co.uk data"),
}


def usage() -> str:
    width = max(map(len, COMMANDS))
    lines = [f"probcup {__version__} — Probability Cup forecasting system", "",
             "usage: probcup <command> [options]   (probcup <command> -h for details)", "",
             "commands:"]
    lines += [f"  {name:<{width}}  {summary}" for name, (_, summary) in COMMANDS.items()]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(usage())
        return 0
    if argv[0] in ("-V", "--version"):
        print(__version__)
        return 0
    name, rest = argv[0], argv[1:]
    if name not in COMMANDS:
        print(f"probcup: unknown command {name!r}\n\n{usage()}", file=sys.stderr)
        return 2
    module = importlib.import_module(COMMANDS[name][0])
    rc = module.main(rest)
    return 0 if rc is None else int(rc)


if __name__ == "__main__":
    raise SystemExit(main())
