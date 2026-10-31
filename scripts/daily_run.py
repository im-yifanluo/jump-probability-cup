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
import contextlib
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNS_LOG = ROOT / "data" / "logs" / "runs.log"
AGENT_HEARTBEAT = ROOT / "data" / "logs" / "agent_last_run"
EXPERIMENT_PATH = ROOT / "data" / "bot2_experiment.yaml"
LINEUP_PULL_WINDOW = timedelta(minutes=100)  # pull the confirmed XI within this of kickoff
sys.path.insert(0, str(ROOT / "src"))


def _load_experiment(bots: list[int]) -> dict | None:
    """Bot-2 experimental strategy (a live A/B vs bot-1's stable v4), from
    data/bot2_experiment.yaml. Returns None — so bot 2 MIRRORS bot 1 — if the file
    is absent/empty or bot 2 isn't configured. The agent rewrites this file at the
    06:00 experimental run; every later pass automatically keeps bot 2 on it."""
    if 2 not in bots or not EXPERIMENT_PATH.exists():
        return None
    import yaml
    exp = yaml.safe_load(EXPERIMENT_PATH.read_text()) or {}
    _keys = ("tournament_intensity", "tournament_team_sot", "total_sot_scale",
             "total_offsides_scale", "total_shots_scale", "total_cards_scale",
             "favorite_sot_boost", "timing_scale")
    return exp if any(exp.get(k) for k in _keys) else None


@contextlib.contextmanager
def _experimental(exp: dict):
    """Temporarily swap the live model constants for the experiment's around a
    second pricing pass. CountPricer.mean() and PlayerModel.p_sot_ge read these
    module globals at call time, so the SAME pricer reprices under the override —
    bot 1 and bot 2 thus share identical odds, isolating the strategy as the only
    difference. Always restored, even on error."""
    import models.counts as mc
    import models.players as mp
    saved = (mc.TOURNAMENT_INTENSITY, mp.TOURNAMENT_TEAM_SOT, mc.TOTAL_SOT_SCALE,
             mc.TOTAL_OFFSIDES_SCALE, mc.TOTAL_SHOTS_SCALE, mc.TOTAL_CARDS_SCALE,
             mc.FAVORITE_SOT_BOOST, mc.TIMING_SCALE)
    try:
        if exp.get("tournament_intensity"):
            mc.TOURNAMENT_INTENSITY = exp["tournament_intensity"]
        if exp.get("tournament_team_sot"):
            mp.TOURNAMENT_TEAM_SOT = float(exp["tournament_team_sot"])
        if exp.get("total_sot_scale"):
            mc.TOTAL_SOT_SCALE = float(exp["total_sot_scale"])
        if exp.get("total_offsides_scale"):
            mc.TOTAL_OFFSIDES_SCALE = float(exp["total_offsides_scale"])
        if exp.get("total_shots_scale"):
            mc.TOTAL_SHOTS_SCALE = float(exp["total_shots_scale"])
        if exp.get("total_cards_scale"):
            mc.TOTAL_CARDS_SCALE = float(exp["total_cards_scale"])
        if exp.get("favorite_sot_boost"):
            mc.FAVORITE_SOT_BOOST = float(exp["favorite_sot_boost"])
        if exp.get("timing_scale"):
            mc.TIMING_SCALE = float(exp["timing_scale"])
        yield
    finally:
        (mc.TOURNAMENT_INTENSITY, mp.TOURNAMENT_TEAM_SOT, mc.TOTAL_SOT_SCALE,
         mc.TOTAL_OFFSIDES_SCALE, mc.TOTAL_SHOTS_SCALE, mc.TOTAL_CARDS_SCALE,
         mc.FAVORITE_SOT_BOOST, mc.TIMING_SCALE) = saved


def _available_bots() -> list[int]:
    """Bots with a configured key — every update is submitted to each of them
    explicitly (no reliance on account-level propagation)."""
    import os
    env = os.environ
    env_file = (ROOT / ".env").read_text() if (ROOT / ".env").exists() else ""
    bots = [b for b in (1, 2)
            if env.get(f"SPORTSPREDICT_KEY_{b}") or f"SPORTSPREDICT_KEY_{b}=" in env_file]
    return bots or [1]

import pandas as pd

from price_match import build_pricer
from probability_cup import ProbabilityCup
from questions.parser import parse
from questions.teams import ALL_TEAM_NAMES

sys.path.insert(0, str(ROOT / "scripts"))


def upcoming_matches(pc: ProbabilityCup, hours: float) -> pd.DataFrame:
    markets = pc.markets_df(max_age=pd.Timedelta(minutes=30).to_pytimedelta())
    if markets.empty or "kickoff" not in markets:
        # A fully-empty snapshot (between rounds: no open markets anywhere)
        # yields a columnless frame — treat as "nothing upcoming", don't crash.
        return pd.DataFrame()
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


