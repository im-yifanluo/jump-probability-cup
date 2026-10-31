"""Ensemble: blend deep-model count rates into the statistical stack.

Gating rule (decided by data, not enthusiasm): the deep model only receives
weight on a stat if its held-out club Brier beats the statistical model's on
that same stat. Until a trained checkpoint exists (research/rejected/deep_model/train_deep.py on
the H200), `deep_rate_adjustments` returns None and the pricer runs purely
statistical.

Inference caveat, by design: for World Cup fixtures we rarely have the dense
last-10-match stat sequences the encoder saw in training (national teams play
sparsely and qualifiers lack stat coverage). Missing form is encoded as the
neutral sequence, which collapses the deep prediction toward its odds-feature
baseline — i.e. when the deep model knows nothing extra, it changes nothing.
"""

from __future__ import annotations


from probcup import config
import numpy as np

CHECKPOINT = config.MODELS / "deep_model.pt"
# Two-man rule for the deep model: a checkpoint alone is not enough (smoke
# tests also write one). The model only activates once its held-out Brier has
# been compared against the statistical stack and a human creates this marker.
APPROVAL_MARKER = config.MODELS / "deep_model_APPROVED"


def deep_rate_adjustments(lam_home: float, lam_away: float,
                          home_form: np.ndarray | None = None,
                          away_form: np.ndarray | None = None) -> dict | None:
    """Multiplicative adjustments {(side, stat): factor} from the deep model,
    or None unless a trained AND approved checkpoint exists."""
    if not (CHECKPOINT.exists() and APPROVAL_MARKER.exists()):
        return None

    import torch
    from deep import SEQ_LEN, SEQ_FEATURES, TEAM_STATS, DeepCounts

    model = DeepCounts()
    state = torch.load(CHECKPOINT, map_location="cpu")
    model.load_state_dict(state["state_dict"])
    model.eval()

    neutral = np.zeros((SEQ_LEN, len(SEQ_FEATURES)), dtype=np.float32)
    seqs = torch.from_numpy(np.stack([
        home_form if home_form is not None else neutral,
        away_form if away_form is not None else neutral,
    ]))[None]
    match_feats = torch.log(torch.tensor([[lam_home, lam_away]], dtype=torch.float32))

    with torch.no_grad():
        rates = model(seqs, match_feats).exp().numpy()[0]

    out = {}
    for i, stat in enumerate(TEAM_STATS):
        out[("home", stat)] = float(rates[i])
        out[("away", stat)] = float(rates[len(TEAM_STATS) + i])
    return out
