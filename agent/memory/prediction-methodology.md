---
name: prediction-methodology
description: "User-approved methodology for pricing Probability Cup markets, and the submission-consent rule"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: edc0be82-f7ce-4493-be8b-e55f21f51524
---

Methodology hierarchy the user approved for pricing markets (KOR vs CZE, 2026-06-12): (1) markets that bookmakers/prediction markets price directly (1X2, anytime scorer) — use de-margined market odds, don't model; (2) goal-derived markets — independent-Poisson goal model with rates fitted to the market 1X2 + totals line (binary search on the split), then exact scoreline enumeration; 2nd-half goal share 0.54; (3) count markets with no odds (cards, corners, offsides, SOT, penalties) — Poisson tail probabilities with rates from World Cup base rates, ADJUSTED with team-specific data (e.g. card trends) and player-specific data (e.g. Schick 1.21 SOT/match at Leverkusen 2025-26). "Strictly more than" head-to-head markets: enumerate both Poissons; ties count as No, pulling probabilities to ~40-44%, a likely crowd-error edge.

**Why:** the user explicitly directed: keep market-implied values where odds exist, do accurate team/player-specific modeling where they don't. They also repeatedly asked for full transparency on how every value is computed — walk through formulas and arithmetic per market before asking to submit.

**How to apply:** UPDATE 2026-06-12: Yifan granted STANDING AUTHORIZATION for automated daily submissions with bot 1 (chose "fully automate" for the 8AM+4PM daily routines; 60 predictions auto-submitted same day). Automated pipeline submissions via scripts/daily_run.py --submit are authorized; still confirm before anything outside that flow (e.g., bot 2, manual one-offs, model changes). Useful per-match base rates: penalties 0.36/match, ~4 cards/match (3.4 for low-card teams, 62% in 2nd half), ~2 offsides/team, ~10 corners/match (45% pre-HT), ~4.3 SOT/team (55% 2nd half). See [[probability-cup-overview]] and [[kor-cze-submission]].
