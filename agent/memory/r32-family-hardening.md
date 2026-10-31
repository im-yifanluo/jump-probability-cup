---
name: r32-family-hardening
description: R32 derived families hardened 2026-06-28 (advancement models shootouts; substitute-scores re-derived on 5-sub only) + family_calibration.py is the per-family drift monitor
metadata: 
  node_type: memory
  type: project
  originSessionId: edc0be82-f7ce-4493-be8b-e55f21f51524
---

The R32 families shipped 06-27/28 passed the ship gate (calibrated in LOTO backtest, beat base rate, bounded ≤20-pt downside) but were NOT proven optimal. The LOTO backtest used REALIZED tournament totals as the tempo proxy, so it validates the conditional structure but is optimistic vs live (where tempo is odds-derived). On 2026-06-28 (user-approved) three were hardened:

1. **Advancement** (`goals.py` `p_advance`): the old `p_win + p_draw × p_win/(p_win+p_loss)` resolved the regulation draw proportional to regulation odds, which over-favours the stronger team — penalty shootouts are ~coin-flips. New model: explicit 30' extra time (independent Poisson at `ET_FRACTION=1/3` the 90' rate) then penalties with the strength share shrunk toward 0.5 by `PENS_TILT=0.25`. CONSERVES exactly (both sides sum to 1). Lifts underdogs in lopsided ties (heavy underdog advance +~4.6pts; BRA/JPN Japan 0.253→0.286). PENS_TILT is a tunable — let live calibration inform it.

2. **Substitute-scores** (`counts._SUB_GOAL`): re-derived on the 250 matches under the **5-sub rule only** — dropped 3-sub WC-2018 (its sub-scoring rate 0.172 was the lowest of all six tournaments vs the 5-sub mean 0.296 and biased the modal G=2/3 cells low). New table `(0.0,0.070,0.264,0.333,0.565,0.565,0.900)`, tail isotonic-smoothed, marginal 0.296. Reproduce: **`scripts/derive_sub_scores.py`** (there was no derivation script before — provenance gap closed).

3. **Per-family calibration monitor** — **`scripts/family_calibration.py`** (wired into `matchday.md` step 4): predicted-vs-realized GAP + Brier per market family over all settled markets (reads `results_df()`, which carries the question text directly, so settled markets resolve even after aging out of the snapshot). New R32 families starred. Use the gap AND Brier together — a wide gap on a low-base-rate family with low Brier (e.g. player_scores) is fine. 06-28 watch flags (existing families, not the new ones): ht_tied over-predicts, total_count_ge under-predicts (Brier 0.271), penalty_or_red over-predicts.

Also fixed 06-28: `data/logs/submissions.csv` had a MIXED schema (early rows 5-col, no `bot` field; later 6-col) that silently misaligns a naive `DictReader` (it cost a false-negative "never submitted" check). Migrated to a consistent 6-col schema. See [[edge-analysis-and-fixes]], [[strategy-v4-doc]], [[parser-ko-normalization]], [[player-prop-prior-fallback]].
