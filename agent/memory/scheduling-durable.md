---
name: scheduling-durable
description: How Probability Cup runs are scheduled — session crons do the runs; a durable alert-only OS watchdog screams on a missed run (NO mechanical auto-submit)
metadata: 
  node_type: memory
  type: project
  originSessionId: edc0be82-f7ce-4493-be8b-e55f21f51524
---

**Design philosophy (Yifan, 2026-07-11): QUALITY over coverage. Do NOT auto-submit low-quality mechanical runs — a bad prediction loses RBP (crowd_brier − our_brier can go negative), a skipped market is just 0. So: run the researched agent passes, and if one doesn't happen, FAIL LOUDLY so Yifan triggers it — never paper over it.**

**Root cause of the 07-11 miss:** all scheduling was session-only CronCreate jobs (die when the Claude session ends). Worked 07-07→07-10 only because the session stayed alive 4 days; broke on the overnight restart, wiping even the recurring morning cron → 07-11 morning run + NOR/ENG final pass never fired.

**Two layers now:**
1. **Session crons = the actual runs** (research quality: confirmed XI, referee, scorer anchors, promotions). Recurring 7:48AM morning run + [AUTO-DAY] one-shot final passes it schedules ~50min pre-kickoff. Recreate them whenever a session resumes (they don't survive restarts).
2. **Durable OS crontab = ALERT-ONLY WATCHDOG** (`scripts/run_watchdog.py`, every 20min via `crontab -l`). It NEVER submits. Checks the agent heartbeat (data/logs/agent_last_run) and screams if: (a) morning run hasn't run by 9am local, or (b) a game locks within 2h with no agent run in the last ~95min. On a miss → sticky `data/logs/RUN_MISSED.txt` + `data/logs/watchdog.log` + best-effort desktop `notify-send`/`wall`. Self-clears when a run happens.

**REJECTED earlier same day:** a submitting OS-cron fallback (`cron_fallback.sh` + `daily_run --defer-to-agent`) — removed because auto-submitting model-quality prices dilutes RBP. The `--defer-to-agent` machinery in daily_run.py still exists but is unused. (Fixed a real bug while there: `daily_run._log_run` KeyError'd on the empty DataFrame of a no-op run — guarded `match_name`.)

**2026-07-13 fix:** daily_run now writes the heartbeat on a no-op `--agent` run too (empty-markets branch). Before, a games-free-day morning run returned before the heartbeat write → the watchdog false-alarmed "MORNING run has not run today." A run that finds no games still RAN. (This is why the heartbeat looked 29h stale on 07-13 despite the 07-12 morning run happening.)

**Caveats:** (1) No scheduler fires while the machine is asleep/off. (2) The watchdog's notify-send only reaches Yifan AT the machine — an off-machine channel (email/phone push) is a pending choice. (3) A truly missed game stays SUBMITTED from the prior pass (not forfeited), just unrefreshed. Related: [[daily-automation]].

**LESSON:** never assure Yifan "it will fire" on session-only crons; the watchdog is the honesty mechanism. Better a loud gap than a quiet low-quality submit.
