---
name: bot2-experiment-bench
description: "bot 2 is now a live A/B experiment bench vs bot 1's stable v4; how it works + the 06:00 routine"
metadata: 
  node_type: memory
  type: project
  originSessionId: edc0be82-f7ce-4493-be8b-e55f21f51524
---

Set up 2026-06-26 (Yifan request). **bot 1 = stable v4 (sacred, never put unvalidated
changes on it). bot 2 = live A/B bench** for candidate strategies we'd consider for
bot 1 later but haven't validated.

HOW IT WORKS (low-risk, no model-code changes):
- `data/bot2_experiment.yaml` defines the active experiment (name/description/hypothesis
  + override keys `tournament_intensity` and/or `tournament_team_sot`). Empty/absent →
  bot 2 mirrors bot 1.
- `scripts/daily_run.py` (refactored): on EVERY pass it prices each match once, submits
  bot 1 with the live v4 constants, then reprices the SAME markets/odds for bot 2 under
  the experiment's overrides (via a `_experimental()` context manager that swaps
  counts.TOURNAMENT_INTENSITY / players.TOURNAMENT_TEAM_SOT — read at call time). So bot 2
  stays on the experiment continuously through the normal matchday passes too; identical
  odds isolate the strategy as the only difference. Both bots submitted their own dict.
- When the sot multiplier changes, set tournament_team_sot = 4.11×(new_sot/1.00) to hold
  player-SOT invariant (the experiment is about count thresholds, not player props).

VERIFICATION CHANGED: bot1/bot2 open COUNTS still must be equal (both complete), but their
VALUES now differ ON PURPOSE on count-threshold markets — NOT a sync bug. The old
"0 mismatches" goal only holds when no experiment file exists. If bot 2 is short, re-run
daily_run (do NOT copy bot1→bot2 — that wipes the experiment). matchday.md step 3b updated.

DAILY RUNS (REBUILT 2026-06-26 to DYNAMIC self-scheduling; strategy folded into the morning run —
experiment.md/bot1_production.md DELETED):
- 07:48 MORNING anchor `.claude/commands/morning_run.md` — evaluate bot2-vs-bot1 on settled
  markets, research, update bot 2's experiment yaml, FLAG (NOT apply) a proven bot-1 promotion
  (bot 1 changes ONLY on Yifan's OK), SUBMIT both, OUTPUT bot 1's TODAY predictions to chat, THEN
  SCHEDULE the day's final passes via `scripts/schedule_day.py` (deletes old `[AUTO-DAY]` one-shots,
  creates one ~50min before each kickoff wave). Log to experiment_log.md.
- Dynamic `[AUTO-DAY]` one-shots (created by the morning run, one per wave, ~50min pre-kickoff):
  kind=final → matchday.md 5h (confirmed lineups); the LAST wave → night_run.md 26h + OUTPUT
  bot 1's TOMORROW predictions for pre-fill. They adapt to each day's moving kickoff times.
- SAFETY: daily_run never submits past kickoff (late fire only loses freshness); `[AUTO-DAY]`
  dedup prevents duplicates; the script does the tz math (system-local, no LLM arithmetic).

SUBMISSION ORDER (daily_run, all passes): bot 1 priced first, but submitted bot 2 (experiment)
FIRST then bot 1 (stable v4) LAST — so if any contamination occurred, the stable version is the
final write on both.

TWO CONSTRAINTS on bot-2 experiments: (1) CAREFUL real edge only, never random; (2) keep bot 2
COMPETITIVE / low-downside now, so it survives for FUTURE higher-variance top-10 gambles.

PROMOTION RULE UPDATED 2026-07-02 (supersedes "only on explicit OK"): STANDING AUTHORIZATION —
a probe that clearly wins ~8-10 of its OWN-family settles promotes to bot 1 without further
per-change approval, executed by matchday.md step 0 (STRATEGY REVIEW before every wave, scored
via `backtest_engine.py ab`). Clear losers retire. See [[promotion-event-07-02]].

