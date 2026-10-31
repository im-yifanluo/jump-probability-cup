---
name: coverage-families-0707
description: 5 new KO market families built+deployed to bot1 on 2026-07-07; totalSOT-1.60 promotion confound lesson; team_first_goal_game drift deferred
metadata: 
  node_type: memory
  type: project
  originSessionId: edc0be82-f7ce-4493-be8b-e55f21f51524
---

**2026-07-07 morning run** (ARG/EGY + SUI/COL R16, 2× weight).

**5 new closed-form families built, tested, deployed to BOTH bots** (cleared 5 forfeited KO markets; all recur into 3× final):
- `total_goals_odd` — parity sum over total-goals pmf (goals.p_total_goals_odd)
- `decided_by_one_goal` — margin==1 over the grid (goals.p_margin_exact)
- `first_goal_in_2h` — exp(-λ₁ₕ)·(1-exp(-λ₂ₕ)) (goals.p_first_goal_in_2h)
- `either_wins_both_halves` — Σ team wins both halves, independent-half Poisson race (goals.p_either_wins_both_halves / _p_win_half)
- `card_each_half` — card sibling of goal_each_half, convolved two-team NB tail ≥1 per half × TOTAL_CARDS_SCALE (counts.p_card_each_half)
Parser rules + pricer cases + test_sf_ko_family_templates added. Conservation verified (odd+even=1, margins sum=1). Still forfeited: "goal after hydration break" (timing sub-window) + "first goal NOT by Messi/Salah" (needs first-scorer player model) — deferred, noisier/single-occurrence.

**PROMOTION CONFOUND LESSON (critical):** the `ab` tally showed totalSOT-1.60 winning 10/13 — looked promotable. But ALL 13 settles were pre-07-02, comparing 1.60 vs bot1's OLD 1.40 level (bot1 went 1.40→1.50 on 07-02). Clean 1.60-vs-1.50 sample = **n=0**. DO NOT promote on era-confounded tallies. Before any promotion, filter settles to AFTER the last bot1 level change for that constant. Same caveat applies to cards/offsides/favboost/timing probes (all moved 07-02). See [[promotion-event-07-02]], [[bot2-experiment-bench]].

**RESOLVED 2026-07-08 — `team_first_goal_game` drift was NOISE.** The −0.193 live drift (n=10, model 0.607 vs actual 0.800) looked structural (favourites score first more than lam-share) but `scripts/validate_first_goal_drift.py` tested it on the full StatsBomb set (n=219): conditional on final goal counts, P(higher-count team scored first) = observed 0.886 vs exchangeable-model 0.878, **gap +0.007, z=0.35**. Goals ARE time-exchangeable — the independent-Poisson ordering (lam_s/lam_tot×P(≥1)) is CORRECT. The live n=10 was a small-sample fluctuation (~1.25 SE). NO model change. Lesson (again): a calibration flag at n=8-14 with a ~1-1.25 SE gap is a candidate for large-sample validation, NOT a bot1 tweak — the [[promotion-event-07-02]] backtest-engine principle (validate on the big sample) applies to drift flags too. Same reasoning defers the new **btts** flag (n=14, ~1.1 SE) as likely noise — monitor, don't act.

**btts drift RESOLVED as NOISE 2026-07-12** (grew to n=16, pred 0.497 vs realized 0.688, z~1.65). scripts/backtest_btts_calibration.py fit the REAL from_market model to 6000 club matches: pred 0.514 vs realized 0.519 (z=0.82), per-decile near-perfect across the range. Odds-anchored goal model's BTTS is correct; WC was knockout variance. No change. TWO drift flags now cleared by large-sample tests (team_first_goal via StatsBomb n=219, btts via club n=6000) vs ONE that survived and promoted (timing, via walk-forward). The rule holds: a sub-2-SE live drift (n<~20) is a candidate for large-sample validation, NEVER a bot1 change on its own.

**timing_scale PROMOTED 2026-07-10: bot1 1.0 → 0.85** (Yifan OK, after rigorous backtest). timing_event over-predicted (realized 0.214 vs shipped 0.432, n=28). Validated 3 ways: live A/B probe 5/6, family calibration +0.146, and a walk-forward (scripts/backtest_timing_scale.py, n=22 OOS: 0.85 beat 1.0 by −1.83 Brier, 0.70 by −3.22, in-sample optimum ~0.45). 0.85 is a conservative partial correction; **bot2 now probes timing_scale 0.70** (the deeper OOS optimum) to confirm before it touches bot1. Method for future drift→promotion: recover each settled market's window-mean from the shipped prob, sweep the scale, and WALK-FORWARD — a live-probe alone (n=6) is not enough for a bot1 change. See [[bot2-experiment-bench]], [[promotion-event-07-02]].

**DEMBÉLÉ −94 LESSON (2026-07-18 bronze, worst market of the tournament):** his S/A was model-stale at 0.19 STARTER (5 tournament goals — Bellingham-class form lag) → bench-crash scaled it to 0.05 → he subbed on and scored/assisted vs crowd 38. TWO rules from it: (1) **the S/A floor (S/A ≥ de-margined anytime + assist uplift) must sweep EVERY score-or-assist market on a board**, not just researched stars — a 5-goal player at 19% starter S/A should scream; (2) **benched ELITE attackers get the Kane-style cameo decomposition (P(on)×P(score|mins@late intensity) ≈ 0.10-0.18), never the raw model bench-crash (0.05)**. Counterweights same game: Kane bench-cut 0.15 won +31.6, 20+shots 92 won +23.4 — the handling was right where applied; the failure was COVERAGE of the check. Net −70.7, first negative game.

**Salah anchor:** floored-looking 0.135 was actually corroborated (Dimers sim 13.3% ≡ our model); market 4/1 (~0.17 de-margined) is public-name-biased. Set scorer_prop_prob 0.15 (mild lift). Pattern: a "floored" superstar isn't always a [[player-prop-prior-fallback]] bug — check if an independent sim agrees before chasing the public-shortened market up.
