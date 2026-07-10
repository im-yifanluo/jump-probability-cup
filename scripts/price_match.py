"""Price all open markets for one World Cup match.

Pipeline: SportsPredict snapshot -> ESPN odds (manual YAML fallback) ->
odds-anchored ScorelineModel + club-fitted CountPricer + PlayerModel ->
blended probability per market -> rationale report + predictions CSV.

Usage:
    python3 scripts/price_match.py "CAN vs BIH"           # report only
    python3 scripts/price_match.py "CAN vs BIH" --save    # also write CSV

Manual odds fallback (data/manual_odds.yaml), used when ESPN has no line:
    CAN vs BIH: {p_home: 0.55, p_draw: 0.25, p_away: 0.20, total_goals: 2.4}
(orientation: p_home refers to the first-listed team in the match name).
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import pandas as pd
import yaml

from models.counts import CountPricer, load_count_models, tournament_aggregates
from models.goals import ScorelineModel
from models.players import OVERRIDES_PATH, PlayerModel
from models.pricer import MatchPricer
from probability_cup import ProbabilityCup
from questions.parser import parse
from questions.teams import ALL_TEAM_NAMES, match_teams, names_match
from wcdata.odds_feeds import MatchOdds, fetch_match_odds
from wcdata.polymarket import fetch_match_1x2
from wcdata.statsbomb import build_aggregates

MANUAL_ODDS = ROOT / "data" / "manual_odds.yaml"
REFEREE_OVERRIDES = ROOT / "data" / "referee_overrides.yaml"
MATCH_LINEUPS = ROOT / "data" / "match_lineups.yaml"
TOURNAMENT_AVG_LAM = 1.30  # structural baseline goal rate per side
TOURNAMENT_CARDS_PER_MATCH = 3.46  # StatsBomb mean total cards/match (referee baseline)

# NOTE (2026-06-14): a favorite-longshot extremizing of the odds was tried and
# REMOVED. The bias is real but its benefit in modern de-margined odds is
# negligible (+0.00005/market out-of-sample), and it hurt on the live sample.
# We anchor on the de-margined odds as-is — accurate, lean, no overfitting.


def _card_factor(match_name: str) -> float:
    """Referee strictness multiplier for a match's card markets, from
    data/referee_overrides.yaml ({match: {referee, cards_per_game}}). 1.0 when
    the referee is unknown — the agent researches and fills it pre-match."""
    if not REFEREE_OVERRIDES.exists():
        return 1.0
    refs = yaml.safe_load(REFEREE_OVERRIDES.read_text()) or {}
    entry = refs.get(match_name)
    if not entry or "cards_per_game" not in entry:
        return 1.0
    # ASYMMETRIC CAP at 1.0 (2026-06-19, RBP-validated). A referee multiplier
    # ABOVE 1.0 (strict ref) consistently OVER-predicted cards and was our single
    # worst RBP leak — card markets flipped +15.5 -> -12.4/market once aggressive
    # referee factors went in; the backtest shows capping at 1.0 recovers +37.7
    # RBP. We still let a LENIENT ref reduce cards (we over-predict, so that's
    # safe) but never let a strict ref inflate them. The base card model is
    # already StatsBomb-calibrated; a properly-calibrated upward model could
    # return later. (The remaining low-event level is handled by TOURNAMENT_INTENSITY.)
    return min(1.0, max(0.6, float(entry["cards_per_game"]) / TOURNAMENT_CARDS_PER_MATCH))


def get_anchors(match_name: str, kickoff) -> tuple[float, float, float]:
    """(p_home_team, p_draw, total_goals) for the first-listed team."""
    home_full, away_full = match_teams(match_name)
    if MANUAL_ODDS.exists():
        manual = yaml.safe_load(MANUAL_ODDS.read_text()) or {}
        if match_name in manual:
            m = manual[match_name]
            # Manual odds are an intentional OVERRIDE of the live line (kept first
            # so we can pin a known-better price), but SELF-EXPIRING + LOUD so a
            # forgotten entry can't silently ride forward over good live odds:
            # honour it only when its kickoff_date matches (or is absent), and
            # always announce it.
            tag = m.get("kickoff_date")
            ko_date = kickoff.strftime("%Y-%m-%d") if hasattr(kickoff, "strftime") else None
            if tag is not None and tag != ko_date:
                print(f"  ** manual_odds for {match_name} EXPIRED (tagged {tag}, this "
                      f"kickoff {ko_date}) — ignoring it, using live odds **")
            else:
                print(f"  ** USING MANUAL ODDS OVERRIDE for {match_name}: "
                      f"home {m['p_home']} / draw {m['p_draw']} / total {m['total_goals']} **")
                return m["p_home"], m["p_draw"], m["total_goals"]

    odds: MatchOdds = fetch_match_odds(kickoff.strftime("%Y%m%d"), home_full.split()[0])
    # ESPN/DraftKings 1X2 oriented to the first-listed team
    if names_match(home_full, odds.home_name):
        book = (odds.p_home, odds.p_draw, odds.p_away)
    elif names_match(home_full, odds.away_name):
        book = (odds.p_away, odds.p_draw, odds.p_home)
    else:
        raise SystemExit(f"Cannot orient ESPN teams {odds.home_name}/{odds.away_name} "
                         f"to {home_full}/{away_full} — extend EXTERNAL_ALIASES")
    print(f"odds: {odds.source} ({book[0]:.3f}/{book[1]:.3f}/{book[2]:.3f}, total {odds.total_goals:.2f})")

    # Blend with Polymarket prediction-market 1X2 (independent, sharp). Average
    # the two de-margined distributions; if they disagree sharply, flag it.
    p_team, p_draw = book[0], book[1]
    try:
        pm = fetch_match_1x2(home_full, away_full)
    except Exception:
        pm = None
    if pm is not None:
        print(f"odds: Polymarket ({pm.p_home:.3f}/{pm.p_draw:.3f}/{pm.p_away:.3f}, vol ${pm.volume/1e6:.1f}M)")
        if abs(book[0] - pm.p_home) > 0.10:
            print(f"  ** sources disagree on home win by {abs(book[0]-pm.p_home):.2f} — check for stale odds **")
        p_team = (book[0] + pm.p_home) / 2
        p_draw = (book[1] + pm.p_draw) / 2
        print(f"odds: BLENDED home {p_team:.3f} / draw {p_draw:.3f}")
    return p_team, p_draw, odds.total_goals


def _scorer_props_from_overrides(match_name: str | None = None) -> dict[str, float]:
    """De-margined anytime-scorer props from the overrides (key scorer_prop_prob).
    Markets beat models, so when one exists MatchPricer uses it outright.

    A prop is OPPONENT-SPECIFIC (a de-margined price for one fixture), so it must
    be SCOPED to the match it was researched for via `scorer_prop_match`, or it
    rides into the team's next game and goes stale (the 06-29 Gyökeres/Balogun
    class). With a match_name given:
      tagged & matches this match -> used
      tagged & a DIFFERENT match  -> EXPIRED, skipped (loud), reverts to the model
      untagged                    -> used but LOUDLY flagged (please scope it)
    match_name=None keeps the legacy all-props behaviour for callers without it."""
    if not OVERRIDES_PATH.exists():
        return {}
    overrides = yaml.safe_load(OVERRIDES_PATH.read_text()) or {}
    props = {}
    for name, o in overrides.items():
        if not (isinstance(o, dict) and "scorer_prop_prob" in o):
            continue
        tag = o.get("scorer_prop_match")
        if match_name is None or tag == match_name:
            props[name] = o["scorer_prop_prob"]
        elif tag is None:
            props[name] = o["scorer_prop_prob"]
            print(f"  ** scorer_prop for {name} is UNSCOPED (no scorer_prop_match) — it "
                  f"applies to ANY match; add scorer_prop_match: {match_name!r} to scope it **")
        else:
            print(f"  ** scorer_prop for {name} EXPIRED (researched for {tag!r}, not "
                  f"{match_name!r}) — ignoring it, using the model **")
    return props


def _match_minutes(match_name: str) -> dict[str, float]:
    """Confirmed expected_minutes for THIS match from data/match_lineups.yaml
    (written by pull_lineups.py from the posted XI). Empty when no lineup has been
    pulled — every player then defaults to 90 (everyone starts), the safe prior."""
    if not MATCH_LINEUPS.exists():
        return {}
    data = yaml.safe_load(MATCH_LINEUPS.read_text()) or {}
    return (data.get(match_name) or {}).get("minutes") or {}


def build_pricer(match_name: str, kickoff) -> MatchPricer:
    home_full, away_full = match_teams(match_name)
    p_home, p_draw, total = get_anchors(match_name, kickoff)

    # Tournament aggregates drive every half-split, including goals — the
    # 2H goal share at international tournaments (~0.60) is materially higher
    # than the club default, and half-based markets are priced off it.
    params = load_count_models()
    team_halves, sb_players = build_aggregates()
    aggregates = tournament_aggregates(team_halves)
    h2_goals = aggregates["stats"]["goals"]["h2_share"]

    goals = ScorelineModel.from_market(p_home, p_draw, total, h2_share=h2_goals)
    players = PlayerModel(sb_players, match_minutes=_match_minutes(match_name))

    card_factor = _card_factor(match_name)
    counts = CountPricer(goals.lam_home, goals.lam_away, params, aggregates,
                         card_factor=card_factor)
    base_goals = ScorelineModel(TOURNAMENT_AVG_LAM, TOURNAMENT_AVG_LAM, goals.rho, h2_goals)
    baseline = MatchPricer(
        home_full, away_full, base_goals,
        CountPricer(TOURNAMENT_AVG_LAM, TOURNAMENT_AVG_LAM, params, aggregates,
                    card_factor=card_factor),
        players)
    return MatchPricer(home_full, away_full, goals, counts, players,
                       scorer_props=_scorer_props_from_overrides(match_name), baseline=baseline)


def build_sim_pricer(match_name: str, kickoff, n_sims: int = 40000):
    """Monte Carlo pricer for one match — same odds-anchoring and count setup
    as build_pricer, but derived markets come from a game-state-aware match
    simulation (captures leading-team-cedes-possession dynamics)."""
    from models.simulator import MatchSimulator
    from models.sim_pricer import SimMatchPricer

    home_full, away_full = match_teams(match_name)
    p_home, p_draw, total = get_anchors(match_name, kickoff)
    params = load_count_models()
    team_halves, sb_players = build_aggregates()
    aggregates = tournament_aggregates(team_halves)
    h2_goals = aggregates["stats"]["goals"]["h2_share"]
    goals = ScorelineModel.from_market(p_home, p_draw, total, h2_share=h2_goals)
    counts = CountPricer(goals.lam_home, goals.lam_away, params, aggregates,
                         card_factor=_card_factor(match_name))
    result = MatchSimulator(goals, counts, n_sims=n_sims).run()
    return SimMatchPricer(home_full, away_full, result, PlayerModel(sb_players),
                          counts, goals, scorer_props=_scorer_props_from_overrides())


def price_match(match_name: str) -> pd.DataFrame:
    pc = ProbabilityCup(bot=1)
    markets = pc.markets_df()
    mine = markets[markets["match_name"] == match_name]
    if mine.empty:
        raise SystemExit(f"No open markets for {match_name!r}")
    kickoff = mine["kickoff"].iloc[0]

    pricer = build_pricer(match_name, kickoff)
    print(f"\n{match_name}  kickoff {kickoff:%Y-%m-%d %H:%M UTC}")
    print(f"goal rates: {pricer.home_name} {pricer.goals.lam_home:.2f}, "
          f"{pricer.away_name} {pricer.goals.lam_away:.2f}, rho {pricer.goals.rho:.3f}\n")

    rows = []
    for _, market in mine.iterrows():
        spec = parse(market["question"], teams=ALL_TEAM_NAMES)
        p = pricer.price(spec)
        note = spec.family + (f"/{spec.stat}" if spec.stat else "")
        if spec.player:
            note += f" [{pricer.players.rates(spec.player).source}]"
        rows.append({"market_id": market["market_id"], "question": market["question"],
                     "probability": round(float(p), 3), "model": note})

    df = pd.DataFrame(rows).sort_values("probability", ascending=False)
    print(df[["probability", "question", "model"]].to_string(index=False, max_colwidth=70))
    return df


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("match", help='match name as in the snapshot, e.g. "CAN vs BIH"')
    ap.add_argument("--save", action="store_true", help="write data/predictions_<match>.csv")
    args = ap.parse_args()

    df = price_match(args.match)
    if args.save:
        out = ROOT / "data" / f"predictions_{args.match.replace(' ', '_')}.csv"
        df.to_csv(out, index=False)
        print(f"\nsaved {out}")


if __name__ == "__main__":
    main()
