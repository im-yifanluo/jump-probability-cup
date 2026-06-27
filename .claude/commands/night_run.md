---
description: Probability Cup NIGHT run — final confirmed-lineup pass for tonight's late games + price tomorrow + output TOMORROW's predictions for manual entry
---

The last run of the night. Does the final confirmed-lineup pass for tonight's late games AND
prices tomorrow's full slate so Yifan can pre-fill the MANUAL submission tonight. **bot 1 =
stable v4; bot 2 = experiment.** Standing authorization (Yifan 2026-06-12). Full pass
checklist: `matchday.md`.

Steps:
1. **FINAL PASS for tonight's imminent late games** — confirmed lineups + scorer odds +
   referees (matchday.md steps 2/2b/2c): ruled-out → expected_minutes 0 (+ kill scorer keys);
   benched-then-restored → revert; market-anchor scorer props.
2. **SUBMIT both bots:** `daily_run.py --hours 26 --submit --agent` — this horizon covers
   tonight's late games AND all of TOMORROW (bot 2 first, bot 1 LAST). VERIFY both bots' open
   counts equal; bot 2 differs only on count markets (expected).
3. **OUTPUT TOMORROW'S PREDICTIONS to chat — every time.** bot 1 (stable v4) values for ALL of
   tomorrow's games, grouped by match, every market with its % as tables/charts, so Yifan can
   pre-fill the MANUAL submission tonight. (The morning run re-outputs the refined version.)
4. **LOG** the report to `data/logs/matchday_reports.md`.
