---
name: r32-rbp-analysis
description: "R32 RBP diagnosis (2026-07-01): deviate-low is the edge (+479); leaks = volume-total under-prediction + player 1+SOT floors; Fix 1 (SOT1 tempo compression) deployed"
metadata: 
  node_type: memory
  type: project
  originSessionId: edc0be82-f7ce-4493-be8b-e55f21f51524
---

Deep R32 analysis (2026-07-01, Yifan asked why some games have negative RBP + whether pre-game
adjustments hurt). Data: `scripts/parse_history_rbp.py` -> `data/rbp_full.csv`; R32-only slices
`data/rbp_r32.csv` / `_by_game.csv` / `_by_family.csv`. R32 = the 7 games in history.md's
`## Round of 32` section (Canada/SA, BRA/JPN, GER/PAR, NED/MAR, CIV/NOR, FRA/SWE, MEX/ECU).

**Core diagnosis (by mode):** R32 net +435. Deviating LOWER than the crowd is our ENGINE (+479 RBP
on 50 mkts, +9.6 each). Losses = -232 on just 9 mkts, two modes: (a) **volume-total under-prediction**
(total SOT/offsides/shots — we systematically under-count shot-volume; -100), (b) **player common-event
floors** (1+ SOT for starters priced ~0.10-0.33 when a starter's 1+ SOT is ~0.49; -132). Both negative
games are ENTIRELY these: BRA/JPN -41 (8+SOT -58, an already-pre-fix loss), CIV/NOR -22 (Amad Diallo
1+SOT -89). Strip the 9 leaks -> both games flip +57/+67. So NOT a broad decline — 2 concentrated modes.

**Adjustments hypothesis (Yifan): CONFIRMED but targeted.** The worst single loss (Diallo -89) was a
bench-minutes projection (~20 min) applied to a player who STARTED (10% req expected_minutes~20; at
90min he's ~0.30-0.46 = the crowd's 43%). Market-anchoring (Dembélé 0.104->0.46 toward the market, he
didn't score) is the same family. BUT adjustments are net-POSITIVE in aggregate — only two types hurt:
minutes/bench floors before the XI is CONFIRMED, and crowd-anchoring that surrenders the deviate-low edge.

**Edge (do NOT shrink):** deviate-low wins on team SOT (+81), player_scores (+69), total cards when low
(+48), player 2+SOT (+43), braces, timing-goals. Goal-method models won their first settle (own +6.3,
outside-box +12.4). RBP is vs the FIELD — honest low probs on events the crowd over-rates = pure edge.

**FIX 1 DEPLOYED to bot 1 (player 1+SOT tempo compression):** the LINEAR tempo curve
(rate * min(team_sot/4.11, 1.6)) is too steep for the near-binary 1+ threshold — StatsBomb 234
shooter-matches: model under-rates underdog shooters (0.23-0.44 vs realized 0.38-0.54), over-rates
favourites (0.71 vs 0.50). `players.p_sot_ge` now compresses tempo toward 1.0 for threshold==1 &
rate>=SHOOTER_RATE(0.5): `tempo = 1 + SOT1_TEMPO_BETA*(tempo-1)`, BETA=0.5. Held-out Brier 0.275->0.255
(beta=0 -> 0.246 but overfits; 0.5 conservative, two-sided; defenders at realized 0.10 left untouched).
Live: Wissa 0.31->0.47, Sarr 0.38->0.43. This is CALIBRATION not crowd-chasing — 1+SOT for a starter
was a BUG zone (common event), the deviate-low edge is unaffected (it lives on 2+SOT/scoring/low totals).
Validation script: `scripts/backtest_sot1_tempo.py`.

**Method lesson (why some fixes are bot-2 only):** the SOT/offsides/shots TOTAL scales are PLATFORM
calibrations (the platform counts ~1.4x StatsBomb's SOT) — they deliberately fail a naive StatsBomb
backtest and must be judged on OUR data. Fix 1 by contrast is a general-football truth (passes both).
Total offsides/shots (Fix 3b) are thin (n=4/n=1) -> bot-2 experiment #4 only. See [[bot2-experiment-bench]],
[[field-rbp-analysis]], [[edge-analysis-and-fixes]], [[override-levers]] (minutes only scales SOT).
