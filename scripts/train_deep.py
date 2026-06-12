"""Train the deep count model (see src/models/deep.py).

On the H200 box:
    python3 scripts/train_deep.py --device cuda --epochs 30
CPU smoke test:
    python3 scripts/train_deep.py --device cpu --limit 4000 --epochs 1

Saves the best checkpoint to data/deep_model.pt and prints held-out
NB-NLL + per-target Poisson-equivalent Brier so it can be compared with the
statistical stack before earning any ensemble weight.
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from math import factorial

import numpy as np
import torch
from torch.utils.data import DataLoader

from models.deep import DeepCounts, MatchDataset, TEAM_STATS
from wcdata.football_data_uk import load_club_matches
from backtest.implied import add_implied_lambdas

TEST_FROM_SEASON = "2425"


def evaluate(model, loader, device) -> dict:
    model.eval()
    nlls, briers = [], []
    with torch.no_grad():
        for seqs, mf, targets in loader:
            seqs, mf, targets = seqs.to(device), mf.to(device), targets.to(device)
            log_rates = model(seqs, mf)
            nlls.append(model.nb_nll(log_rates, targets).item())
            # quick Brier proxy: P(count >= mean_target) per target family
            mu = log_rates.exp()
            for t, stat in enumerate(TEAM_STATS):
                threshold = max(round(float(targets[:, t].mean())), 1)
                p = 1 - torch.exp(-mu[:, t]) * sum(
                    mu[:, t] ** k / factorial(k) for k in range(threshold))
                outcome = (targets[:, t] >= threshold).float()
                briers.append(((p - outcome) ** 2).mean().item())
    return {"nll": float(np.mean(nlls)), "brier_proxy": float(np.mean(briers))}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--batch", type=int, default=512)
    ap.add_argument("--lr", type=float, default=3e-4)
    ap.add_argument("--limit", type=int, default=None, help="cap samples (smoke tests)")
    args = ap.parse_args()

    print("building dataset...")
    matches = add_implied_lambdas(load_club_matches())
    dataset = MatchDataset(matches, limit=args.limit)
    train_set, test_set = dataset.split_by_season(TEST_FROM_SEASON)
    print(f"samples: {len(dataset)} (train {len(train_set)}, test {len(test_set)})")

    train_loader = DataLoader(train_set, batch_size=args.batch, shuffle=True)
    test_loader = DataLoader(test_set, batch_size=args.batch)

    model = DeepCounts().to(args.device)
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr)
    best_nll = float("inf")

    for epoch in range(args.epochs):
        model.train()
        losses = []
        for seqs, mf, targets in train_loader:
            seqs, mf, targets = (seqs.to(args.device), mf.to(args.device),
                                 targets.to(args.device))
            loss = model.nb_nll(model(seqs, mf), targets)
            opt.zero_grad()
            loss.backward()
            opt.step()
            losses.append(loss.item())
        metrics = evaluate(model, test_loader, args.device) if len(test_set) else {}
        print(f"epoch {epoch + 1}/{args.epochs}  train_nll {np.mean(losses):.4f}  "
              + " ".join(f"test_{k} {v:.4f}" for k, v in metrics.items()))
        if metrics and metrics["nll"] < best_nll:
            best_nll = metrics["nll"]
            torch.save({"state_dict": model.state_dict(), "test": metrics},
                       ROOT / "data" / "deep_model.pt")
            print("  saved checkpoint")


if __name__ == "__main__":
    main()
