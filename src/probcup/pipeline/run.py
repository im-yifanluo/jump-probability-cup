"""The production run: price + submit every match kicking off within the horizon.

This is the deterministic 90% of the daily routine — designed to be run by a
scheduled agent (or by hand) once or more per day:

1. refresh the market snapshot (new matches/markets appear as rounds open)
2. for each match with kickoff within --hours: pull the confirmed lineup if
   kickoff is near, fetch live odds, price all open markets
3. submit via the upsert path — first touch creates predictions, every later
   run PATCHes only the values that changed (so "initial" and "final" passes
   are the same command at different times). Bot 2 (the experiment) is
   submitted first and bot 1 (stable) last.
4. print a per-match report + anything needing human/agent attention
   (players without data, odds fallbacks, submission errors)

Usage:
    probcup run --hours 48 --dry-run          # show the plan
    probcup run --hours 48 --submit --agent   # do it (the agent's passes)
"""

import argparse
from datetime import datetime, timedelta, timezone

import pandas as pd

from probcup import config
from probcup.ops import heartbeat
from probcup.pipeline.build import build_pricer
from probcup.pipeline.experiment import experimental, load_experiment
from probcup.questions.parser import parse
from probcup.questions.teams import ALL_TEAM_NAMES
from probcup.sportspredict.cup import ProbabilityCup

RUNS_LOG = config.RUNS_LOG
LINEUP_PULL_WINDOW = timedelta(minutes=100)  # pull the confirmed XI within this of kickoff


def _available_bots() -> list[int]:
    """Bots with a configured key — every update is submitted to each of them
    explicitly (no reliance on account-level propagation)."""
    import os
    env = os.environ
    env_file = config.ENV_FILE.read_text() if config.ENV_FILE.exists() else ""
    bots = [b for b in (1, 2)
            if env.get(f"SPORTSPREDICT_KEY_{b}") or f"SPORTSPREDICT_KEY_{b}=" in env_file]
    return bots or [1]


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


def _pull_confirmed_lineups(match_name, kickoff, specs, attention) -> None:
    """For an imminent kickoff, pull the posted XI from ESPN and write confirmed
    expected_minutes (data/inputs/match_lineups.yaml) BEFORE pricing — replacing hand-typed
    benchings. Fail-safe: writes nothing unless a real XI is posted, so an early or
    missing lineup keeps the all-90 default; unmatched players are flagged, never
    silently benched."""
    players = {sp.player for _, _, sp in specs if sp.player}
    if not players:
        return
    try:
        from probcup.sources.espn_lineups import pull_lineup, write_lineup
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
    ov_path = config.PLAYER_OVERRIDES
    ml_path = config.MATCH_LINEUPS
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


def main(argv: list[str] | None = None):
    ap = argparse.ArgumentParser(prog="probcup run", description=__doc__)
    ap.add_argument("--hours", type=float, default=48, help="kickoff horizon")
    ap.add_argument("--submit", action="store_true", help="actually submit (else dry-run)")
    ap.add_argument("--dry-run", action="store_true", help="alias for not passing --submit")
    ap.add_argument("--agent", action="store_true",
                    help="mark this as the agent's run: writes the heartbeat the "
                         "missed-run watchdog checks. Used by the playbooks.")
    args = ap.parse_args(argv)
    dry = not args.submit

    pc = ProbabilityCup(bot=1)
    bots = _available_bots()
    markets = upcoming_matches(pc, args.hours)
    if markets.empty:
        print(f"No open markets with kickoff in the next {args.hours:.0f}h.")
        # A no-op agent run still RAN — record the heartbeat so the watchdog does
        # not false-alarm "morning run missed" on days with no games in horizon.
        if args.agent and not dry:
            heartbeat.touch()
        _log_run(args.hours, dry, pd.DataFrame(), {"noop": 1}, [])
        return

    experiment = load_experiment(bots)
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
        # pricer, which then reads data/inputs/match_lineups.yaml for this match.
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
                                     f"— fetch current club form (probcup player-rates) or research")
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
                with experimental(experiment):
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

    # An agentic run that actually submitted drops the heartbeat; the watchdog
    # raises an alarm when a scheduled run leaves it stale.
    if args.agent and not dry:
        heartbeat.touch()

    # No-miss backlog: scan EVERY market (full snapshot, not just this run's
    # horizon) so any unpriceable template is always recorded — nothing overlooked.
    try:
        from probcup.pipeline.backlog import scan_and_update
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
    # markets may be empty (a run with no games in its horizon), in which case
    # it has no columns — guard the match count.
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
