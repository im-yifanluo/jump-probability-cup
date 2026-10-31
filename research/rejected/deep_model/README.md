# Rejected: deep form model

**Idea.** A GRU encodes each team's last ten matches (stat lines, venue, recency); an MLP over
both encodings plus the odds-implied goal rates predicts negative-binomial rates for ten
targets ([deep.py](deep.py), [train_deep.py](train_deep.py)). The odds go in as features, so
the network only has to learn residual structure.

**Gate.** [ensemble.py](ensemble.py) gives it weight on a stat only if it beats the
statistical stack on held-out Brier, and only after a manual approval marker exists.

**Why it never shipped.** National teams play sparsely and qualifiers lack stat coverage, so
World Cup fixtures rarely have the dense sequences the encoder needs; with missing form it
collapses to its odds-only baseline, which the statistical stack already prices. No checkpoint
was ever approved.

    pip install -e ".[deep]"
    python research/rejected/deep_model/train_deep.py --device cpu --limit 4000 --epochs 1