def _pull_confirmed_lineups(match_name, kickoff, specs, attention) -> None:
    """For an imminent kickoff, pull the posted XI from ESPN and write confirmed
    expected_minutes (data/match_lineups.yaml) BEFORE pricing — replacing hand-typed
    benchings. Fail-safe: writes nothing unless a real XI is posted, so an early or
    missing lineup keeps the all-90 default; unmatched players are flagged, never
    silently benched."""
    players = {sp.player for _, _, sp in specs if sp.player}
    if not players:
        return
    try:
        from pull_lineups import pull_lineup, write_lineup
        res = pull_lineup(match_name, kickoff, players=players)
    except Exception as exc:
        attention.append(f"{match_name}: lineup pull error — {exc}")
        print(f"  lineups: ERROR — {exc}")
        return
    if res["status"] != "pulled":
        print(f"  lineups: {res['status']} — not posted yet, all-90 default kept")
        return
    write_lineup(match_name, kickoff, res)
    starters = sum(1 for m in res["minutes"].values() if m >= 90)
    bench = sum(1 for m in res["minutes"].values() if m < 90)
    print(f"  lineups: ESPN confirmed XI — {starters} of our players start, {bench} benched"
          + (f"; UNMATCHED {res['unmatched']}" if res["unmatched"] else ""))
    if res["unmatched"]:
        attention.append(f"{match_name}: lineup could not match {res['unmatched']} "
                         "— verify against the posted XI (left at default 90)")


