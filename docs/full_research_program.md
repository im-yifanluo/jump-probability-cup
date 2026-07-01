# Full Research Program

Last updated: 2026-06-29.

This document defines the full manual-entry research program for the Jump
Probability Cup. It is intentionally separate from bot submission logic.
Manual recommendations must not read stored bot predictions, must not submit,
and may use the SportsPredict API only for read-only GET endpoints documented
in `docs/manual_strategy.md`. The primary source and promotion-gate references
are maintained in `docs/methodology_sources.md`.

## Objective

Produce the best calibrated manual-entry probability for every market, with
every strategy change backed by one of:

- a current exact market anchor with wording/threshold/team/player/period and
  volume gates;
- a validated historical SportsPredict Brier/RBP replay;
- a validated external-data proxy that is explicitly labelled proxy-only; or
- a live execution fact such as a confirmed lineup, current prop odds,
  verified referee assignment, or a field average only if the UI behavior
  changes and exposes one before lock.

Because the contest score is Brier-based RBP, the optimization target is
calibrated probability. Intentional extremizing, variance chasing, and
leaderboard catch-up shading are not valid unless the user explicitly changes
the objective away from expected RBP.

## Rule Source

The bundled API PDF and the live API page document the contest mechanics:
integer 1-99 predictions, Brier scoring, RBP as crowd Brier minus user Brier
times 100, group/knockout/final weighting, and latest pre-close update scoring.
The public web search did not expose a more detailed official contest-rules
page beyond the SportsPredict API page at
`https://sportspredict.com/probabilitycup/api`.

## Literature Mapping

| source | useful claim | action in this repo |
| --- | --- | --- |
| Gneiting and Raftery, 2007, `Strictly Proper Scoring Rules, Prediction, and Estimation` | Proper scoring rules incentivize honest probabilistic forecasts; Brier is one of the core categorical examples. | Keep no-extremizing as a hard rule; use Brier/RBP deltas for backtests. |
| Brier, 1950, `Verification of forecasts expressed in terms of probability`; Murphy, 1973, `A new vector partition of the probability score` | Probability-score performance can be decomposed into reliability/calibration and resolution components. | Treat calibration-bucket weakness as a source-recheck warning unless a walk-forward probability transform beats the promoted policy. |
| Dixon and Coles, 1997, `Modelling Association Football Scores and Inefficiencies in the Football Betting Market` | Football scoreline probabilities are naturally generated from Poisson-style score models. | Maintain one coherent score grid for win/goals/BTTS/half/scoreline-derived questions. |
| Wolfers and Zitzewitz, 2004, `Prediction Markets` | Prediction markets aggregate dispersed information and often beat moderately sophisticated benchmarks. | Use exact Polymarket anchors only when semantic and liquidity gates pass; otherwise treat them as source checks. |
| Dudik, Lahaie, Pennock, and Rothschild, 2017, `A Combinatorial Prediction Market for the U.S. Elections` | Many related prediction-market contracts can expose useful conditional/derivative prices, but thin contracts still need market-quality controls. | Keep exact Polymarket derivative anchors under wording, subject, threshold, period, and volume gates; continue settled snapshot scoring before historical promotion. |
| Cawley and Talbot, 2010, `On Over-fitting in Model Selection...` | Optimizing model-selection criteria on finite samples creates selection bias and fake edge. | Require chronological or walk-forward holdout plus match/bootstrap uncertainty before promotion. |
| Goto, Takeishi, and Yairi, 2026, `Forecast Sports Outcomes under Efficient Market Hypothesis` | Odds conversion is itself a forecasting problem; OO-EPC/GLM methods can beat standard multiplicative/Shin/power conversions on large football odds samples. | Add OO-EPC/FL-GLM to the odds-conversion research backlog; do not promote until SportsPredict-relevant replay clears both 1X2 and totals gates. |
| Clegg, Song, and Cartlidge, 2026, `A market-calibrated accelerated failure time model for in-play football forecasting` | Calibrating model intensities to 1X2 and over/under markets is the dominant accuracy driver. | Keep current public odds as the first goal-model anchor; use extra team features only if they beat the market-calibrated baseline. |
| Rezaei and Samadi, 2026, `Predicting the 2026 FIFA World Cup with Sufficient Dimension Reduction of Elo Rating Histories` | Recent Elo history may add signal beyond current Elo in World Cup goal models. | Treat Elo-history as a candidate feature for non-priced or weakly priced national-team markets; it must beat current odds anchors out of sample. |
| Whitaker, Silva, Edwards, and Kosmidis, 2017/2020, `A Bayesian inference approach for determining player abilities in football` | Player event rates should be modelled by event type with shrinkage, not by generic star-player hunches. | Keep player props minutes/role/current-prop led; do not reuse stale opponent-specific prop values globally. |
| Yip, Tsang, and Yau, 2021, `A Bayesian approach to predict football matches with changed event counts` | Football event-count markets, especially corners, can need overdispersed count processes rather than independent Poisson assumptions. | Treat corner-specific count models as future research; live use still needs SportsPredict replay or exact current corner anchors because field values are unavailable before lock. |
| Azmat and Yi, 2024, `Expected Booking: A football metric for evaluating discipline` | Discipline/card risk can be modelled with richer event context than a match-level base rate. | Treat card/foul event models as future research only; verified referee/current-source checks remain the live rule because field values are unavailable before lock. |
| Football-Data | Provides CSV match results, statistics, referee, and pre-closing/closing odds columns. | Continue using it for large-sample odds/count/card diagnostics where SportsPredict history is too small. |
| StatsBomb open data | Provides auditable event and lineup JSON for selected tournaments. | Continue using it for special-market proxies, player priors, half shares, penalty/red, offside, and tournament-event baselines. |

