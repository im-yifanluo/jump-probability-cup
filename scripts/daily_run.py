"""Daily pipeline: price + submit every match kicking off within the horizon.

This is the deterministic 90% of the daily routine — designed to be run by a
scheduled agent (or by hand) once or more per day:

1. refresh the market snapshot (new matches/markets appear as rounds open)
2. for each match with kickoff within --hours: fetch live odds, refresh
   player rates (API-budget aware), price all open markets
3. submit via the upsert path — first touch creates predictions, every later
   run PATCHes only the values that changed (so "initial" and "final" passes
   are the same command at different times)
4. print a per-match report + anything needing human/agent attention
   (players without data, odds fallbacks, submission errors)

Usage:
    python3 scripts/daily_run.py --hours 48 --dry-run   # show the plan
    python3 scripts/daily_run.py --hours 48 --submit    # do it
"""

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNS_LOG = ROOT / "data" / "logs" / "runs.log"
AGENT_HEARTBEAT = ROOT / "data" / "logs" / "agent_last_run"
sys.path.insert(0, str(ROOT / "src"))

import pandas as pd

from price_match import build_pricer
from probability_cup import ProbabilityCup
from questions.parser import parse
from questions.teams import ALL_TEAM_NAMES

sys.path.insert(0, str(ROOT / "scripts"))


def upcoming_matches(pc: ProbabilityCup, hours: float) -> pd.DataFrame:
    markets = pc.markets_df(max_age=pd.Timedelta(minutes=30).to_pytimedelta())
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(hours=hours)
    mask = (markets["kickoff"] > now) & (markets["kickoff"] <= horizon)
    return markets[mask]


def _agent_ran_within(minutes: float) -> float | None:
    """Age in minutes of the agent heartbeat, or None if absent/stale."""
    if not AGENT_HEARTBEAT.exists():
        return None
    age = (datetime.now(timezone.utc).timestamp() - AGENT_HEARTBEAT.stat().st_mtime) / 60
    return age if age <= minutes else None


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--hours", type=float, default=48, help="kickoff horizon")
    ap.add_argument("--submit", action="store_true", help="actually submit (else dry-run)")
    ap.add_argument("--dry-run", action="store_true", help="alias for not passing --submit")
    ap.add_argument("--agent", action="store_true",
                    help="mark this as the agentic run (writes the heartbeat the "
                         "crontab fallback defers to). Used by the /matchday routine.")
    ap.add_argument("--defer-to-agent", type=float, metavar="MINUTES", default=0,
                    help="skip entirely if the agent ran within MINUTES (crontab fallback "
                         "uses this so it never clobbers a fresh agent run).")
    args = ap.parse_args()
    dry = not args.submit

    # Coordination: the agent is primary, the crontab is a fallback. If the
    # agent already ran this window, the crontab run defers and does nothing.
    if args.defer_to_agent:
        age = _agent_ran_within(args.defer_to_agent)
        if age is not None:
            msg = f"deferring to agent (ran {age:.0f} min ago, within {args.defer_to_agent:.0f})"
            print(msg)
            _log_run(args.hours, dry, pd.DataFrame(), {"deferred": 1}, [msg])
            return

    pc = ProbabilityCup(bot=1)
    markets = upcoming_matches(pc, args.hours)
    if markets.empty:
        print(f"No open markets with kickoff in the next {args.hours:.0f}h.")
        return

    attention, all_probs = [], {}
    for match_name, group in markets.groupby("match_name", sort=False):
        kickoff = group["kickoff"].iloc[0]
        print(f"\n=== {match_name}  (kickoff {kickoff:%Y-%m-%d %H:%M UTC}) ===")
        try:
            pricer = build_pricer(match_name, kickoff)
        except Exception as exc:
            attention.append(f"{match_name}: odds/pricer failed — {exc}")
            print(f"  SKIPPED: {exc}")
            continue

        for _, market in group.iterrows():
            # One unpriceable market must never sink the whole unattended run:
            # skip it, submit the rest, and surface it in the attention report.
            try:
                spec = parse(market["question"], teams=ALL_TEAM_NAMES)
                p = pricer.price(spec)
            except Exception as exc:
                attention.append(f"{match_name}: CANNOT PRICE {market['question']!r} "
                                 f"({exc.__class__.__name__}: {exc})")
                print(f"  SKIP   {market['question'][:70]}  <- {exc.__class__.__name__}")
                continue
            all_probs[market["market_id"]] = p
            flag = ""
            if spec.player:
                src = pricer.players.rates(spec.player).source
                # Flag low-confidence player-rate sources so the agent researches
                # current form: no data ("prior") or sparse/possibly-stale
                # StatsBomb history (a role can change between tournaments).
                if src == "prior":
                    flag = "  <- prior only: research this player"
                    attention.append(f"{match_name}: {spec.player} priced on prior (no data)")
                elif src == "statsbomb" and pricer.players.is_sparse(spec.player):
                    flag = "  <- sparse/old data: verify current form"
                    attention.append(f"{match_name}: {spec.player} on sparse StatsBomb history "
                                     f"— fetch current club form (update_player_rates) or research")
            print(f"  {p:5.3f}  {market['question'][:70]}{flag}")

    actions = {}
    if all_probs:
        # NOTE: bot 2's key is a backup credential only — verified 2026-06-13
        # that predictions are stored per ACCOUNT, so both keys read/write the
        # same prediction set and mirroring is meaningless.
        print(f"\n{'[dry run] ' if dry else ''}submitting {len(all_probs)} predictions...")
        report = pc.submit(all_probs, dry_run=dry)
        actions = report["action"].value_counts().to_dict()
        print(actions)
        errors = report[report["action"] == "error"]
        if not errors.empty:
            attention.append(f"{len(errors)} submission errors")
            print(errors.to_string(index=False))

    if attention:
        print("\nNEEDS ATTENTION:")
        for item in attention:
            print(f"  - {item}")

    # An agentic run that actually submitted drops the heartbeat the crontab
    # fallback defers to — so the deterministic pipeline only fires when the
    # agent didn't, and never overwrites the agent's research-informed values.
    if args.agent and not dry:
        AGENT_HEARTBEAT.parent.mkdir(parents=True, exist_ok=True)
        AGENT_HEARTBEAT.write_text(datetime.now(timezone.utc).isoformat())

    _log_run(args.hours, dry, markets, actions, attention)


def _log_run(hours: float, dry: bool, markets: pd.DataFrame,
             actions: dict, attention: list[str]) -> None:
    """Every run leaves a permanent one-block trace in data/logs/runs.log,
    regardless of whether it was invoked by cron, an agent, or by hand.
    (Per-market submission detail goes to submissions.csv via the domain layer.)"""
    RUNS_LOG.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    lines = [
        f"[{stamp}] daily_run hours={hours:g} mode={'dry-run' if dry else 'SUBMIT'}",
        f"  matches={markets['match_name'].nunique()} markets={len(markets)} "
        f"actions={actions or 'none'}",
    ]
    lines += [f"  attention: {item}" for item in attention]
    with RUNS_LOG.open("a") as fh:
        fh.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