def _flag_stale_overrides(match_name, specs, attention) -> None:
    """Safety net for the staleness class: flag any player drawing a VOLATILE value
    (expected_minutes != 90 or scorer_prop_prob) from the GLOBAL override file rather
    than a per-match confirmation. Minutes are auto-corrected by the pull; a global
    scorer_prop is per-OPPONENT and must be re-checked each match (the 06-26
    Al-Dawsari +3000-vs-Spain class). Makes the residual cases loud, not silent."""
    import yaml
    ov_path = ROOT / "data" / "player_overrides.yaml"
    ml_path = ROOT / "data" / "match_lineups.yaml"
    if not ov_path.exists():
        return
    overrides = yaml.safe_load(ov_path.read_text()) or {}
    entry = (yaml.safe_load(ml_path.read_text()) or {}).get(match_name, {}) if ml_path.exists() else {}
    pulled_min = entry.get("minutes") or {}
    for p in sorted({sp.player for _, _, sp in specs if sp.player}):
        o = overrides.get(p)
        if not isinstance(o, dict):
            continue
        em = o.get("expected_minutes")
        if em is not None and em != 90 and p not in pulled_min:
            attention.append(f"{match_name}: STALE-RISK {p} expected_minutes={em} from a GLOBAL "
                             "override (past game), NOT confirmed by today's pull — verify the XI")
        if "scorer_prop_prob" in o:
            attention.append(f"{match_name}: STALE-RISK {p} scorer_prop_prob={o['scorer_prop_prob']} "
                             "is per-OPPONENT — confirm it is for THIS match's odds")


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
    bots = _available_bots()
    markets = upcoming_matches(pc, args.hours)
    if markets.empty:
        print(f"No open markets with kickoff in the next {args.hours:.0f}h.")
        # A no-op agent run still RAN — record the heartbeat so the watchdog does
        # not false-alarm "morning run missed" on days with no games in horizon.
        if args.agent and not dry:
            AGENT_HEARTBEAT.parent.mkdir(parents=True, exist_ok=True)
            AGENT_HEARTBEAT.write_text(datetime.now(timezone.utc).isoformat())
        _log_run(args.hours, dry, pd.DataFrame(), {"noop": 1}, [])
        return

    experiment = _load_experiment(bots)
    if experiment:
        print(f"** bot 2 on EXPERIMENT '{experiment['name']}' (A/B vs bot-1 v4): {experiment.get('description','')} **")
    attention, probs = [], {b: {} for b in bots}
    now = datetime.now(timezone.utc)
    for match_name, group in markets.groupby("match_name", sort=False):
        kickoff = group["kickoff"].iloc[0]
        print(f"\n=== {match_name}  (kickoff {kickoff:%Y-%m-%d %H:%M UTC}) ===")

        specs = []
        for _, market in group.iterrows():
            try:
                specs.append((market["market_id"], market["question"],
                              parse(market["question"], teams=ALL_TEAM_NAMES)))
            except Exception as exc:
                attention.append(f"{match_name}: CANNOT PARSE {market['question']!r} ({exc})")
                print(f"  SKIP   {market['question'][:70]}  <- parse {exc.__class__.__name__}")

        # Confirmed lineups (replaces hand-typed benchings): within the pull window
        # of kickoff, fetch the posted XI -> expected_minutes BEFORE building the
        # pricer, which then reads data/match_lineups.yaml for this match.
        if kickoff - now <= LINEUP_PULL_WINDOW:
            _pull_confirmed_lineups(match_name, kickoff, specs, attention)
        _flag_stale_overrides(match_name, specs, attention)

        try:
            pricer = build_pricer(match_name, kickoff)
        except Exception as exc:
            attention.append(f"{match_name}: odds/pricer failed — {exc}")
            print(f"  SKIPPED: {exc}")
            continue

        # bot 1 = stable v4: price, print, and flag low-confidence player rates.
        # One unpriceable market must never sink the whole unattended run.
        for mid, q, spec in specs:
            try:
                p = pricer.price(spec)
            except Exception as exc:
                attention.append(f"{match_name}: CANNOT PRICE {q!r} ({exc.__class__.__name__}: {exc})")
                print(f"  SKIP   {q[:70]}  <- {exc.__class__.__name__}")
                continue
            probs[1][mid] = p
            flag = ""
            if spec.player:
                src = pricer.players.rates(spec.player).source
                if src == "prior":
                    flag = "  <- prior only: research this player"
                    attention.append(f"{match_name}: {spec.player} priced on prior (no data)")
                elif src == "statsbomb" and pricer.players.is_sparse(spec.player):
                    flag = "  <- sparse/old data: verify current form"
                    attention.append(f"{match_name}: {spec.player} on sparse StatsBomb history "
                                     f"— fetch current club form (update_player_rates) or research")
                elif (spec.family in ("player_scores", "player_scores_or_assists")
                      and pricer.players.unrepresentative_scorer(spec.player)):
                    flag = "  <- zero-goal history (Larin class): likely UNDER-rates a real scorer"
                    attention.append(f"{match_name}: {spec.player} has 0 goals in enough StatsBomb "
                                     f"apps — goal_share floored to the prior; research his real "
                                     f"scoring rate or set a market scorer_prop_prob (+ scorer_prop_match)")
            print(f"  {p:5.3f}  {q[:70]}{flag}")

        # bot 2 = experiment: reprice the SAME markets/odds under the override
        # constants; if no experiment, it mirrors bot 1 exactly (current behaviour).
        if 2 in bots:
            if experiment:
                with _experimental(experiment):
                    for mid, q, spec in specs:
                        if mid in probs[1]:
                            try:
                                probs[2][mid] = pricer.price(spec)
                            except Exception:
                                probs[2][mid] = probs[1][mid]
            else:
                probs[2] = dict(probs[1])

    actions = {}
    if any(probs.values()):
        # Submit each bot its OWN dict explicitly (bot 1 stable, bot 2 experiment) —
        # never rely on account-level propagation. ORDER MATTERS: bot 2 (experiment)
        # FIRST, bot 1 (stable v4) LAST — so if any cross-contamination ever occurred,
        # the stable version is the final write and wins on both. (Computation order is
        # unchanged: bot 1 is priced first, above.)
        submit_order = ([2] if 2 in bots else []) + ([1] if 1 in bots else [])
        for bot in submit_order:
            tag = f" [EXPERIMENT: {experiment['name']}]" if (bot == 2 and experiment) else " [stable v4]"
            print(f"\n{'[dry run] ' if dry else ''}submitting {len(probs[bot])} predictions to bot {bot}{tag}...")
            client = pc if bot == 1 else ProbabilityCup(bot=bot)
            try:
                report = client.submit(probs[bot], dry_run=dry)
            except Exception as exc:
                attention.append(f"bot {bot}: submission failed — {exc}")
                print(f"  bot {bot}: FAILED — {exc}")
                continue
            a = report["action"].value_counts().to_dict()
            print(f"  bot {bot}: {a}")
            if bot == 1:
                actions = a
            errors = report[report["action"] == "error"]
            if not errors.empty:
                attention.append(f"bot {bot}: {len(errors)} submission errors")
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

    # No-miss backlog: scan EVERY market (full snapshot, not just this run's
    # horizon) so any unpriceable template is always recorded — nothing overlooked.
    try:
        from scan_backlog import scan_and_update
        bl = scan_and_update(pc.markets_df())
        if bl["open_backlog"]:
            print(f"\nBACKLOG: {bl['unparseable_markets']} unpriced markets across "
                  f"{bl['open_backlog']} open templates -> data/logs/unparsed_backlog.md")
    except Exception as exc:
        attention.append(f"backlog scan failed — {exc}")

    _log_run(args.hours, dry, markets, actions, attention)


def _log_run(hours: float, dry: bool, markets: pd.DataFrame,
             actions: dict, attention: list[str]) -> None:
    """Every run leaves a permanent one-block trace in data/logs/runs.log,
    regardless of whether it was invoked by cron, an agent, or by hand.
    (Per-market submission detail goes to submissions.csv via the domain layer.)"""
    RUNS_LOG.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    # markets may be empty (e.g. a --defer-to-agent run that did nothing), in
    # which case it has no columns — guard the match count.
    n_matches = markets["match_name"].nunique() if "match_name" in markets else 0
    lines = [
        f"[{stamp}] daily_run hours={hours:g} mode={'dry-run' if dry else 'SUBMIT'}",
        f"  matches={n_matches} markets={len(markets)} "
        f"actions={actions or 'none'}",
    ]
    lines += [f"  attention: {item}" for item in attention]
    with RUNS_LOG.open("a") as fh:
        fh.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