## Promotion Gate

A rule can be promoted only if all applicable checks pass:

1. The candidate is predeclared before reading the result slice used to judge it.
2. The row join is exact enough for the market family.
3. The full-sample Brier/RBP delta is positive.
4. The chronological or walk-forward holdout delta is positive.
5. Match-level or block bootstrap lower bound is positive, or the limitation is
   explicitly labelled `PROMOTED_LIMITED` with a narrow support set.
6. Sensitivity checks do not reveal a brittle one-parameter optimum.
7. The implementation path used for live entries matches the backtested path.

Anything failing these checks is one of:

- `CURRENT_SOURCE_RULE`: usable current source, not a historical promotion.
- `PROXY_ONLY`: useful external prior without enough official settlement.
- `GUARDRAIL_ONLY`: use as a recheck warning, not a probability transform.
- `NOT_PROMOTED`: tested but not usable yet.
- `REJECTED`: do not spend active research time unless new evidence changes the
  premise.

## Active Research Families

The current active research universe is the 21 non-promoted/proxy/guardrail
families in `data/logs/strategy_evidence_matrix.md`:

1. `calibration_watchlist`
2. `brier_decomposition_reliability`
3. `odds_conversion_power`
4. `odds_total_source_quality`
5. `card_field_component`
6. `count_intensity_tuning`
7. `count_total_dependence`
8. `count_no_field_fallback`
9. `player_prop_boundary`
10. `player_recent_role`
11. `player_minutes_uncertainty`
12. `player_sot_rate_shrinkage`
13. `player_goal_involvement_rate_shrinkage`
14. `penalty_red`
15. `referee_card_context`
16. `referee_card_adjustment`
17. `special_markets`
18. `special_boundary`
19. `special_count_thresholds`
20. `special_prior_shrinkage`
21. `exact_polymarket_anchor_risk`

The explicitly rejected families are not active optimization candidates:

- `global_post_calibration`
- `category_linear_pool`
- `catch_up_extremizing`

## New Candidate Methods From Full Research

These are research candidates, not live rules:

1. `odds_conversion_oo_epc`: now implemented in
   `scripts/odds_conversion_backtest.py` and covered by
   `tests/test_odds_conversion_backtest.py`. The latest Football-Data replay is
   positive on point estimates, but total-goals bootstrap lower bounds remain
   negative, so it is not promoted.
2. `odds_conversion_fl_glm`: now implemented as calendar-year walk-forward
   training in `scripts/odds_conversion_backtest.py`, with no future rows used
   for each test year. It remains not promoted because the total-goals
   bootstrap lower bound is negative.
3. `odds_conversion_shin`: now implemented in
   `scripts/odds_conversion_backtest.py` and covered by
   `tests/test_odds_conversion_backtest.py`. The latest Football-Data replay
   does not promote it; power still has the best point estimate and totals still
   fail the promotion gate.
4. `elo_history_goal_residual`: now implemented in
   `scripts/elo_history_residual_audit.py` and covered by
   `tests/test_elo_history_residual_audit.py`. Recent Elo trajectory is
   positive on international 1X2 data and is classified as proxy/no-odds
   fallback evidence, but it is not allowed to override market-anchored
   probabilities until an archived odds-residual replay exists.
5. `player_minutes_uncertainty`: now implemented in
   `scripts/player_minutes_uncertainty_audit.py` and covered by
   `tests/test_player_minutes_uncertainty_audit.py`. The StatsBomb proxy replay
   is positive across 9,318 player appearances and 314 matches for SOT, scorer,
   and score-or-assist probabilities, but it is classified as proxy-only until
   official SportsPredict replay or current prop integration clears the live
   promotion gate. Keep exact prop odds and confirmed lineups above this proxy
   candidate for manual entries.
