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
from wcdata.statsbomb import build_aggregates

MANUAL_ODDS = ROOT / "data" / "manual_odds.yaml"
TOURNAMENT_AVG_LAM = 1.30  # structural baseline goal rate per side


def get_anchors(match_name: str, kickoff) -> tuple[float, float, float]:
    """(p_home_team, p_draw, total_goals) for the first-listed team."""
    home_full, away_full = match_teams(match_name)
    if MANUAL_ODDS.exists():
        manual = yaml.safe_load(MANUAL_ODDS.read_text()) or {}
        if match_name in manual:
            m = manual[match_name]
            return m["p_home"], m["p_draw"], m["total_goals"]

    odds: MatchOdds = fetch_match_odds(kickoff.strftime("%Y%m%d"), home_full.split()[0])
    if names_match(home_full, odds.home_name):
        p_team = odds.p_home
    elif names_match(home_full, odds.away_name):
        p_team = odds.p_away
    else:
        raise SystemExit(f"Cannot orient ESPN teams {odds.home_name}/{odds.away_name} "
                         f"to {home_full}/{away_full} — extend EXTERNAL_ALIASES")
    print(f"odds source: {odds.source}  ({odds.home_name} {odds.p_home:.3f} / "
          f"draw {odds.p_draw:.3f} / {odds.away_name} {odds.p_away:.3f}, "
          f"total {odds.total_goals:.2f})")
    return p_team, odds.p_draw, odds.total_goals


def _scorer_props_from_overrides() -> dict[str, float]:
    """De-margined anytime-scorer prop probabilities researched into the
    overrides YAML (key: scorer_prop_prob). Markets beat models — when one
    exists, MatchPricer uses it outright for that player's scorer market."""
    if not OVERRIDES_PATH.exists():
        return {}
    overrides = yaml.safe_load(OVERRIDES_PATH.read_text()) or {}
    return {name: o["scorer_prop_prob"] for name, o in overrides.items()
            if isinstance(o, dict) and "scorer_prop_prob" in o}


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
    players = PlayerModel(sb_players)

    counts = CountPricer(goals.lam_home, goals.lam_away, params, aggregates)
    base_goals = ScorelineModel(TOURNAMENT_AVG_LAM, TOURNAMENT_AVG_LAM, goals.rho, h2_goals)
    baseline = MatchPricer(
        home_full, away_full, base_goals,
        CountPricer(TOURNAMENT_AVG_LAM, TOURNAMENT_AVG_LAM, params, aggregates),
        players)
    return MatchPricer(home_full, away_full, goals, counts, players,
                       scorer_props=_scorer_props_from_overrides(), baseline=baseline)


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
