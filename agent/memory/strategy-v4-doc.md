---
name: strategy-v4-doc
description: Canonical v4 strategy reference doc (docs/strategy_v4.md) — what the live system is and why it beats v3
metadata: 
  node_type: memory
  type: reference
  originSessionId: edc0be82-f7ce-4493-be8b-e55f21f51524
---

The complete current strategy is documented at `docs/strategy_v4.md` (created 2026-06-25,
user-requested reference). Treat it as canonical for "what is our strategy / why v4 > v3."

Key points it records (see [[edge-analysis-and-fixes]] for the full backtest):
- Core: the score is a PROPER scoring rule → the whole game is CALIBRATION, not boldness.
  Anchor to markets; fade where the crowd over-predicts; don't manufacture confidence.
- v4 vs v3: replaced v3's uniform TOURNAMENT_INTENSITY 0.90 (blunt, halved our edge,
  #50→#100) with per-stat {cards 0.78, sot 1.00, corners 1.05, offsides 0.90} (+112 RBP
  OOS) + coupled TOURNAMENT_TEAM_SOT 3.70→4.11 so the SOT change doesn't inflate player
  props. WC-2026 is high-attacking/low-card (goals 3.12/g, fouls 24.4, yellows 2.17/g).
- REJECTED (don't re-litigate): extremizing (proper-scoring math + OOS ~0), the game-state
  sim (mean-preserving, +0.0003 Brier), the SOT-correlation term (residual corr ≈0).
- Discipline: tinker less; override only on a confirmed fact the market missed; never go
  above the crowd on a low-base-rate prop because one input looks high.

Update the doc (not just memory) when the model changes; re-fit the multipliers as KO data
settles. See also [[override-levers]], [[daily-automation]].