6. `player_sot_rate_shrinkage`: now implemented in
   `scripts/player_sot_rate_audit.py` and covered by
   `tests/test_player_sot_rate_audit.py`. The chronological StatsBomb proxy
   replay on 3,883 attacking-position appearances supports stronger
   app-based shrinkage for 1+ SOT, but the 2+ SOT bootstrap lower bound is
   negative. This remains proxy-only and does not change live SOT constants;
   high-threshold SOT rows still need exact prop odds or structured
   current-source research.
7. `player_goal_involvement_rate_shrinkage`: now implemented in
   `scripts/player_goal_involvement_rate_audit.py` and covered by
   `tests/test_player_goal_involvement_rate_audit.py`. Stronger scorer and
   score-or-assist shrinkage has positive point estimates, but match-block
   bootstrap lower bounds cross zero, so the current goal/involvement shrinkage
   stays in place unless official replay or current prop integration clears a
   stronger gate.
8. `referee_card_adjustment_verified`: now implemented as a proxy gate in
   `scripts/referee_card_adjustment_audit.py` and covered by
   `tests/test_referee_card_adjustment_audit.py`. Football-Data named-referee
   card threshold shrinkage is positive, while the capped lenient-ref Poisson
   variant failed. This remains proxy-only: live use still requires verified
   current official assignment plus archived independent referee card-rate
   research; field values are unavailable before lock.
9. `special_official_replay`: keep collecting official settled special outcomes
   until family-level samples are sufficient to validate or reject StatsBomb
   proxy priors.
10. `special_count_threshold_adaptation`: now implemented in
   `scripts/special_count_threshold_audit.py` and covered by
   `tests/test_special_count_threshold_audit.py`. StatsBomb threshold
   adaptation is proxy-positive for total-shots and early-corner threshold
   rows, but it is not a live transform until current-tournament count outcomes,
   archived ex-ante count-volume projections, or official SportsPredict replay
   provide deployable evidence.
11. `count_total_dependence`: now implemented in
   `scripts/count_total_dependence_audit.py` and
   `scripts/count_total_dependence_policy_replay.py`, covered by
   `tests/test_count_total_dependence_audit.py` and
   `tests/test_count_total_dependence_policy_replay.py`. The Football-Data
   club-history replay supports direct-total NB overdispersion for total-count
   thresholds, but the settled Cup replay did not promote it on the deployable
   manual policy. It remains proxy-only and does not supersede exact current
   count anchors or the current independent convolution before lock.

12. `question_family_coverage`: now implemented in
    `scripts/question_family_coverage_audit.py` and covered by
    `tests/test_question_family_coverage_audit.py`. This is not a probability
    model; it is the completeness check for the "all question types" objective.
    It verifies that every observed historical/open family key and parser
    family has a documented strategy owner and explicit status. The latest run
    covers 62 observed family keys and 41 parser family names, with 16 observed
    keys still limited, proxy-only, or guardrail-only. Those rows stay active
    research targets rather than being promoted by coverage alone.

13. `brier_decomposition_reliability`: now implemented in
    `scripts/brier_decomposition_audit.py` and covered by
    `tests/test_brier_decomposition_audit.py`. This is a diagnostic-only
    guardrail, not a production transform. It separates reliability,
    resolution, uncertainty, ECE, and bucket residuals on the same safe exact
    SportsPredict settlement sample as the manual-policy backtest. The latest
    run shows the combined policy's Brier improvement comes from better
    resolution while keeping reliability low; weak categories point back to
   field-unavailable/player/source execution rather than a calibration multiplier.

14. `corner_overdispersion_model`: future research candidate for total/team
    corner questions. External count literature supports richer corner-count
    distributions, but the current Cup replay has not promoted replacing the
    independent NB convolution. Build this only if enough settled corner rows
    or archived exact corner markets accumulate for an official replay.

15. `card_foul_event_model`: future research candidate for cards, fouls, and
    discipline-style questions. External expected-booking work supports richer
    event/context modelling, but live Cup use still lacks verified referee and
    team-style inputs with official replay. Keep this below referee
    verification and current-source checks in execution priority.

## Execution Priority

The daily prediction edge is expected to come more from live inputs than from
new global transforms:

1. Treat field averages as unavailable before prediction lock. Do not wait for
   them or fabricate them; field-led historical branches should be labelled
   `FIELD_UNAVAILABLE_PRELOCK` and entered from the no-field/current-source
   value with lower confidence.
