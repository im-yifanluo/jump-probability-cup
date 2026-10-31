# Agent memory (snapshot)

The notes the agent kept for itself during the tournament, published verbatim. Claude Code
loads [`MEMORY.md`](MEMORY.md) (the index) at the start of every session, and the agent reads
individual notes when they become relevant — this is how decisions, root causes and operating
rules survived context compaction and session restarts.

They are working notes, not documentation: dense, dated, full of shorthand, and sometimes
superseded by a later note (the index says which). For the cleaned-up account, read
[docs/strategy.md](../../docs/strategy.md) and [agent/README.md](../README.md).

| Note | About |
|---|---|
| [probability-cup-overview](probability-cup-overview.md) | the competition, the API surface, live-system quirks |
| [prediction-methodology](prediction-methodology.md) | the pricing hierarchy and the submission-consent rule |
| [prediction-system](prediction-system.md) | the architecture, backtest results and early calibration fixes |
| [daily-automation](daily-automation.md) | the run cadence, logging, and the silent bot-2 failure |
| [scheduling-durable](scheduling-durable.md) | session crons + the alert-only watchdog, and why |
| [bot2-experiment-bench](bot2-experiment-bench.md) | the A/B bench and the history of every experiment |
| [promotion-event-07-02](promotion-event-07-02.md) | standing promotion authorization and the replay engine |
| [edge-analysis-and-fixes](edge-analysis-and-fixes.md), [field-rbp-analysis](field-rbp-analysis.md), [r32-rbp-analysis](r32-rbp-analysis.md) | where the points came from, and the leaks |
| [lineup-puller](lineup-puller.md), [player-prop-prior-fallback](player-prop-prior-fallback.md), [override-levers](override-levers.md) | player-input failure modes and fixes |
| [parser-ko-normalization](parser-ko-normalization.md), [r32-family-hardening](r32-family-hardening.md), [goal-method-models](goal-method-models.md), [coverage-families-0707](coverage-families-0707.md) | knockout-stage coverage |
| [odds-and-simulator](odds-and-simulator.md), [polymarket-derived-backtest](polymarket-derived-backtest.md) | the odds blend and two rejected ideas |
| [strategy-v4-doc](strategy-v4-doc.md), [kor-cze-submission](kor-cze-submission.md) | pointers to the strategy write-up; the first live match |
