"""Strategy backtest engine over REAL settled Probability Cup markets.

Simulates how a candidate strategy WOULD have scored on the actual matches we
played, with the rigor the small-sample setting demands:

  * exact RBP arithmetic on every settled market (you/crowd/outcome from
    docs/archive/crowd_history.md via `probcup ledger` -> data/cache/rbp_full.csv), with the
    market's realized KO weight recovered from its displayed RBP;
  * GAME-CLUSTERED bootstrap CIs — markets within a match are correlated
    (one blowout settles 15 markets the same way), so we resample MATCHES,
    never individual markets;
  * chronological WALK-FORWARD for any fitted parameter — params are chosen
    only from games strictly before the game being scored (expanding window,
    no look-ahead);
  * an A/B REPLAY of bot 2 vs bot 1 from the platform's own settled records —
    those are REAL model outputs (not price-space proxies), the gold standard
    for validating model-constant changes.

HONEST LIMITS: price-space transforms (below) cannot perfectly replicate a
model-CONSTANT change (a mean-scale acts before the NB/Poisson tail, not on
the price). They answer the strategy-level question — "how much should we
trust/press our deviation from the crowd, per family?" — while constant-level
questions go to the bot-2 live A/B (subcommand `ab`) or the StatsBomb replay
scripts (research/validations/, research/derivations/).

Usage:
  probcup replay policies            # test the built-in policy grid
  probcup replay alpha               # deviation-amplification curve (walk-forward)
  probcup replay ab                  # bot2-vs-bot1 replay on settled divergents
  probcup replay policies --recent 30   # only the most recent 30 games
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import pandas as pd

from probcup import config

RBP = config.RBP_LEDGER
N_BOOT = 2000
SEED = 7


# --------------------------------------------------------------------------- #
# data

def load(recent: int | None = None) -> pd.DataFrame:
    d = pd.read_csv(RBP)
    d["famkey"] = d.family + d.stat.map(lambda s: "/" + str(s) if isinstance(s, str) and s else "")
    # realized KO weight: displayed rbp / unweighted rbp (1x group, 2x KO, 3x final)
    unw = 100 * ((d.outcome - d.crowd) ** 2 - (d.outcome - d.you) ** 2)
    with np.errstate(divide="ignore", invalid="ignore"):
        w = np.where(np.abs(unw) > 1e-9, d.rbp / unw, 1.0)
    d["w"] = np.clip(np.round(w, 0), 1, 3)
    d["dev"] = d.you - d.crowd
    if recent:
        keep = sorted(d["order"].unique())[-recent:]
        d = d[d["order"].isin(keep)].copy()
    return d


def rbp_of(d: pd.DataFrame, p: np.ndarray) -> float:
    """Exact weighted RBP a prediction vector would have realized."""
    p = np.clip(p, 0.01, 0.99)
    return float((100 * ((d.outcome - d.crowd) ** 2 - (d.outcome - p) ** 2) * d.w).sum())


# --------------------------------------------------------------------------- #
# policies: row-level price transforms, optionally per-family, optionally fitted

@dataclass
class Policy:
    name: str
    transform: Callable[[pd.DataFrame], np.ndarray]      # df -> new price vector
    families: list[str] | None = None                    # None = all markets
    note: str = ""

    def apply(self, d: pd.DataFrame) -> np.ndarray:
        p = d.you.to_numpy().astype(float).copy()
        mask = d.famkey.isin(self.families).to_numpy() if self.families else np.ones(len(d), bool)
        if mask.any():
            p[mask] = np.clip(self.transform(d[mask]), 0.01, 0.99)
        return p


def clustered_delta_ci(d: pd.DataFrame, pol: Policy, n_boot: int = N_BOOT) -> tuple[float, float, float]:
    """Delta RBP (policy - submitted) with a game-clustered bootstrap CI."""
    p = pol.apply(d)
    base = rbp_of(d, d.you.to_numpy())
    delta = rbp_of(d, p) - base
    games = d["match"].unique()
    rng = np.random.default_rng(SEED)
    by_game = {g: idx for g, idx in d.groupby("match").indices.items()}
    boots = np.empty(n_boot)
    you = d.you.to_numpy(); out = d.outcome.to_numpy(); crowd = d.crowd.to_numpy(); w = d.w.to_numpy()
    per_mkt = (100 * ((out - crowd) ** 2 - (out - np.clip(p, .01, .99)) ** 2) * w
               - 100 * ((out - crowd) ** 2 - (out - you) ** 2) * w)
    game_sums = {g: per_mkt[idx].sum() for g, idx in by_game.items()}
    vals = np.array(list(game_sums.values()))
    for i in range(n_boot):
        boots[i] = rng.choice(vals, size=len(vals), replace=True).sum()
    return delta, float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))


# --------------------------------------------------------------------------- #
# built-in policy grid

def builtin_policies(d: pd.DataFrame) -> list[Policy]:
    pols: list[Policy] = []
    # deviation amplification/shrink toward the crowd — the core strategy dial
    for a in (0.75, 0.9, 1.1, 1.25, 1.5):
        pols.append(Policy(f"deviate x{a}", lambda x, a=a: x.crowd + a * (x.you - x.crowd),
                           note="press/shrink ALL deviations from the crowd"))
    # per-family shrink of known drift families toward their base
    pols.append(Policy("ht_tied shrink50", lambda x: 0.30 + 0.5 * (x.you - 0.30),
                       families=["ht_tied"], note="drift family: over-predicts HT draws"))
    # very-low-zone press (the Isak/Brobbey zone: you<=0.15 & below crowd)
    def low_press(x):
        p = x.you.to_numpy().copy()
        m = (p <= 0.15) & (x.dev.to_numpy() < -0.03)
        p[m] = p[m] * 0.6
        return p
    pols.append(Policy("very-low press x0.6", low_press, note="halve the sub-15% deviate-low prices"))
    # crowd blends (sanity: expected to LOSE per prior analysis)
    for wgt in (0.25, 0.5):
        pols.append(Policy(f"crowd blend {wgt}", lambda x, wgt=wgt: (1 - wgt) * x.you + wgt * x.crowd,
                           note="anchor toward crowd (expect negative)"))
    return pols


# --------------------------------------------------------------------------- #
# walk-forward deviation-amplification (fitted alpha, no look-ahead)

def walk_forward_alpha(d: pd.DataFrame, grid=(0.75, 0.9, 1.0, 1.1, 1.25, 1.5),
                       min_train_games: int = 25) -> None:
    orders = sorted(d["order"].unique())
    total = np.zeros(len(grid)); wf_delta = 0.0; scored = 0
    picks = []
    for k, o in enumerate(orders):
        train = d[d["order"] < o]
        test = d[d["order"] == o]
        if train["match"].nunique() < min_train_games or test.empty:
            continue
        deltas = []
        for a in grid:
            p = np.clip(train.crowd + a * (train.you - train.crowd), .01, .99)
            deltas.append(rbp_of(train, p) - rbp_of(train, train.you.to_numpy()))
        best = grid[int(np.argmax(deltas))]
        picks.append(best)
        p_test = np.clip(test.crowd + best * (test.you - test.crowd), .01, .99)
        wf_delta += rbp_of(test, p_test) - rbp_of(test, test.you.to_numpy())
        scored += len(test)
    print(f"walk-forward deviation-amplification: {scored} markets scored OOS")
    print(f"  chosen alpha by era: {pd.Series(picks).value_counts().to_dict()}")
    print(f"  OOS delta vs submitted: {wf_delta:+.1f} RBP")
    print("  (positive = we should press deviations harder; negative = current sizing is right)")


# --------------------------------------------------------------------------- #
# bot2-vs-bot1 replay (real model outputs from the platform)

def ab_replay() -> None:
    from probcup.sportspredict.cup import ProbabilityCup
    r1 = ProbabilityCup(bot=1).results_df()[["market_id", "question", "probability_submitted", "brier_score", "created_date"]]
    r1 = r1.rename(columns={"probability_submitted": "p1", "brier_score": "br1"})
    r2 = ProbabilityCup(bot=2).results_df()[["market_id", "probability_submitted", "brier_score"]]
    r2 = r2.rename(columns={"probability_submitted": "p2", "brier_score": "br2"})
    m = r1.merge(r2, on="market_id")
    dvg = m[m.p1 != m.p2].copy()
    dvg["dBrier"] = dvg.br2 - dvg.br1
    print(f"bot2-vs-bot1 replay: {len(m)} settled matched, {len(dvg)} divergent")
    print(f"  net dBrier {dvg.dBrier.sum():+.3f} (negative = bot2/probe better) | bot2 wins {(dvg.dBrier<0).sum()}/{len(dvg)}")
    print("  by created_date (newest 15):")
    for _, x in dvg.sort_values("created_date").tail(15).iterrows():
        print(f"    p1={x.p1:3.0f} p2={x.p2:3.0f} dBr={x.dBrier:+.3f}  {x.question[:56]}")


# --------------------------------------------------------------------------- #

def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="probcup replay", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["policies", "alpha", "ab"])
    ap.add_argument("--recent", type=int, default=None, help="restrict to the most recent N games")
    args = ap.parse_args(argv)

    if args.mode == "ab":
        ab_replay(); return

    d = load(args.recent)
    print(f"{len(d)} settled markets over {d['match'].nunique()} games"
          f"{f' (most recent {args.recent})' if args.recent else ''}; "
          f"submitted RBP baseline {rbp_of(d, d.you.to_numpy()):+.1f}\n")

    if args.mode == "alpha":
        walk_forward_alpha(d); return

    rows = []
    for pol in builtin_policies(d):
        delta, lo, hi = clustered_delta_ci(d, pol)
        n = len(d[d.famkey.isin(pol.families)]) if pol.families else len(d)
        rows.append(dict(policy=pol.name, n=n, delta=round(delta, 1),
                         ci_lo=round(lo, 1), ci_hi=round(hi, 1),
                         verdict=("WIN" if lo > 0 else "LOSE" if hi < 0 else "inconclusive"),
                         note=pol.note))
    print(pd.DataFrame(rows).to_string(index=False))


if __name__ == "__main__":
    main()
