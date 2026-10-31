---
description: Probability Cup MORNING run — evaluate last night, set bot2 experiment, submit both bots, output TODAY's predictions for manual entry
---

The single MORNING broad run (consolidates the old strategy + production + matchday-morning
passes into one — no overlap). **bot 1 = stable v4 (the LEADER, sacred). bot 2 = live A/B
experiment bench.** Standing authorization for bot-1 + bot-2 submissions (Yifan 2026-06-12).
Strategy: `docs/strategy.md`. Full pass checklist: `matchday.md`.

Steps:
1. **EVALUATE last night.** Refresh `results_df` for bot 1 AND bot 2; re-parse `docs/archive/crowd_history.md`
   if updated (`probcup ledger`). Per-family RBP + calibration trend. **Core
   question: on markets settled SINCE the current experiment started, is bot 2 beating bot 1
   (lower Brier)?** Isolate the count markets; small samples → tentative.
2. **RESEARCH today's matches** (predicted lineups, scorer/anytime-goal odds, referees for any
   card-threshold markets — see matchday.md steps 2/2b/2c).
3. **SET bot 2's experiment** — write `data/inputs/bot2_experiment.yaml` (keep / evolve / next
   candidate). Two hard constraints: CAREFUL real edge only (never random); keep bot 2
   COMPETITIVE / low-downside now (reserve high-variance gambles for a future top-10 push).
   `tournament_team_sot` = 4.11 × new_sot to hold player-SOT invariant.
4. **PROMOTION (STANDING AUTHORIZATION, Yifan 2026-07-02 — supersedes the old explicit-OK rule):**
   a bot-2 probe that clearly wins ~8-10 of its OWN-family settles (scored via
   `probcup replay ab`, consistent direction) PROMOTES to bot 1's live constants without
   further per-change approval — apply, run `pytest`, move bot 2 to the next notch, log. Clear
   losers retire (bot 2 back to bot 1's level). Small samples keep running. High-confidence
   BUG fixes also go straight to bot 1. Everything else stays a bot-2 probe.
5. **SUBMIT both bots:** `.venv/bin/probcup run --hours 48 --submit --agent` (bot 2 first, bot 1 LAST —
   contamination safety). VERIFY both bots' open COUNTS equal; bot 2 differs only on count
   markets (expected — do not "fix").
6. **OUTPUT TODAY'S PREDICTIONS to chat — every time.** bot 1 (stable v4) values for ALL of
   today's games, grouped by match, every market with its % as tables/charts, so Yifan can fill
   the MANUAL submission.
7. **SCHEDULE today's dynamic final passes.** Run `.venv/bin/probcup schedule`
   (50-min lead). FIRST `CronList` + `CronDelete` every existing `[AUTO-DAY]` one-shot (clears
   stale/duplicate jobs). THEN for each `SCHEDULE` line create a ONE-SHOT cron (`recurring:false`)
   at its exact `cron=` expression, with a prompt PREFIXED `[AUTO-DAY]`:
   - `kind=final` → read `matchday.md` + `.venv/bin/probcup run --hours 5 --submit --agent` (confirmed
     lineups/odds/refs for the imminent wave, both bots). The prompt MUST say to execute
     matchday.md INCLUDING step 0 (the pre-match STRATEGY REVIEW — probe scoring + standing
     promotions, Yifan 2026-07-02).
   - `kind=night` → read `night_run.md` (strategy review + final pass + `probcup run --hours 26`
     + OUTPUT tomorrow's bot-1 predictions to chat).
   They fire ~50 min before each kickoff so lineups are out and there is time to reprice
   unhurried; `probcup run` never submits past kickoff, so a late fire only loses freshness, it can
   never post a stale price. Do NOT schedule tomorrow's passes here — tomorrow's morning run does.
8. **LOG:** `data/logs/experiment_log.md` (settled verdict, decision, active experiment, promotion
   flag) + append the matchday report to `data/logs/matchday_reports.md`.
