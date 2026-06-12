"""Deep model: multi-task team-form encoder over club match history.

Each sample is one match. Both teams contribute a sequence of their last
SEQ_LEN matches (stat lines + venue + recency); a shared GRU encodes each
sequence, and an MLP over [home_emb, away_emb, odds-implied rates] predicts
negative-binomial rates for ten targets: {home, away} x {goals, corners,
cards, sot, fouls}. Dispersion is a learned per-target parameter.

The odds-implied rates are inputs on purpose: the literature says odds are
the strongest single signal, so the network is trained to learn *residual*
structure on top of them (form, style, tempo interactions). In the ensemble
it only earns weight where it beats the statistical stack on held-out Brier.

Train on the H200:  python3 scripts/train_deep.py --device cuda
Smoke-test on CPU:  python3 scripts/train_deep.py --device cpu --limit 5000 --epochs 1
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from torch.utils.data import Dataset

ROOT = Path(__file__).resolve().parent.parent.parent

SEQ_LEN = 10
TEAM_STATS = ["goals", "corners", "cards", "sot", "fouls"]  # prediction targets
SEQ_FEATURES = (  # per past match, from the team's perspective
    [f"{s}_for" for s in TEAM_STATS] + [f"{s}_against" for s in TEAM_STATS]
    + ["was_home", "log_days_ago"]
)
MATCH_FEATURES = ["log_lam_home", "log_lam_away"]
N_TARGETS = 2 * len(TEAM_STATS)


# --------------------------------------------------------------------------- #
# Dataset construction from the club parquet (+ implied lambdas)
# --------------------------------------------------------------------------- #

def build_samples(matches: pd.DataFrame) -> pd.DataFrame:
    """Wide frame with per-team history columns; one row per usable match."""
    df = matches.dropna(subset=["lam_home", "lam_away", "home_corners",
                                "home_cards", "home_sot", "home_fouls"]).copy()
    df = df.sort_values("date").reset_index(drop=True)

    # Long per-team view to build rolling histories
    sides = []
    for side, opp in (("home", "away"), ("away", "home")):
        s = pd.DataFrame({
            "match_idx": df.index, "team": df[f"{side}_team"], "date": df["date"],
            "was_home": 1.0 if side == "home" else 0.0,
        })
        for stat in TEAM_STATS:
            s[f"{stat}_for"] = df[f"{side}_{stat}"].to_numpy()
            s[f"{stat}_against"] = df[f"{opp}_{stat}"].to_numpy()
        sides.append(s)
    long = pd.concat(sides).sort_values(["team", "date"]).reset_index(drop=True)
    return df, long


class MatchDataset(Dataset):
    """Tensorizes matches whose both teams have >= SEQ_LEN prior matches."""

    def __init__(self, matches: pd.DataFrame, limit: int | None = None):
        df, long = build_samples(matches)
        history: dict[str, list[np.ndarray]] = {}
        feats_by_match: dict[int, dict[str, np.ndarray]] = {}

        seq_cols = [c for c in SEQ_FEATURES if c != "log_days_ago"]
        for row in long.itertuples():
            team_hist = history.setdefault(row.team, [])
            if len(team_hist) >= SEQ_LEN:
                window = np.stack(team_hist[-SEQ_LEN:])
                feats_by_match.setdefault(row.match_idx, {})[
                    "home" if row.was_home else "away"] = window
            vec = np.array([getattr(row, c) for c in seq_cols] + [row.date.toordinal()],
                           dtype=np.float32)
            team_hist.append(vec)

        self.samples = []
        for idx, sides in feats_by_match.items():
            if "home" not in sides or "away" not in sides:
                continue
            m = df.loc[idx]
            seqs = []
            for window in (sides["home"], sides["away"]):
                w = window.copy()
                days_ago = np.log1p(m["date"].toordinal() - w[:, -1])
                w[:, -1] = days_ago  # replace raw ordinal with recency feature
                seqs.append(w)
            targets = np.array(
                [m[f"home_{s}"] for s in TEAM_STATS] + [m[f"away_{s}"] for s in TEAM_STATS],
                dtype=np.float32)
            match_feats = np.log(np.array([m["lam_home"], m["lam_away"]], dtype=np.float32))
            self.samples.append((np.stack(seqs), match_feats, targets, m["season"]))
            if limit and len(self.samples) >= limit:
                break

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, i):
        seqs, match_feats, targets, _ = self.samples[i]
        return (torch.from_numpy(seqs), torch.from_numpy(match_feats),
                torch.from_numpy(targets))

    def split_by_season(self, test_from: str):
        train_idx = [i for i, s in enumerate(self.samples) if s[3] < test_from]
        test_idx = [i for i, s in enumerate(self.samples) if s[3] >= test_from]
        return torch.utils.data.Subset(self, train_idx), torch.utils.data.Subset(self, test_idx)


# --------------------------------------------------------------------------- #
# Model
# --------------------------------------------------------------------------- #

class FormEncoder(nn.Module):
    def __init__(self, hidden: int = 64):
        super().__init__()
        self.gru = nn.GRU(len(SEQ_FEATURES), hidden, batch_first=True)

    def forward(self, x):  # (batch, SEQ_LEN, features)
        _, h = self.gru(x)
        return h.squeeze(0)


class DeepCounts(nn.Module):
    def __init__(self, hidden: int = 64, mlp: int = 128):
        super().__init__()
        self.encoder = FormEncoder(hidden)
        self.head = nn.Sequential(
            nn.Linear(2 * hidden + len(MATCH_FEATURES), mlp), nn.ReLU(),
            nn.Linear(mlp, mlp), nn.ReLU(),
            nn.Linear(mlp, N_TARGETS),
        )
        self.log_alpha = nn.Parameter(torch.full((N_TARGETS,), -2.0))

    def forward(self, seqs, match_feats):
        # seqs: (batch, 2, SEQ_LEN, features)
        home = self.encoder(seqs[:, 0])
        away = self.encoder(seqs[:, 1])
        log_rates = self.head(torch.cat([home, away, match_feats], dim=1))
        return log_rates.clamp(-3, 4)

    def nb_nll(self, log_rates, targets):
        """Negative binomial NLL with per-target learned dispersion."""
        mu = log_rates.exp()
        alpha = self.log_alpha.exp()
        size = 1.0 / alpha
        log_p = (torch.lgamma(targets + size) - torch.lgamma(size)
                 - torch.lgamma(targets + 1)
                 + size * torch.log(size / (size + mu))
                 + targets * torch.log(mu / (size + mu)))
        return -log_p.mean()