2. Re-run current odds/Polymarket anchors and flag source disagreement.
3. Check confirmed/probable lineups and player minutes/role risk. Run
   `lineup_source_audit.py` so lineup availability is machine-readable:
   `PENDING_NOT_IN_WINDOW` is normal early, while
   `LINEUP_NOT_POSTED_IN_PULL_WINDOW` or `LINEUP_SOURCE_ISSUES` keeps player
   rows unlocked unless exact prop odds or verified lineup/role research
   supersedes them.
4. Run `current_player_stats_audit.py` for ESPN current-tournament player
   source coverage, and run `current_player_stats_replay.py` as the promotion
   gate. The current replay is point-positive but not bootstrap-robust, so major
   disagreement remains a research flag rather than an automatic probability
   override.
5. Gather exact current player prop odds where available and require
   `player_prop_anchor_status` to show no unresolved high-priority manual CSV
   anchors, while also checking `current_prop_anchor_status` for automatic
   exact Polymarket prop coverage. A partial automatic-anchor status is useful
   evidence, but it does not resolve high-priority player rows without exact
   current prop evidence or lineup/role confirmation.
6. Verify referee assignment and card history only for card-sensitive rows.
7. Recheck weak special and count-threshold special markets for current source
   disagreement. Run `current_tournament_stats_audit.py` to add ESPN public
   box-score evidence for full-match count thresholds, run
   `special_count_source_discovery.py` to separate exact public count sources
   from near-misses, and require `special_count_source_status` to be acceptable
   before treating total-shots or early-corner proxy rows as researched.
8. Keep `question_family_coverage_audit.py` complete before claiming that the
   strategy covers all foreseeable question types. A complete status proves
   ownership, not full promotion; limited/proxy/guardrail family keys still
   require the family-specific gates above.
9. Run `match_lock_readiness_audit.py` and
   `manual_submission_packet_audit.py` after evidence coverage and before
   treating any match as final. `LOCK_READY` and `READY_TO_SUBMIT` are the only
   clean lock states; hard blockers, rechecks, and proxy-only/no-field rows
   must either be resolved with current-source/player research or explicitly
   carried as unresolved risk.
10. Keep `automation_inventory_audit.py` at `PRIMARY_HEARTBEAT_READY` before
   relying on scheduled runs. The main-chat heartbeat should be the only active
   Probability Cup workflow; paused legacy standalone crons are acceptable, but
   active duplicates are not.
11. Run `goal_completion_audit.py --write` before claiming the full objective is
    done. It checks the five requested deliverables against current docs/logs,
    reports `GOAL_READY`, `GOAL_IN_PROGRESS`, or `GOAL_NOT_READY`, and keeps
    live `NOW_*` blockers separate from deferred low-value work.

## Deferred Low-Value Tasks

These are valid future hardening tasks, but they should not displace live
lineup, prop, odds, referee, and special-source work before kickoff:

1. Build a pinned `pyproject.toml`/lockfile. The one-command audited wrapper now
   exists as `scripts/manual_research_refresh.py`, but the underlying package
   versions are still resolved through explicit `uv run --with ...` command
   lines.
2. Add a browser/UI monitor that proves whether field averages ever become
   visible before lock. Current evidence and user confirmation say they do not,
   so this is diagnostic rather than a dependency for manual entry.
3. Build a corner-specific overdispersion model. External literature supports
   the idea, but live corner rows already have current-anchor/source gates and
   the settled Cup replay has not promoted a replacement count shape.
4. Build a richer card/foul event model. This remains below verified current
   referee assignment and current-source checks because live inputs dominate
   card-count execution.
5. Add archived odds-residual replay for Elo-history features. It is useful
   for no-odds fallback research, but it must not override priced goal/outcome
   markets without pre-lock archived odds data.
6. Automate exact player-prop scraping beyond Polymarket. This is high value
   only if the source is stable and legally/technically accessible; otherwise
   manual exact prop CSV plus diagnostics is more reliable.
7. Add more granular special-market priors for hydration/stoppage events.
   Current official settlement is too thin, so new priors should wait behind
   current-source checks and official replay accumulation.
8. Build a dashboard over the markdown logs. Helpful for ergonomics, but it
   does not improve probabilities unless it reduces missed field-unavailable,
   player, or source actions.

## Reproducibility Gap

This checkout has no `pyproject.toml`, lockfile, or `.venv`; the research
environment is reconstructed through explicit `uv run --with ...` command
lines. `scripts/manual_research_refresh.py` now provides the one-command audited
runner for full/live/evidence/readiness profiles and writes
`data/logs/manual_research_refresh.md`. The runner checkpoint-writes that log
after every command and supports a per-command timeout so long API/source
refreshes cannot silently consume the whole run budget. The remaining
reproducibility gap is a pinned project environment so future reruns are not
dependent on hidden local package state.
