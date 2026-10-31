"""Backtest: would Polymarket's 1-HOUR-BEFORE-KICKOFF odds have beaten our
model on the settled games? Focus on team_win (moneyline) and CORNERS (our
weakest family — the one we'd most want to anchor). Uses CLOB price-history for
the pre-match price (no leakage). Also reports how INFORMATIVE the Polymarket
price was (distance from 0.50) — a thinly-traded market stuck at ~0.50 adds
nothing even if it exists.
"""

import ast
import math
import urllib.request
import json
from datetime import datetime, timedelta, timezone

from probcup import config
import numpy as np
import pandas as pd

from probcup.sportspredict.cup import ProbabilityCup
from probcup.questions.parser import parse
from probcup.questions.teams import ALL_TEAM_NAMES, match_teams, names_match

KICKOFFS = {
    "KOR vs CZE": datetime(2026, 6, 12, 2, 0), "CAN vs BIH": datetime(2026, 6, 12, 19, 0),
    "USA vs PAR": datetime(2026, 6, 13, 1, 0), "QAT vs SUI": datetime(2026, 6, 13, 19, 0),
    "BRA vs MAR": datetime(2026, 6, 13, 22, 0), "Haiti vs SCO": datetime(2026, 6, 14, 1, 0),
    "AUS vs TUR": datetime(2026, 6, 14, 4, 0), "GER vs Curacao": datetime(2026, 6, 14, 17, 0),
    "NED vs JPN": datetime(2026, 6, 14, 20, 0), "CIV vs ECU": datetime(2026, 6, 14, 23, 0),
    "SWE vs TUN": datetime(2026, 6, 15, 2, 0),
}


def _get(url):
    try:
        return json.load(urllib.request.urlopen(
            urllib.request.Request(url, headers={"User-Agent": "bt/1.0"}), timeout=25))
    except Exception:
        return []


def price_1h(token: str, ko: datetime) -> float | None:
    t = int((ko.replace(tzinfo=timezone.utc) - timedelta(hours=1)).timestamp())
    h = _get(f"https://clob.polymarket.com/prices-history?market={token}&startTs={t-10800}&endTs={t+300}&fidelity=15")
    h = h.get("history", []) if isinstance(h, dict) else []
    pre = [p for p in h if p["t"] <= t + 300]
    return float(pre[-1]["p"]) if pre else None


def toks(m):
    tk = m.get("clobTokenIds")
    return ast.literal_eval(tk) if isinstance(tk, str) else tk


def fit_poisson_lambda(survivals: dict[int, float]) -> float | None:
    """survivals = {k: P(X >= k)} from O/U lines. Least-squares fit of a Poisson
    mean over the available points (corner counts are ~Poisson at the team level)."""
    pts = [(k, p) for k, p in survivals.items() if 0.02 < p < 0.98]
    if len(pts) < 1:
        return None
    from math import exp, factorial
    def surv(lam, k):  # P(X >= k)
        return 1 - sum(exp(-lam) * lam ** i / factorial(i) for i in range(k))
    best, berr = None, 1e9
    lam = 0.2
    while lam <= 9.0:
        err = sum((surv(lam, k) - p) ** 2 for k, p in pts)
        if err < berr:
            berr, best = err, lam
        lam += 0.05
    return best


def p_a_more_than_b(lam_a: float, lam_b: float) -> float:
    """P(A > B) for two independent Poisson corner counts."""
    from math import exp, factorial
    def pmf(lam, k):
        return exp(-lam) * lam ** k / factorial(k)
    return sum(pmf(lam_a, a) * pmf(lam_b, b)
               for a in range(25) for b in range(a))


def build_slug_map() -> dict:
    """Map our match name -> polymarket game slug, by matching team names."""
    evs = []
    for c in ("true", "false"):
        evs += _get(f"https://gamma-api.polymarket.com/events?series_slug=soccer-fifwc&closed={c}&limit=300")
    games = [e for e in evs if e.get("gameId") and " vs" in e.get("title", "")
             and not any(suf in e.get("slug", "") for suf in
                         ("halftime", "exact", "second", "corners", "-total", "-team", "-shots",
                          "-goals", "-assists", "-spread", "-btts", "-more", "-first", "-player"))]
    out = {}
    for match in KICKOFFS:
        home, away = match_teams(match)
        for e in games:
            t = e.get("title", "")
            left, _, right = t.partition(" vs")
            if names_match(home, left.strip()) and names_match(away, right.lstrip(". ").strip()):
                out[match] = e["slug"]
                break
    return out


