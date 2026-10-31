"""Assemble the pricer for one match, and the `probcup price` report.

Pipeline: SportsPredict snapshot -> ESPN odds (manual YAML fallback) ->
odds-anchored ScorelineModel + club-fitted CountPricer + PlayerModel ->
blended probability per market -> rationale report + predictions CSV.

Usage:
    probcup price "CAN vs BIH"           # report only
    probcup price "CAN vs BIH" --save    # also write CSV

Manual odds fallback (data/inputs/manual_odds.yaml), used when ESPN has no line:
    CAN vs BIH: {p_home: 0.55, p_draw: 0.25, p_away: 0.20, total_goals: 2.4}
(orientation: p_home refers to the first-listed team in the match name).
"""

import argparse

import pandas as pd
import yaml

from probcup import config
from probcup.models import calibration as cal
from probcup.models.counts import CountPricer, load_count_models, tournament_aggregates
from probcup.models.goals import ScorelineModel
from probcup.models.players import PlayerModel
from probcup.models.pricer import MatchPricer
from probcup.pipeline.overrides import match_minutes, referee_card_factor, scorer_props
from probcup.questions.parser import parse
from probcup.questions.teams import ALL_TEAM_NAMES, match_teams, names_match
from probcup.sources.espn_odds import MatchOdds, fetch_match_odds
from probcup.sources.polymarket import fetch_match_1x2
from probcup.sources.statsbomb import build_aggregates
from probcup.sportspredict.cup import ProbabilityCup

MANUAL_ODDS = config.MANUAL_ODDS


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


def build_pricer(match_name: str, kickoff,
                 anchors: tuple[float, float, float] | None = None) -> MatchPricer:
    """One fixture's pricer. `anchors` = (p_home, p_draw, total_goals) skips the
    live odds lookup (used by the offline demo); by default they come from
    get_anchors: manual override, else ESPN blended with Polymarket."""
    home_full, away_full = match_teams(match_name)
    p_home, p_draw, total = anchors if anchors is not None else get_anchors(match_name, kickoff)

    # Tournament aggregates drive every half-split, including goals — the
    # 2H goal share at international tournaments (~0.60) is materially higher
    # than the club default, and half-based markets are priced off it.
    params = load_count_models()
    team_halves, sb_players = build_aggregates()
    aggregates = tournament_aggregates(team_halves)
    h2_goals = aggregates["stats"]["goals"]["h2_share"]

    goals = ScorelineModel.from_market(p_home, p_draw, total, h2_share=h2_goals)
    players = PlayerModel(sb_players, match_minutes=match_minutes(match_name))

    card_factor = referee_card_factor(match_name)
    counts = CountPricer(goals.lam_home, goals.lam_away, params, aggregates,
                         card_factor=card_factor)
    base_goals = ScorelineModel(cal.TOURNAMENT_AVG_LAM, cal.TOURNAMENT_AVG_LAM, goals.rho, h2_goals)
    baseline = MatchPricer(
        home_full, away_full, base_goals,
        CountPricer(cal.TOURNAMENT_AVG_LAM, cal.TOURNAMENT_AVG_LAM, params, aggregates,
                    card_factor=card_factor),
        players)
    return MatchPricer(home_full, away_full, goals, counts, players,
                       scorer_props=scorer_props(match_name), baseline=baseline)


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


def main(argv: list[str] | None = None):
    ap = argparse.ArgumentParser(prog="probcup price", description=__doc__)
    ap.add_argument("match", help='match name as in the snapshot, e.g. "CAN vs BIH"')
    ap.add_argument("--save", action="store_true", help="write data/cache/predictions_<match>.csv")
    args = ap.parse_args(argv)

    df = price_match(args.match)
    if args.save:
        out = config.CACHE / f"predictions_{args.match.replace(' ', '_')}.csv"
        out.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(out, index=False)
        print(f"\nsaved {out}")


if __name__ == "__main__":
    main()
