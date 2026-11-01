# Timeline: directives and what changed

How the system grew over the tournament, one operator directive at a time. Directives are
paraphrased from the session history; outcomes link to where they live now.

| Date | Directive (paraphrased) | What changed |
|---|---|---|
| Jun 11 | Read the API documentation; build a clean, readable way to use every endpoint. | Two layers: a thin REST client ([client.py](../src/probcup/sportspredict/client.py)) and a domain layer that hides ids, upserts and unit conversion ([cup.py](../src/probcup/sportspredict/cup.py)); an [API tour notebook](../notebooks/01_api_tour.ipynb). |
| Jun 11 | Price the first match. Keep bookmaker and prediction-market odds where they exist, model the rest with team and player data, and explain every number before submitting. | The pricing hierarchy that held all tournament: market anchor → odds-fitted model → measured base rates. Ten hand-checked predictions, 16 minutes before kickoff. |
| Jun 11–12 | Research the state of the art and build the real system, with backtesting. | Dixon-Coles scoreline grid fitted to the odds; negative-binomial count models on ~41k club matches with closing odds; a walk-forward backtest of 782k predictions ([notebook](../notebooks/02_backtest_report.ipynb)). |
| Jun 12 | An agent should wake up every morning, pull the questions, price and submit everything in the next 48 hours. The agent orchestrates; the statistics compute. | The production run (`probcup run`) and the first playbook; standing authorization for automated bot-1 submissions; every run and submission logged. |
| Jun 12 | Review an external audit; fix whatever is real. | Scorer odds wired into the pricer, StatsBomb appearance counts corrected, Dixon-Coles ρ clamped, team-name aliases, exact count convolutions. |
| Jun 13 | Don't assume both bots were updated — verify, and submit each bot its values explicitly. | Per-bot submission; verification against the platform. (The deeper silent failure surfaced on Jun 16; see [README](README.md#what-went-wrong-and-what-changed-because-of-it).) |
| Jun 13–14 | Early games went badly. Diagnose rigorously, but make sure we aren't overfitting to a few unlucky matches; we want real edge. | Fixes validated on large samples only (player shots on target, offside elasticity, international discounts, referee strictness). A shrinkage fitted to the recent run and an "extremize" tweak were reverted. |
| Jun 14–15 | Find every market feed (Polymarket, Kalshi, …) and backtest whether pre-match prediction-market prices would improve us. | Polymarket's 1X2 blended with the bookmaker; derived prediction markets rejected after a backtest ([study](../research/studies/polymarket_derived_markets.py)). |
| Jun 15 | Points come from beating the crowd. Compare our numbers with the field's on every settled market. | The crowd ledger and the edge map: win on niche counts (offsides, corners, cards, shots on target), anchor to the market on goals. |
| Jun 19 | The operator was away for three days; keep submitting careful predictions in case scheduled runs don't fire. | An unattended stretch run off the playbooks. |
| Jun 24 | We slipped from about #50 to #94. Go through every prediction and find out why. | The uniform low-event multiplier was the cause; per-stat calibration (v4) replaced it, validated out of sample. |
| Jun 25 | Is "extremizing" actually correct under a Brier score? Research it and backtest it. | Rejected: Brier is a proper scoring rule, so the honest probability maximizes expected points. Written up as the v4 strategy. |
| Jun 26 | Use bot 2 to test strategies while bot 1 stays on the last stable version. Submit bot 2 first and bot 1 last. | The live A/B bench, a daily strategy review, and contamination-safe ordering ([experiment.py](../src/probcup/pipeline/experiment.py)). |
| Jun 26 | Run each final pass shortly before its kickoff, not at fixed times. | Self-scheduling: the morning run lays one-shot crons 50 minutes before each wave. |
| Jun 26 | A stale benching just cost us. Pull lineups in real time, and audit the whole process for failures of the same kind. | The ESPN lineup puller, per-match scoping of every researched input, and a staleness guard. |
| Jun 28 | Never skip a new question type: research it, build it, backtest it. | The coverage rule and the backlog scanner; 19 new families during the knockouts, closed-form or StatsBomb-derived, each tested. |
| Jul 1 | Analyse the round of 32 by question type. What are we good and bad at, and why? | "Deviate low on soft markets" confirmed as the edge (+479 points); total-volume corrections queued as bot-2 probes. |
| Jul 2 | Stuck between 50th and 100th. Build a rigorous backtesting engine; validated probes may be promoted without asking each time. | The replay engine (game-clustered bootstrap, walk-forward, bot-2-vs-bot-1 replay) and the standing promotion rule. Four constants promoted that day. |
| Jul 10 | Promote the timing correction only if it survives rigorous backtesting. | Timing scale 1.0 → 0.85, the one knockout-era promotion, validated three independent ways. |
| Jul 11 | Runs were missed. The goal isn't to always submit something: fail loudly instead of submitting low-quality numbers. | The alert-only watchdog on a durable OS cron; no automatic fallback submission. |
| Jul 19 | Final day. Bot 1 must stay in the top 1% and bot 2 should keep its knockout standing; no boldness on ranked entries. | Both bots played the final honest and identical. Bot 1 scored +501.6 points on the final and finished #17 of 4,013; bot 2 finished #5 on the knockout leaderboard. |
| Jul 19–20 | Write down the exact strategy, with the evidence behind every question type. | [docs/strategy.md](../docs/strategy.md) and [docs/methodology.md](../docs/methodology.md). |