def main():
    pc = ProbabilityCup(bot=1)
    s = pc.results_df()
    s["p"] = s["probability_submitted"].map(lambda v: v if v <= 1 else v / 100)
    s["o"] = s.apply(lambda r: 1 if abs(round(r.p + np.sqrt(r.brier_score)) - (r.p + np.sqrt(r.brier_score)))
                     < abs(round(r.p - np.sqrt(r.brier_score)) - (r.p - np.sqrt(r.brier_score))) else 0, axis=1)
    s["spec"] = s["question"].map(lambda q: parse(q, teams=ALL_TEAM_NAMES))

    slugs = build_slug_map()
    print(f"matched {len(slugs)}/{len(KICKOFFS)} games to Polymarket slugs")
    rows = []
    for match, slug in slugs.items():
        ko = KICKOFFS[match]
        home, away = match_teams(match)
        ours = s[s["question"].apply(lambda q: home in q or away in q)]
        # moneyline (game event)
        ml = {}
        ev = _get(f"https://gamma-api.polymarket.com/events?slug={slug}")
        if ev:
            for m in ev[0].get("markets", []):
                if "win on" in m.get("question", "") and toks(m):
                    ml[m["question"].split(" win on")[0].replace("Will ", "").strip()] = toks(m)[0]
        # corners event -> team O/U ladders {team: {line: token}}, total {line: token}
        team_ou: dict[str, dict[float, str]] = {}
        total_ou: dict[float, str] = {}
        ce = _get(f"https://gamma-api.polymarket.com/events?slug={slug}-total-corners")
        if ce:
            for m in ce[0].get("markets", []):
                q = m.get("question", "")
                tk = toks(m)
                if not tk or ":" not in q or "Half" in q:
                    continue
                body = q.split(":", 1)[1].strip()          # "Sweden O/U 4.5 Corners" | "O/U 7.5 Total Corners"
                if "O/U" not in body:
                    continue
                left, _, rest = body.partition("O/U")
                try:
                    line = float(rest.strip().split()[0])
                except (ValueError, IndexError):
                    continue
                if "Total Corners" in body and not left.strip():
                    total_ou[line] = tk[0]
                elif left.strip():
                    team_ou.setdefault(left.strip(), {})[line] = tk[0]

        def team_lambda(team_name: str) -> float | None:
            """Fit a Poisson corner mean for `team_name` from its O/U ladder 1h pre."""
            key = next((k for k in team_ou if names_match(team_name, k)), None)
            if key is None:
                return None
            surv = {}
            for line, tok in team_ou[key].items():
                p = price_1h(tok, ko)
                if p is not None and 0 < p < 1:
                    surv[int(line + 0.5)] = p     # O/U 4.5 over == P(corners >= 5)
            return fit_poisson_lambda(surv) if surv else None

        for _, r in ours.iterrows():
            sp = r["spec"]
            pm = None
            if sp.family == "team_win":
                tok = next((t for nm, t in ml.items() if names_match(sp.team, nm)), None)
                if tok:
                    pm = price_1h(tok, ko)
            elif sp.family == "team_count_ge" and sp.stat == "corners" and sp.period == "match":
                # "team X+ corners" -> team O/U line (X-0.5) over
                key = next((k for k in team_ou if names_match(sp.team, k)), None)
                tok = team_ou.get(key, {}).get(sp.threshold - 0.5) if key else None
                if tok:
                    pm = price_1h(tok, ko)
            elif sp.family == "total_count_ge" and sp.stat == "corners" and sp.period == "match":
                pm = price_1h(total_ou[sp.threshold - 0.5], ko) if (sp.threshold - 0.5) in total_ou else None
            elif sp.family == "team_more_count_than" and sp.stat == "corners" and sp.period == "match":
                # head-to-head full match: derive P(team > opp) from both O/U ladders
                la, lb = team_lambda(sp.team), team_lambda(sp.opponent)
                if la is not None and lb is not None:
                    pm = p_a_more_than_b(la, lb)
            # NOTE: 1H/2H head-to-head corners are NOT derivable — Polymarket only
            # lists per-team O/U for the FULL match and total (not per-team) per half.
            if pm is not None and 0 < pm < 1:
                rows.append((match, sp.family + "/" + (sp.stat or "") + "/" + sp.period,
                             r["p"], pm, int(r["o"])))

    if not rows:
        print("no mappable markets found")
        return
    d = pd.DataFrame(rows, columns=["match", "family", "ours", "pm", "o"])
    d["b_ours"] = (d.ours - d.o) ** 2
    d["b_pm"] = (d.pm - d.o) ** 2
    d["pm_info"] = (d.pm - 0.5).abs()  # how far from coin-flip
    print(f"\n=== {len(d)} settled markets with a Polymarket 1h-pre price ===")
    for fam, g in d.groupby("family"):
        print(f"  {fam:22s} n={len(g):2d}  ours={g.b_ours.mean():.4f}  PM={g.b_pm.mean():.4f}  "
              f"PM_informativeness(|p-.5|)={g.pm_info.mean():.3f}")
    print(f"  {'OVERALL':22s} n={len(d):2d}  ours={d.b_ours.mean():.4f}  PM={d.b_pm.mean():.4f}")
    print("\nsample rows:")
    print(d[["match", "family", "ours", "pm", "o"]].head(12).to_string(index=False))
    d.to_csv(config.CACHE / "backtest_polymarket.csv", index=False)


if __name__ == "__main__":
    main()
