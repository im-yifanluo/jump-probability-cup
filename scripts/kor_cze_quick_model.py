"""Time-critical v0 pricing for KOR vs CZE (kickoff 2026-06-12 02:00Z).

Anchored on bookmaker consensus (bet365/FanDuel/Kalshi, 2026-06-11):
1X2 ~ KOR 36.5% / draw 31% / CZE 33%, totals line 2.5 (balanced -> ~2.6 xG).
Goal markets are priced from independent Poissons fitted to those anchors;
side stats (corners, cards, offsides, SOT, penalty) use World Cup base rates
with a Skellam for the head-to-head comparisons; player props use
share-of-team-rate logic. Each market's reasoning is inline.
"""

import sys
from math import exp, factorial
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from probability_cup import ProbabilityCup

MAX_GOALS = 12  # truncation for Poisson sums (P(X>12) negligible)


def pois(k: int, lam: float) -> float:
    return exp(-lam) * lam**k / factorial(k)


def p_more(lam_a: float, lam_b: float) -> float:
    """P(A > B) for independent Poissons (Skellam tail by enumeration)."""
    return sum(
        pois(a, lam_a) * pois(b, lam_b)
        for a in range(MAX_GOALS + 1)
        for b in range(MAX_GOALS + 1)
        if a > b
    )


def p_geq(k: int, lam: float) -> float:
    return 1 - sum(pois(i, lam) for i in range(k))


def fit_goal_rates(p_home_win: float, total: float) -> tuple[float, float]:
    """Find (lam_home, lam_away) summing to `total` that reproduce the
    bookmaker home-win probability under independent Poissons."""
    lo, hi = 0.1, total - 0.1
    for _ in range(40):
        lam_h = (lo + hi) / 2
        if p_more(lam_h, total - lam_h) < p_home_win:
            lo = lam_h
        else:
            hi = lam_h
    return lam_h, total - lam_h


# --- Anchors from bookmaker consensus -------------------------------------
P_KOR_WIN, TOTAL_GOALS = 0.365, 2.60
lam_kor, lam_cze = fit_goal_rates(P_KOR_WIN, TOTAL_GOALS)
print(f"Fitted goal rates: KOR {lam_kor:.2f}, CZE {lam_cze:.2f} "
      f"(implied KOR win {p_more(lam_kor, lam_cze):.3f})")

H2 = 0.54  # share of goals scored in second halves (empirical, stable)

probs = {
    # Q: Will South Korea win the match? -> bookmaker consensus directly.
    "Will South Korea win the match?": P_KOR_WIN,

    # Q: BTTS AND 3+ total goals -> exact enumeration over the joint Poisson.
    "Will both teams score AND the match have 3 or more total goals?": sum(
        pois(a, lam_kor) * pois(b, lam_cze)
        for a in range(MAX_GOALS + 1) for b in range(MAX_GOALS + 1)
        if a >= 1 and b >= 1 and a + b >= 3
    ),

    # Q: KOR scores in the 2nd half -> Poisson with 2nd-half share of lam_kor.
    "Will South Korea score in the second half?": p_geq(1, lam_kor * H2),

    # Q: Son Heung-min anytime scorer -> priced directly off prop markets:
    # FanDuel +195 / Caesars +205 (~33% implied), ~30% after stripping the
    # typical prop-market margin. Confirmed starter, penalty + FK taker.
    "Will Son Heung-min score a goal (excluding own goals)?": 0.30,

    # Q: Schick 1+ shot on target -> starting CZE striker averages ~1.2 SOT
    # per full match; haircut for early-sub risk.
    "Will Patrik Schick have at least 1 shot on target?": 0.95 * p_geq(1, 1.2),

    # Q: CZE more corners than KOR at halftime -> ~10 corners/match, ~45%
    # before HT, split evenly between two even teams: lam ~2.3 each.
    "At halftime, will Czechia have more corner kicks than South Korea?": p_more(2.3, 2.3),

    # Q: KOR offside 2+ times -> WC team average ~1.9-2.0 offsides/match;
    # no team-specific data found, slight shade down from the raw prior.
    "Will South Korea be caught offside 2 or more times?": p_geq(2, 1.9),

    # Q: 2+ cards in 2nd half -> both sides trend low-card (CZE last 5 and
    # 9 of KOR's last 10 all under 4.5 cards): ~3.4 cards/match, ~62% in
    # second halves -> lam ~2.1.
    "Will there be 2 or more total cards shown in the second half?": p_geq(2, 2.1),

    # Q: penalty awarded -> recent WCs: ~0.36 pens/match (23 in 64 in 2022).
    "Will a penalty kick be awarded in the match?": p_geq(1, 0.36),

    # Q: KOR more SOT than CZE in 2nd half -> ~4.3 SOT/team/match, ~55% in
    # 2nd half: lam ~2.4 each; tiny edge to KOR as slight favourite.
    "Will South Korea have more shots on target than Czechia in the second half?": p_more(2.5, 2.3),
}


def main():
    pc = ProbabilityCup(bot=1)
    markets = pc.markets_df()
    match_markets = markets[markets["match_name"] == "KOR vs CZE"]

    submission = {}
    for _, row in match_markets.iterrows():
        if row["question"] not in probs:
            raise SystemExit(f"Unpriced question: {row['question']}")
        submission[row["market_id"]] = probs[row["question"]]
        print(f"{probs[row['question']]:.3f}  {row['question']}")

    report = pc.submit(submission)
    print(report[["market_id", "probability", "action", "error"]].to_string(index=False))


if __name__ == "__main__":
    main()
