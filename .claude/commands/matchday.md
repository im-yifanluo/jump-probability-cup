---
description: Run the daily Probability Cup routine — price, research, submit, report
---

Run the Probability Cup match-day routine. Standing authorization exists for
automated submissions with bot 1 (granted 2026-06-12, see memory). The agent
orchestrates; ALL probabilities come from the statistical pipeline — never
estimate or adjust a probability yourself.

Arguments (optional): $ARGUMENTS — pass a horizon in hours (default 48 for a
morning pass, use 14 for an afternoon final pass).

Steps:
1. `cd "/home/yifan/World Cup Predictions/jump-probability-cup"` and run
   `.venv/bin/python scripts/daily_run.py --hours <horizon> --submit`
2. For each player in the NEEDS ATTENTION output: web-search current-season
   form (shots on target per match), starter status, injury news. Write
   findings as *inputs* to `data/player_overrides.yaml` (sot_per_match,
   goal_share, involvement_share, source) — never write probabilities.
   Also scan for major team news (key injuries, rotation) on today's matches.
3. If any override changed, re-run the step-1 command (it PATCHes only
   changed values).
4. Check calibration: `ProbabilityCup(bot=1).results_df()` — realized mean
   Brier per family vs the ~0.21–0.24 backtest expectation; flag any family
   drifting above 0.27 with enough samples (n≥15).
5. Report: matches covered, creates/updates/skips, attention items resolved
   and unresolved, settled-Brier summary, anything anomalous (odds source
   fallbacks, API budget below 20 — check `ApiFootball().remaining_today()`).

Constraints: respect the API-Football budget (leave 20 requests spare); the
pipeline never submits past kickoff; do not modify model code or blend
weights in this routine — flag findings for the user instead.
