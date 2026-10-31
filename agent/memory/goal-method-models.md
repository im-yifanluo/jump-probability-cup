---
name: goal-method-models
description: "own-goal / header / outside-the-box goal markets: P(>=1 type | total goals G) models, LOTO-validated, deployed to both bots 2026-06-30"
metadata: 
  node_type: memory
  type: project
  originSessionId: edc0be82-f7ce-4493-be8b-e55f21f51524
---

Built + deployed 2026-06-30 (Yifan request: "build the own-goal / header / outside-the-box
models... backed by SOTA research, rigorous backtesting; if not catastrophic (<30pt downside),
deploy it"). These three families were previously **forfeited** (unparseable → no submission).

**Model:** each is `P(>=1 goal of that method | total REGULATION goals G)`, integrated over the
match's total-goals pmf at price time — the **substitute-scores pattern** (`p_sub_goal`). Conditioning
on G is the key: more goals ⇒ higher chance one is an own-goal/header/outside-box. Tables live in
`src/models/counts.py` (`_OWN_GOAL`, `_HEADER_GOAL`, `_OUTSIDE_GOAL` indexed by G, + `p_goal_method`);
priced via `MatchPricer._p_goal_method(table)`; 3 parser rules ("Will an own goal be scored?", "...a
header goal...", "...a goal from outside the penalty area/box?") + 3 pricer dispatch cases.

**Derivation + backtest:** `scripts/derive_goal_methods.py` from StatsBomb shot events (body_part=Head;
own = "Own Goal Against"; outside-box = location outside x∈[102,120],y∈[18,62] on the 120×80 pitch;
periods {1,2} only). **LOTO** (leave-one-tournament-out) Brier beats a flat base rate for all three:
own 0.124→0.109, header 0.331→0.196, outside 0.242→0.170 — conditioning on G wins OOS, calibrated,
downside well under the 30-pt gate (rare bounded events). Re-run the script to refresh the tables.

**Deployed prices (sanity):** MEX/ECU (total≈2.0, low-scoring) own 0.10 + outside-box 0.20 — BELOW the
base rate because the model responds to match context, not just spitting the unconditional rate; POR/CRO
header 0.34. MEX/ECU settles 2026-07-01 05:00Z = first live OOS check (compare our Brier vs the field).

Both bots carry these (correctness, not a strategy gamble). See [[prediction-system]], [[strategy-v4-doc]],
[[player-prop-prior-fallback]] (the two name-capture bug fixes shipped in the same pass).
