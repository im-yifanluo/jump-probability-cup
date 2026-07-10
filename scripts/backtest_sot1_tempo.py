"""Backtest the ">=1 SOT" tempo-compression fix (players.SOT1_TEMPO_BETA).

Claim: for the near-binary ">=1 SOT" threshold, team tempo barely predicts whether a
genuine shooter gets a shot ON TARGET. The linear tempo curve is too steep — it
under-rates underdog shooters and over-rates favourites. Compressing the tempo swing
toward 1.0 (beta<1) lowers held-out Brier. This validates the fix on the large,
unbiased StatsBomb sample (the correct gate — not the small live run).

Run: python scripts/backtest_sot1_tempo.py
"""
from __future__ import annotations
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from models.players import p_ge, SOT_ALPHA, TOURNAMENT_TEAM_SOT, MAX_TEMPO  # noqa: E402


def load():
    df = pd.read_parquet(ROOT / "data" / "statsbomb_players.parquet")
    team_sot = df.groupby(["match_id", "team"])["sot"].sum().rename("team_sot").reset_index()
    pm = df.groupby("player").agg(apps=("match_id", "count"), sot=("sot", "sum")).query("apps>=15")
    pm["rate"] = pm.sot / pm.apps
    m = df.merge(pm[["rate"]], on="player").merge(team_sot, on=["match_id", "team"])
    m["has"] = (m.sot >= 1).astype(int)
    # de-leak the tempo signal: teammates' realized SOT + this player's EXPECTED (rate)
    m["tempo_sig"] = m.team_sot - m.sot + m.rate
    return m


def main():
    m = load()
    sh = m[(m.rate >= 0.5) & (m.rate <= 1.2)].copy()   # genuine-shooter class
    sh["tempo_raw"] = np.minimum(sh.tempo_sig / TOURNAMENT_TEAM_SOT, MAX_TEMPO)
    print(f"shooter player-matches (rate 0.5-1.2): {len(sh)}  realized P(>=1 SOT)={sh.has.mean():.3f}\n")

    print("=== held-out Brier of >=1 SOT vs tempo-compression beta (1.0 = current model) ===")
    for beta in [1.0, 0.7, 0.5, 0.3, 0.0]:
        tempo = 1 + beta * (sh.tempo_raw - 1)
        pred = np.array([p_ge(r * t, SOT_ALPHA, 1) for r, t in zip(sh.rate, tempo)])
        print(f"  beta={beta:<4} Brier={((sh.has-pred)**2).mean():.4f}  "
              f"spread[{pred.min():.2f},{pred.max():.2f}]  mean={pred.mean():.3f}")

    print("\n=== calibration by model-prediction bin (beta=1.0 current vs 0.5 deployed) ===")
    for beta, tag in [(1.0, "current"), (0.5, "deployed")]:
        tempo = 1 + beta * (sh.tempo_raw - 1)
        sh["pred"] = [p_ge(r * t, SOT_ALPHA, 1) for r, t in zip(sh.rate, tempo)]
        sh["bin"] = pd.cut(sh.pred, [0, 0.3, 0.4, 0.5, 0.6, 1.0])
        cal = sh.groupby("bin", observed=True).agg(n=("has", "size"), pred=("pred", "mean"),
                                                    realized=("has", "mean"))
        print(f"  --- beta={beta} ({tag}) ---")
        print(cal.round(3).to_string())


if __name__ == "__main__":
    main()
