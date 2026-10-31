---
name: polymarket-derived-backtest
description: "Backtest verdict (2026-06-15) — Polymarket 1h-before odds do NOT improve our predictions; moneyline already blended, derived markets thin/uncovered"
metadata: 
  node_type: memory
  type: project
  originSessionId: edc0be82-f7ce-4493-be8b-e55f21f51524
---

QUESTION (user, 2026-06-15): "Do backtesting of the new polymarket odds... would that really make our prediction better for all previous games, use the odds 1 hr before the beginning of the match." VERDICT: **No.** Keep the current model; do NOT add Polymarket derived-market anchoring.

METHOD (scripts/backtest_polymarket.py): for all 11 settled games, fetched Polymarket's price 1h BEFORE kickoff via CLOB prices-history API (clob.polymarket.com/prices-history?market={token}&startTs=&endTs=&fidelity=15 — no leakage), mapped to our settled markets by family, compared Brier(ours) vs Brier(PM-1h). Game slug map built by matching team names against series_slug=soccer-fifwc events. Outcomes reconstructed from stored brier=(p-o)^2.

RESULTS (13 mappable markets of ~120 settled):
- OVERALL: ours 0.2592 vs PM 0.2669 — ours slightly better.
- team_win (n=9): ours 0.3292 vs PM 0.3307 — a TIE, because get_anchors already blends the PM moneyline 50/50. No new info.
- team_count_ge corners "X+ corners" (n=2): ours 0.0843 vs PM 0.1667 — ours much better; PM prices near coin-flip (|p-.5|=0.11, thinly traded).
- team_more_count_than corners full-match (n=2, derived P(A>B) by Poisson-fitting each team's corner O/U ladder): ours 0.1192 vs PM 0.0798 — PM better and more informative (|p-.5|=0.22), but n=2 only.

COVERAGE IS THE REAL POINT — PM 1h-before could only touch 13 of ~120 settled markets:
- player SOT (our biggest family, 11 settled): PM player-shot markets are DEAD (~$15) and are total-shots not shots-on-target → 0 usable.
- cards (~8 settled), fouls (9), offsides (9): Polymarket offers NO such markets → 0.
- corners: of our 8 settled corner markets, PM could price only 4 (2 direct team O/U + 2 full-match head-to-head). The 4 half-based ones (3×1H + 1×2H "more corners than") are UNPRICEABLE on PM — it lists per-team O/U only for the FULL match, and per-half corners only as the TOTAL (not per-team). So even where corners "exist" they miss our exact question types.

CONCLUSION: the moneyline we already use; the derived markets are partially covered, thinly traded (prices near 0.50), and net a wash-to-worse vs our model. Only faint signal is full-match head-to-head corners (n=2) — too thin and not operationally separable from the half-corner markets PM can't price. Output CSV: data/backtest_polymarket.csv. Related: [[odds-and-simulator]], [[prediction-system]].
