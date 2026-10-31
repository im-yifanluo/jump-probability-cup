"""Leave-one-tournament-out validation of the player shots-on-target model.

Reproduces the result that justified the 2026-06-13 SOT model rewrite: each
StatsBomb tournament is held out in turn, the model is built on the rest, and
P(>=1 SOT) is scored (Brier) on the held-out player-matches. Prints the
shipping model's Brier against the previous model and naive baselines.

Run: python3 research/validations/player_sot_loto.py
"""


import numpy as np


from probcup.models.calibration import PRIOR_SOT_RATE
from probcup.models.players import PlayerModel
from probcup.sources.statsbomb import build_aggregates


def main():
    _, players = build_aggregates()
    new_errs, old_errs, prior_errs = [], [], []

    for t in players["tournament"].unique():
        train = players[players["tournament"] != t]
        test = players[players["tournament"] == t]
        model = PlayerModel(train)
        for _, row in test.iterrows():
            resolved = model.resolve(row["player"])
            if resolved is None:
                continue
            hit = int(row["sot"] >= 1)
            # Shipping model (neutral match tempo, 90 minutes)
            new_errs.append((model.p_sot_ge(row["player"], 1, 3.70) - hit) ** 2)
            # Previous model: Poisson on heavily-shrunk un-discounted rate
            h = model._history.loc[resolved]
            old_rate = (h["sot"] + 0.80 * 6) / (h["apps"] + 6)
            old_errs.append((1 - np.exp(-old_rate) - hit) ** 2)
            # Baseline: always the forward prior
            prior_errs.append((1 - np.exp(-PRIOR_SOT_RATE) - hit) ** 2)

    print(f"Leave-one-tournament-out P(>=1 SOT), n={len(new_errs)} player-matches:")
    print(f"  shipping model     Brier {np.mean(new_errs):.4f}")
    print(f"  previous model     Brier {np.mean(old_errs):.4f}")
    print(f"  always-prior       Brier {np.mean(prior_errs):.4f}")
    print(f"  coin flip          Brier 0.2500")
    assert np.mean(new_errs) < np.mean(old_errs), "regression: new model worse than old"
    assert np.mean(new_errs) < 0.20, "regression: SOT Brier above 0.20"
    print("\nOK: shipping model beats the previous model and the 0.20 bar.")


if __name__ == "__main__":
    main()