EXPERIMENT HISTORY:
- #1 (06-26..06-29) 'aggressive-count-multipliers' (cards 0.72/sot 1.05/corners 1.15):
  REJECTED — trailed v4 by +0.012→+0.015 Brier over ~1 SE on 33+ markets. Confirms v4's
  conservative card/corner calibration.
- #2 (06-29) SOT recalibration: PROMOTED to bot 1, then CORRECTED. The under-prediction is
  TOTAL-SPECIFIC (total_count_ge/sot pred 0.54 vs hit 0.83, n=18); a flat sot=1.40 over-corrected
  TEAM SOT (leave-out Brier 0.203->0.230 on 31 settled team-SOT markets — Yifan caught this). FINAL
  form is DECOUPLED: TOURNAMENT_INTENSITY['sot']=1.00 (team/player SOT = calibrated v4) + a NEW
  **counts.TOTAL_SOT_SCALE=1.40** applied ONLY to the convolved total (p_total_count_ge for sot) and
  the total-derived squad "2+ SOT". Confirmed live (BRA/JPN "8+ total SOT" HIT: v4 Brier 0.476 vs
  boost 0.122). KEY LESSON: team-vs-team COMPARISONS and per-team markets are ~scale-invariant/
  calibrated; TOTALS need their own level — never fix a total by lifting the per-team mean.
- #3 (06-29→07-01) 'total-sot-1.6-probe' — total_sot_scale 1.60 vs bot1's 1.40. Folded into #4.
- #4 (07-01→) 'total-level-corrections' — probes ALL THREE shot-volume TOTAL leaks the R32 our-data
  RBP backtest found: total SOT 1.60 (vs bot1 1.40), total offsides 1.20, total shots 1.20 (vs bot1
  1.00). New counts.TOTAL_OFFSIDES_SCALE / TOTAL_SHOTS_SCALE (default 1.0 = bot1 unchanged), total-only
  (per-team/comparison offsides NOT touched — same discipline as SOT). daily_run `_experimental` + the
  active-experiment gate now honour `total_offsides_scale` / `total_shots_scale` keys (the gate was
  also fixed — it only recognised intensity/team_sot before). KEY DESIGN: the three scales hit DISJOINT
  market sets, so one bot-2 run tests all three with NO confounding — each promotes/dies on its own
  settles (~8-10 each). Evidence is THIN (offsides n=4, shots n=1) and unverifiable vs StatsBomb
  (platform counting), which is exactly why it's bot-2 only. See [[r32-rbp-analysis]].
- #5 (07-01 PM) 'dominant-favorite-volume' — added counts.FAVORITE_SOT_BOOST (supremacy-gated favourite
  team-SOT lift; even games untouched) 0.06 + cards 0.85; offsides/shots cut 1.20->1.10 after USA/BIH
  showed blanket up-scales over-predict MODERATE games (22+shots 91% vs NO). ESP/AUT: bot2 won 4/4
  divergents incl. the direct favourite-SOT target (17->25 on YES) + the player cascade (Yamal).
- #6 (07-02→) 'next-notch-probes' — AFTER the promotion event (bot1 absorbed fav-boost 0.06 / cards
  0.85 / total-SOT 1.50 / offsides 1.10): bot2 probes one notch further on each — fav-boost 0.10,
  cards 0.80, total-SOT 1.60, offsides 1.20, shots 1.10 (probation; NOT promoted — dominance-
  concentrated, not a level issue). Each notch promotes/retires on its own settles vs bot1's levels.

SCHEDULING CAVEAT: the in-app cron (CronCreate) is SESSION-ONLY here — durable:true does NOT
persist (reports "dies when Claude exits"), and recurring jobs auto-expire after 7 days. So
the 6:04 AM auto-run only fires if Claude Code is running/idle then. bot 2 still stays on the
current experiment via the matchday passes regardless; a missed 06:00 run just skips that
day's strategy EVOLUTION (fine for gathering A/B data). For guaranteed firing: keep Claude
open, or trigger the experiment routine manually (like the matchday passes), or an OS-level
scheduler. See [[strategy-v4-doc]], [[daily-automation]].
