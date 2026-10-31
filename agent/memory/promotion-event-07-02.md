---
name: promotion-event-07-02
description: "2026-07-02 strategy overhaul: standing promotion authorization, bot1 absorbed 4 validated constants, backtest engine verdict (no global dial), R16 families deployed, strategy review before every wave"
metadata: 
  node_type: memory
  type: project
  originSessionId: edc0be82-f7ce-4493-be8b-e55f21f51524
---

Yifan directive 2026-07-02 ("stuck 50-100th, ~668 RBP to top-10, not enough edge"):

**STANDING AUTHORIZATION (supersedes the old only-on-explicit-OK rule):** high-confidence
fixes go to bot 1 DIRECTLY; uncertain ones to bot 2; a bot-2 probe that wins ~8-10 of its
OWN-family settles PROMOTES to bot 1 without further per-change approval. Applied via
matchday.md **step 0 = STRATEGY REVIEW before every match wave** (score probes with
`backtest_engine.py ab`, promote winners / retire losers, note tallies). night_run.md step 0
+ morning_run.md cron template carry it too.

**bot 1 now carries (promoted 07-02, submitted before POR/CRO lock):** favorite_sot_boost
0.06 (dominant-favourite SOT was THE recent leak — Bellingham -64/ENG-7+SOT -61/Spain-8+SOT
-58 were the 3 biggest losses in 4 games; A/B won 4/4 ESP/AUT divergents), total_cards_scale
0.85 (5-way corroborated), total_sot_scale 1.50 (1.60-probe won 4/5 vs 1.40; implied 1.49),
total_offsides_scale 1.10 (bias -0.32, n=7). Blanket TOTAL_SHOTS_SCALE stays 1.0 on bot 1
(USA/BIH 91%-vs-NO blowup — dominance-concentrated, not a level issue). bot 2 =
'next-notch-probes' (0.10 / 0.80 / 1.60 / 1.20 / shots 1.10 probation).

**Backtest engine** (`scripts/backtest_engine.py`): exact RBP replay of settled markets with
GAME-clustered bootstrap CIs, chronological walk-forward, and the bot2-vs-bot1 `ab` replay
(REAL model outputs — the promotion validator). VERDICT that shapes strategy: **no global
transform wins** — deviation amplification, crowd blends, fitted-alpha walk-forward (-78 OOS),
drift-family shrinks: all negative or inconclusive. Deviation sizing is already right; edge
comes from (a) model-leak fixes, (b) coverage of forfeited families, (c) sharper inputs.

**R16 families** (13 forfeited markets, first lock CAN/MAR 07-04): advance-to-QF (round-
agnostic), shootout_decided (= p_draw × ET-tie, consistent with p_advance), total_goals_exact,
goal_each_half, cards_gt_goals (pmf joint), hold_lead (StatsBomb: led|won=1.000, led|drew&
first=1.000, led|drew&nofirst=0.073, led|lost=0.132 — `derive_r16_families.py`),
plays_full_match (5-sub era: FWD 0.282/MID 0.575/DEF 0.790; position via override key;
non-starter → 0.02). Also FIXED the "a substitute" misparse (cost -47 on BEL/SEN).

**Data lever still open:** bookmaker lines for niche families (SportsGameOdds = all WC fixtures
incl. props/corners/cards; Goalserve = Bet365/Pinnacle) — awaiting Yifan's paid-key go.
See [[bot2-experiment-bench]], [[r32-rbp-analysis]], [[edge-analysis-and-fixes]].
