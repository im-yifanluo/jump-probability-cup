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
   `.venv/bin/python scripts/daily_run.py --hours <horizon> --submit --agent`
   (the `--agent` flag drops a heartbeat so the deterministic crontab fallback
   defers to this run and never overwrites your research-informed values).
1b. CONFIRMED LINEUPS ARE AUTO-PULLED (scripts/pull_lineups.py): within 100 min
   of kickoff, daily_run fetches the posted XI from ESPN and writes
   expected_minutes to data/match_lineups.yaml (starter->90, bench->20), which the
   pricer reads — so you NO LONGER hand-set expected_minutes for rotation/benching.
   It prints `lineups: ESPN confirmed XI — N start / M bench`. YOUR JOB: (a) if it
   prints `UNMATCHED [names]`, those couldn't be matched to the XI (spelling) —
   check each against the posted lineup and ONLY if truly out set expected_minutes
   0 by hand; (b) `not_posted` just means lineups aren't out yet (defaults hold —
   fine); (c) the pull sets MINUTES only, so a confirmed benching still needs its
   goal_share/involvement_share and scorer_prop_prob reviewed for the scorer family.
2. For each player in the NEEDS ATTENTION output: first run
   `python3 scripts/update_player_rates.py "<match>"` to pull current club
   SOT/goal rates from API-Football (this fixes both "prior" and
   "sparse/old data" flags — the latter are players whose StatsBomb history
   predates a role change). For anyone the API can't match, web-search
   current-season shots-on-target per match. Write findings as *inputs* to
   `data/player_overrides.yaml`: `sot_per_match` (club-season rate — the model
   applies the 0.839 international discount automatically), `goal_share`,
   `involvement_share`, `expected_minutes` (NOW auto-set by the lineup pull — only
   hand-set it for an injury/absence known BEFORE lineups post, e.g. ruled-out=0;
   the SOT rate scales by minutes/90), and `team` (the
   player's national team as it appears in the match name — REQUIRED for any
   player whose squad is absent from the StatsBomb data, e.g. Iraq/Jordan/New
   Zealand/Cape Verde; without it the model prices the player at the
   match-average team strength, over-rating underdogs and under-rating
   favourites). Never write probabilities. Also scan for major team news (key injuries, rotation).
2b. For every "Will <player> score a goal" market on matches kicking off
   today: web-search the player's anytime-goalscorer odds (FanDuel/DraftKings/
   bet365). Convert: implied = 100/(100+american), then divide by ~1.10 prop
   margin. Write the result as `scorer_prop_prob` in that player's
   player_overrides.yaml entry — the pricer uses a prop-market price outright
   when one exists (markets beat models).
2c. REFEREE (highest-leverage card input — a strict vs lenient ref swings
   P(4+ cards) ~0.24->0.66): for each match kicking off today, web-search the
   appointed referee and their career average cards per game. Write to
   data/referee_overrides.yaml as {match: {referee, cards_per_game, source}}.
   The card model scales by cards_per_game / 3.46 (tournament avg). Leave
   unset if the referee isn't announced yet (defaults to neutral).
3. If any override changed, re-run the step-1 command (it PATCHes only
   changed values).
3b. VERIFY BOTH BOTS ACTUALLY PERSISTED (critical — a silent bug once left bot 2
   empty for the first ~15 matches while every pass reported "bot 2: unchanged").
   Do NOT trust the submit summary. Query each bot directly and compare the COUNT
   of `market_status == "open"` predictions:
   `ProbabilityCup(bot=1).api.predictions(lobby_id=...)` vs `bot=2`. The COUNTS must
   be equal (both internally complete).
   NOTE ON VALUES — A/B IS ACTIVE: when `data/bot2_experiment.yaml` is present, bot 2
   runs an EXPERIMENT and its VALUES differ from bot 1 on the count-threshold markets
   ON PURPOSE — that is NOT a sync bug, do not "fix" it. (Only when no experiment file
   exists should bot 1 and bot 2 be value-identical / 0 mismatches.)
   If bot 2 is SHORT on count: do NOT copy bot 1 → bot 2 (that would wipe the
   experiment). Instead re-run the same daily_run command — it reprices bot 2 with
   its experiment and re-submits — then re-verify the counts match.
4. Check calibration: `ProbabilityCup(bot=1).results_df()` — realized mean
   Brier per family vs the ~0.21–0.24 backtest expectation; flag any family
   drifting above 0.27 with enough samples (n≥15).
5. Report: matches covered, creates/updates/skips, attention items resolved
   and unresolved, settled-Brier summary, anything anomalous (odds source
   fallbacks, API budget below 20 — check `ApiFootball().remaining_today()`).
6. Persist the report: append the full report from step 5 (markdown, with a
   `## <UTC timestamp> — /matchday <horizon>` heading) to
   `data/logs/matchday_reports.md`. This is mandatory — a routine run that
   isn't logged there didn't happen. (The pipeline independently logs every
   run to data/logs/runs.log and every submission to data/logs/submissions.csv.)

Constraints: respect the API-Football budget (leave 20 requests spare); the
pipeline never submits past kickoff; do not modify model code or blend
weights in this routine — flag findings for the user instead.
