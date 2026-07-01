# Methodology Sources And Guardrails

Last updated: 2026-06-29.

This note connects the manual-entry strategy to primary forecasting literature
and to the checks that must be true before a change is promoted. It is not a
prediction sheet and it does not authorize bot submissions.

## Primary Sources

| topic | source | pipeline implication |
| --- | --- | --- |
| Proper scoring and calibration | Gneiting and Raftery, 2007, "Strictly Proper Scoring Rules, Prediction, and Estimation" (`https://sites.stat.washington.edu/raftery/Research/PDF/Gneiting2007jasa.pdf`) | RBP is an affine transform of Brier for a fixed market, so the objective is calibrated probability, not variance-seeking or catch-up extremizing. |
| Reliability and resolution diagnostics | Brier, 1950, "Verification of forecasts expressed in terms of probability" (`https://doi.org/10.1175/1520-0493(1950)078%3C0001:VOFEIT%3E2.0.CO;2`) and Murphy, 1973, "A new vector partition of the probability score" (`https://doi.org/10.1175/1520-0450(1973)012%3C0595:ANVPOT%3E2.0.CO;2`) | Backtests should separate calibration/reliability warnings from true probability transforms. The calibration watchlist is therefore a recheck gate, not a blanket multiplier. |
| Football score model structure | Dixon and Coles, 1997, "Modelling Association Football Scores and Inefficiencies in the Football Betting Market" (`https://doi.org/10.1111/1467-9876.00065`) | Scoreline markets should be generated from one coherent Poisson-style score distribution. The repo uses a Dixon-Coles grid, fitted to current market-implied 1X2 and totals rather than stale team-strength estimates. |
| Prediction markets | Wolfers and Zitzewitz, 2004, "Prediction Markets" (`https://www.aeaweb.org/articles?id=10.1257/0895330041371321`) | Polymarket prices are treated as independent market anchors when liquidity and question wording are adequate. Exact same-question derivative markets outrank model-derived probabilities; broad 1X2/totals markets are only inputs to the score-grid model. |
| Prediction-market error and liquidity | Dudik, Lahaie, Pennock, and Rothschild, 2017, "A Combinatorial Prediction Market for the U.S. Elections" (`https://arxiv.org/abs/1702.07810`) | Prediction-market structure can expose many related contracts, but sparse derivative markets are not automatically accurate. The direct Polymarket layer keeps strict semantic gates, separate volume floors, and settled snapshot scoring before historical promotion. |
| Forecast combination | Wang, Hyndman, Li, and Kang, 2022, "Forecast combinations: an over 50-year review" (`https://arxiv.org/abs/2205.04216`) | Linear pools are legitimate forecast-combination candidates, but they must be tested out of sample. The manual family-blend backtest treats model/field linear pooling as a research candidate and rejects it unless walk-forward plus bootstrap evidence beats the promoted policy. |
| Odds conversion and favorite-longshot bias | Goto, Takeishi, and Yairi, 2026, "Forecast Sports Outcomes under Efficient Market Hypothesis" (`https://arxiv.org/abs/2604.17194`) | Their 90,014-football-match odds study supports treating betting odds as a strong benchmark while explicitly correcting overround and favorite-longshot bias. Use de-vigged exact two-sided or 1X2 prices as anchors, and do not replace validated backtests with unvalidated odds-conversion tuning. |
| Market-calibrated goal intensity | Clegg, Song, and Cartlidge, 2026, "A market-calibrated accelerated failure time model for in-play football forecasting" (`https://arxiv.org/abs/2605.16066`) | A current football model study finds that calibrating team strengths to 1X2 and over/under markets is the main accuracy driver. This supports fitting the repo's goal grid to current public markets first, then using match/event features only where they add validated incremental information. |
| Model-selection overfit | Cawley and Talbot, 2010, "On Over-fitting in Model Selection and Subsequent Selection Bias in Performance Evaluation" (`https://www.jmlr.org/papers/volume11/cawley10a/cawley10a.pdf`) | A policy is not promoted from full-sample rank alone. It needs predeclared candidates, chronological holdout or walk-forward scoring, and match-level bootstrap uncertainty. Family-mined rules are rejected unless they survive those checks. |
| Tournament event/player data | StatsBomb open data (`https://github.com/statsbomb/open-data`) | Special-market baselines, player tournament rates, half shares, and event rates must be computed from auditable event data, not hand-estimated. |
| Player event-rate modelling | Whitaker, Silva, Edwards, and Kosmidis, 2017, "A Bayesian inference approach for determining player abilities in football" (`https://arxiv.org/abs/1710.00001`) | Player props should be event-type models with minutes/role uncertainty, not generic star-player adjustments. Sparse rates need shrinkage, lineup checks, and exact current prop-market anchors before deviating from the field. |
| Corner-count overdispersion | Yip, Tsang, and Yau, 2021, "A Bayesian approach to predict football matches with changed event counts" (`https://arxiv.org/abs/2112.13001`) | Corner counts can need richer count distributions than independent Poisson. The current live rule already uses negative-binomial/count-policy guardrails, but new corner-specific models remain research candidates until SportsPredict replay clears the gate. |
| Card/foul event modelling | Azmat and Yi, 2024, "Expected Booking: A football metric for evaluating discipline" (`https://arxiv.org/abs/2401.08718`) | Card/foul risk can be modelled at event/player/team level, but the manual workflow lacks verified live referee/team-style inputs for a promoted no-field transform. Treat this as future card-model research; verified referee/current-source checks remain first because field values are unavailable before lock. |
| Historical football odds/stat data | Football-Data (`https://www.football-data.co.uk/`) | Count-model validation uses computer-readable historical match stats and odds; features should be market-implied goal rates so the model transfers across teams and competitions. |

## Enforced Strategy Rules

1. Proper-score discipline:
   - Do not extremize to chase the leaderboard gap.
   - Do not intentionally decorrelate the manual entry from the best calibrated
     estimate unless a separate, explicit risk-taking objective is requested.
   - Later-stage weighting changes market value, not the optimal probability.

2. Market hierarchy:
   - Ordinary goal/outcome markets use public odds anchors first.
   - If Polymarket prices the exact same non-player question with adequate
     individual-market volume, use that direct price before a model-derived
     probability. This is limited to exact semantic matches for wording,
     threshold, period, and team subject.
   - Count-market direct anchors are narrower than goal-market anchors: only
     exact same-match total/team corner O/U markets are currently promoted, and
     they use a stricter volume gate than ordinary goal anchors.
   - Convert odds to probabilities with margin removed before using them as
     anchors; do not fit new odds-conversion parameters unless they survive the
     same holdout/bootstrap gates as every other promoted strategy.
   - New odds-conversion papers are research leads, not automatic production
     changes. An OO-EPC, Shin, power, or favorite-longshot-bias adjustment is
     promotable only after archived pre-lock odds can be replayed against
     SportsPredict outcomes without using future results.
   - Scoreline-derived markets must come from one coherent score grid.
   - Specials/count/player markets may use local models only where the market
     does not directly price the exact question.

3. Field-average usage:
   - Field averages are not a model substitute.
   - Field-relative transforms are allowed only because they survived
     historical Brier/RBP validation in the manual policy backtests.
   - If the field is not visible, do not invent a field-relative adjustment.
   - Generic category-wise model/field linear pooling is not promoted. The
     walk-forward family-blend backtest lost heavily to the current
     family-specific policy, so use only the documented promoted family rules.

4. Overfit controls:
   - Any promoted policy must be simple and predeclared, or it must beat a
     simple fixed policy in one-match-ahead/walk-forward validation.
   - Report full-sample, chronological holdout, and bootstrap results
     separately.
   - Where a policy depends on a training-history cutoff, report a small
     cutoff-sensitivity grid and treat it as robustness evidence, not as a
     post-hoc tuning search.
   - Treat wide bootstrap intervals and small family samples as risk signals,
     not as permission to tune harder.
   - V1 history rows may be joined to SportsPredict `/results` only when the
     repeated question text has a unique inferred outcome and no result row for
     that question is Brier-uninferable. Ambiguous repeated-question rows and
     rows such as 50% submitted probability with 0.25 Brier must remain
     unsettled unless a future read-only endpoint exposes historical market IDs
     with match metadata.

5. Player-prop freshness:
   - Player props are the highest-risk family.
   - Confirmed lineups, current opponent-specific prop odds, and current player
     role information outrank stale player priors.
   - Current prop odds must be supplied as exact market anchors through
     `--player-prop-csv`; do not convert an opponent-specific prop quote into a
     reusable global player override.
   - ESPN current-tournament player stats can be used as a source cross-check
     for open player props, but current form alone is not a promoted
     probability transform. Treat large disagreement with the manual/model
     value as a research flag to resolve with exact prop odds or confirmed
     lineup evidence.
   - If no CSV anchor is supplied, exact same-match Polymarket player-prop
     markets may replace the player model only when player identity, prop
     family, threshold, and the separate player-prop volume gate all match.
     CSV anchors remain higher priority because they are explicit manual
     research inputs. A current exact prop anchor may clear stale player-source,
     team-side, minutes-override, and scorer-prop flags, but it does not clear
     lineup risk; recheck after confirmed lineups.
   - Manual pricing disables volatile global player overrides:
     opponent-specific `scorer_prop_prob` values and past-match
     `expected_minutes` values are not fed into the manual probability unless a
     current exact prop anchor or current pulled lineup supplies that evidence.
     The flags remain visible as `SCORER_PROP_RECHECK` or
     `GLOBAL_MINUTES_UNCONFIRMED`.
   - `PRIOR_ONLY`, `TEAM_UNKNOWN`, `GLOBAL_MINUTES_UNCONFIRMED`,
     `SCORER_PROP_RECHECK`, and unmatched-lineup flags must either be cleared
     with targeted research.
   - Recent-role evidence is keyed by exact SportsPredict `market_id` and may
     route rows into player-role risk worklists, but it is a guardrail only.
     Do not convert bench/rotation history into an automatic probability
     transform unless the role-policy backtest later passes the promotion
     gates. Final XI or exact current prop odds remains required before lock.
   - The no-field player fallback is source-limited: the current source-proxy
     diagnostic has holdout support from `override`/`statsbomb` rows but no
     `prior` rows, so `PRIOR_ONLY` or `TEAM_UNKNOWN` rows remain
     source-unvalidated and stay raw unless exact prop odds or structured
     player research clears them. Its promoted support is now also checked
     across a no-field sensitivity grid: the current report is positive in all
     25 tested cutoff/smoothing configurations, with worst tested delta
     +130.42 RBP versus raw.
   - Empirical player-minutes distributions have positive proxy evidence from
     StatsBomb tournament lineups/events, but they are not an official
     SportsPredict player-prop promotion. Keep point minutes in the live model
     until an official replay or current prop/lineup integration clears.

6. Read-only manual workflow:
   - Manual prediction scripts may read SportsPredict `/events`, `/lobbies`,
     `/matches`, `/markets`, and `/results`.
   - Manual prediction scripts must not read `/predictions`, must not submit,
     and must not use stored bot values as inputs.

## Evidence Gates

The current promoted manual policy is valid only while these artifacts remain
fresh and internally consistent:

| evidence | command |
| --- | --- |
| Audited full refresh runner | `python3 scripts/manual_research_refresh.py --profile full --local-date YYYY-MM-DD --espn-date YYYYMMDD --max-seconds 28800 --command-timeout-seconds 900` |
| Backtest integrity | `uv run --with requests python scripts/backtest_integrity_audit.py --sports-predict --write` |
| Combined manual policy | `uv run --with requests python scripts/manual_policy_backtest.py --sports-predict --write` |
| Family blend rejection gate | `uv run --with requests python scripts/manual_family_blend_backtest.py --sports-predict --write` |
| Brier decomposition reliability diagnostic | `uv run --with requests python scripts/brier_decomposition_audit.py --sports-predict --write` |
| Manual calibration layer | `uv run --with requests python scripts/manual_calibration_backtest.py --sports-predict --write` |
| Manual calibration watchlist | `uv run --with pandas --with numpy --with scipy --with pyarrow --with pyyaml --with requests --with statsmodels python scripts/manual_calibration_watchlist.py --all-open --write` |
| Leaderboard tail-risk objective | `uv run --with requests --with numpy --with scipy python scripts/leaderboard_tail_risk.py --sports-predict --open-mix --special-proxy statsbomb --trials 20000 --stage-weight 2.0 --write` |
| Odds source quality | `uv run --with requests --with scipy python scripts/odds_anchor_audit.py --write` |
| Odds conversion/favorite-longshot gate | `uv run --with numpy python scripts/odds_conversion_backtest.py --write` |
| Total-goals source quality | `python3 scripts/odds_total_source_audit.py --write` |
| Direct Polymarket semantic gates | `uv run --with pandas --with numpy --with scipy --with pyarrow --with pyyaml --with requests --with statsmodels python -m pytest tests/test_polymarket_direct.py tests/test_polymarket_direct_snapshot_score.py tests/test_full_manual_pricing.py tests/test_live_market_risk_audit.py` |
| Direct Polymarket coverage audit and replay snapshot | `uv run --with pandas --with numpy --with scipy --with pyarrow --with pyyaml --with requests --with statsmodels python scripts/polymarket_direct_coverage_audit.py --all-open --write --snapshot-csv` |
| Direct Polymarket snapshot scoring | `uv run --with requests python scripts/polymarket_direct_snapshot_score.py --sports-predict --write` |
| Direct Polymarket anchor risk audit | `python3 scripts/polymarket_direct_anchor_risk_audit.py --write` |
| Goal/outcome policy | `uv run --with requests python scripts/goal_policy_backtest.py --sports-predict --write` |
| Elo-history national-team proxy | `python3 scripts/elo_history_residual_audit.py --write` |
| Penalty/red policy | `uv run --with requests python scripts/penalty_red_policy_backtest.py --sports-predict --write` |
| Count policy | `uv run --with requests python scripts/count_policy_backtest.py --sports-predict --write` |
| Card-field component audit | `uv run --with requests python scripts/card_field_component_audit.py --sports-predict --write` |
| Count no-field fallback | `uv run --with requests python scripts/count_no_field_policy_backtest.py --sports-predict --write` |
| Count intensity replay | `uv run --with requests --with numpy --with scipy --with pandas --with pyarrow --with statsmodels python scripts/count_intensity_replay.py --sports-predict --write` |
| Count intensity policy replay | `uv run --with requests --with numpy --with scipy --with pandas --with pyarrow --with statsmodels python scripts/count_intensity_policy_replay.py --sports-predict --write` |
| Count total-dependence proxy | `uv run --with pandas --with numpy --with scipy --with statsmodels --with pyarrow python scripts/count_total_dependence_audit.py --write` |
| Count total-dependence Cup replay | `uv run --with pandas --with numpy --with scipy --with statsmodels --with pyarrow --with requests python scripts/count_total_dependence_policy_replay.py --sports-predict --write` |
| Referee/card live audit | `uv run --with pyyaml python scripts/referee_research_audit.py --write` |
| Referee card adjustment proxy | `python3 scripts/referee_card_adjustment_audit.py --write` |
| Player policy | `uv run --with pandas --with pyyaml --with requests python scripts/player_policy_backtest.py --sports-predict --write` |
| Player no-field fallback | `uv run --with pandas --with numpy --with scipy --with pyarrow --with pyyaml --with requests --with statsmodels python scripts/player_no_field_policy_backtest.py --sports-predict --write` |
| Player prop boundary audit | `uv run --with pandas --with numpy --with scipy --with pyarrow --with pyyaml --with requests --with statsmodels python scripts/player_prop_boundary_audit.py --sports-predict --write` |
| Current player-prop audit/template | `uv run --with pandas --with numpy --with scipy --with pyarrow --with pyyaml --with requests --with statsmodels python scripts/player_prop_diagnostics.py --sports-predict --all-open --write --anchor-template --player-prop-csv data/player_prop_anchors_template.csv` |
| Current player-stat source audit | `python3 scripts/current_player_stats_audit.py --start-date 2026-06-11 --end-date YYYY-MM-DD --sports-predict --write` |
| Current player-stat replay gate | `uv run --with requests python scripts/current_player_stats_replay.py --start-date 2026-06-11 --end-date YYYY-MM-DD --sports-predict --write` |
| Player lineup status | `uv run --with requests python scripts/player_lineup_status_backtest.py --sports-predict --write` |
| Player recent-role audit | `uv run --with requests --with pyyaml --with pandas --with numpy --with scipy --with statsmodels --with pyarrow python scripts/player_role_research_audit.py --write` |
| Player recent-role policy | `uv run --with requests --with pyyaml python scripts/player_role_policy_backtest.py --sports-predict --write` |
| Player minutes uncertainty proxy | `uv run --with numpy python scripts/player_minutes_uncertainty_audit.py --write` |
| Player SOT-rate shrinkage proxy | `python3 scripts/player_sot_rate_audit.py --write` |
| Player goal/involvement shrinkage proxy | `python3 scripts/player_goal_involvement_rate_audit.py --write` |
| Special markets | `uv run --with numpy --with scipy python scripts/special_market_backtest.py --write` |
| Special prior shrinkage rejection | `python3 scripts/special_prior_shrinkage_backtest.py --write` |
| Special live audit/source template | `uv run --with pandas --with numpy --with scipy --with pyarrow --with pyyaml --with requests --with statsmodels python scripts/special_market_live_audit.py --all-open --write --count-source-template` |
| Special boundary audit | `python3 scripts/special_boundary_audit.py --write` |
| Special count-threshold proxy | `python3 scripts/special_count_threshold_audit.py --write` |
| Current tournament count source | `uv run --with requests python scripts/current_tournament_stats_audit.py --start-date 2026-06-11 --end-date YYYY-MM-DD --sports-predict --write --update-special-count-csv` |
| Special count current-source validation | `python3 scripts/special_count_source_audit.py --write` |
| Official special outcomes | `uv run --with requests --with numpy --with scipy python scripts/special_official_results_audit.py --sports-predict --write` |
| Open-market target scale | `uv run --with requests python scripts/tournament_target_audit.py --write` |
| Live market risk audit and actionable field template | `uv run --with pandas --with numpy --with scipy --with pyarrow --with pyyaml --with requests --with statsmodels python scripts/live_market_risk_audit.py --all-open --write --field-template` |
| Manual field capture validation | `uv run --with pandas --with numpy --with scipy --with pyarrow --with pyyaml --with requests --with statsmodels python scripts/manual_field_capture_audit.py --write` |
| Manual research queue | `uv run --with pandas --with numpy --with scipy --with pyarrow --with pyyaml --with requests --with statsmodels python scripts/manual_research_queue.py --all-open --write` |
| Manual evidence coverage | `uv run --with pandas --with numpy --with scipy --with pyarrow --with pyyaml --with requests --with statsmodels python scripts/manual_evidence_coverage.py --all-open --write` |
| Question-family strategy coverage | `python3 scripts/question_family_coverage_audit.py --write` |
| Manual execution leverage | `python3 scripts/manual_execution_leverage.py --write` |
| Strategy evidence matrix | `python3 scripts/strategy_evidence_matrix.py --write` |
| Promotion strength audit | `python3 scripts/promotion_strength_audit.py --write` |
| Evidence freshness audit | `python3 scripts/evidence_freshness_audit.py --write` |
| Full goal completion audit | `python3 scripts/goal_completion_audit.py --write` |
| Daily manual sheet | `uv run --with pandas --with numpy --with scipy --with pyarrow --with pyyaml --with requests --with statsmodels python scripts/full_manual_pricing.py --local-date YYYY-MM-DD` |

Use `scripts/manual_research_refresh.py` for routine execution because several
freshness gates are order-sensitive. The evidence profile scores archived
Polymarket direct snapshots after refreshing direct-anchor coverage, while the
readiness phase rebuilds the calibration watchlist and card-field component
after the open-board, referee, and execution-leverage inputs they read. Running
those reports out of order can create false stale/inconsistent freshness rows.

If a candidate change cannot be expressed as an evidence gate, it should remain
an operational note rather than a promoted strategy rule.

After refreshing API-backed reports, run the evidence freshness audit before
using `data/logs/strategy_evidence_matrix.md` as a decision source. It checks
that exact-history reports still match the current safe-settlement sample,
that official special/Polymarket scoring reports used the same `/results`
snapshot as the integrity audit, that open-board reports agree on the same
market count, that execution-leverage coverage states match the coverage audit,
that the calibration watchlist was built from the current all-open board,
that the Brier decomposition audit uses the same safe settlement sample as the
manual policy and calibration reports,
that exact Polymarket anchor counts reconcile between the direct-coverage and
manual-evidence reports, that the odds-conversion report has both 1X2 and
total-goals historical gates, that the total-goals source audit covers every
current manual coverage total/team-goal row, that execution categories
reconcile to the odds, player-role, player-prop diagnostics, player boundary,
special, special boundary, and referee/card live-family reports, that the
card-field component audit matches the current open card worklist, that the
count-total dependence proxy and settled Cup replay gates have positive scored
rows and explicit decisions, that
`strategy_evidence_matrix.md` is newer than the timestamped reports it
summarizes, and that
`promotion_strength_audit.md` is newer than the strategy matrix it interprets.
The manual evidence coverage report also carries a `direct_anchor_risk` column
from `polymarket_direct_anchor_risk_audit.md`, so large exact-anchor shifts
remain visible in the daily worklist even when the main coverage state is a
field or player execution gate.

`data/logs/question_family_coverage_audit.md` is the broad "all question
types" ownership check. It combines observed historical family keys and the
latest open-market coverage log, then maps each family to its strategy area and
promotion status. A complete report does not mean every family is fully
promoted: the latest run has `COMPLETE_WITH_LIMITED_PROXY_OR_GUARDRAIL_ROWS`,
62 observed family keys, 41 parser family names, and 16 observed keys that are
limited, proxy-only, or guardrail-only. Those weak rows remain research targets
rather than hidden production rules.

`data/logs/special_count_threshold_audit.md` is the focused proxy gate for
total-shots and early-corner special thresholds. The latest StatsBomb replay
scores 942 threshold rows across 314 matches and 6 tournaments. Chronological
in-tournament adaptation using count means has a positive tournament-block
bootstrap lower bound, but this remains proxy-only: live entries need
current-tournament count outcomes, archived ex-ante count-volume projections,
or official SportsPredict replay before using adaptation as a probability
transform. Until then, total-shots and early-corner rows remain research/source
checks when they are high-heterogeneity or far from the broad base rate.

`data/logs/count_total_dependence_audit.md` is the focused proxy gate for
ordinary total-count threshold shape. The latest Football-Data replay scores
535,122 chronological club-history total-count rows using train-only marginal
count models and finds a positive match-block bootstrap for `direct_total_nb`
versus the current independent-team NB convolution. The stricter settled Cup
replay in `data/logs/count_total_dependence_policy_replay.md` did not promote
that candidate: only 32 settled total-count rows were available, manual-policy
delta was slightly negative, and the lower bootstrap bound crossed zero.
Therefore exact current count anchors and the current independent total-count
shape remain the live no-field rule.

`data/logs/player_sot_rate_audit.md` is the focused proxy gate for player
shots-on-target rate shrinkage. The latest chronological StatsBomb replay
scores 3,161 future attacking-position appearances, with a positive
match-block bootstrap lower bound for stronger app-based shrinkage on 1+ SOT
rows. The same candidate does not clear the lower-bound gate for 2+ SOT, so it
is not a live SOT-constant change; high-threshold SOT rows remain
prop/source-led until official settlement or current prop integration supports
otherwise.

`data/logs/player_goal_involvement_rate_audit.md` is the focused proxy gate
for scorer and score-or-assist rate shrinkage. The latest chronological
StatsBomb replay scores the same 3,161 future attacking-position appearances.
Stronger shrinkage has positive point estimates, but scorer and
goal-or-assist match-block bootstrap lower bounds cross zero, so this is a
tested non-promotion rather than a live model change.

`data/logs/brier_decomposition_audit.md` is the calibration diagnostic layer.
It computes Murphy-style reliability, resolution, uncertainty, ECE, and bucket
residual terms for current, field, and combined manual policy probabilities on
the same 529 safe exact rows as the manual-policy backtest. The latest run
shows the combined manual policy has lower Brier through better resolution
(`0.0303` vs current `0.0239`) while keeping reliability low (`0.0027`). The
weakest category by reliability is card counts, followed by corners/player
rows, which supports the existing execution priority: clear current-source and
player-source gaps rather than adding a global calibration multiplier.

`data/logs/promotion_strength_audit.md` separates strong promotions from soft,
not-promoted, or component-warning promotions. The latest audit covers 8 major
strategy areas and has 6 warning rows: raw odds-goal rows are directionally
useful but soft because the direct raw-vs-field bootstrap lower bound is
slightly negative; power de-vig is not promoted because the 1X2 signal is
strong but the totals bootstrap lower bound is negative; count field policy is
overall strong historically but field is unavailable before lock; researched
player props are overall-positive but unresolved rows still need exact
prop/lineup evidence; exact Polymarket anchors remain current-source only with
negative early replay; and special markets remain proxy-only. This audit is
classification-only and does not move probabilities.

`data/logs/odds_conversion_backtest.md` is the favorite-longshot/overround
guardrail. The latest local Football-Data run tests 115,809 1X2 closing-odds
rows and 47,344 over/under 2.5 rows from 2009-07-31 to 2026-05-31, now covering
multiplicative, additive, power, Shin, odds-ratio, OO-EPC, and FL-GLM
walk-forward conversions. Power de-vig has the best full-sample 1X2 point
estimate; OO-EPC has the best recent 1X2 point estimate and the best totals
point estimate; FL-GLM is trained on past calendar years only. The 1X2
candidate bootstrap lower bounds are positive, but the total-goals candidate
bootstrap lower bounds remain negative (best totals lower bound -25.83 x100).
Because the live score grid depends on both 1X2 and totals, the current
decision is `not_promoted_keep_multiplicative_devig`: keep proportional
de-margin and monitor favorite-longshot-aware de-vig until totals clear the
same gate.

`data/logs/elo_history_residual_audit.md` is the national-team Elo-history
proxy gate. The latest local international-results run tests 49,405 source
matches and 29,902 scored matches since 1994. A recent 10-match Elo trajectory
feature is positive against plain Elo with a year-block bootstrap lower bound
of +2,079.95 x100 Brier units, so it remains useful as no-odds fallback
evidence. It is not a live override for priced goal/outcome markets: public
odds and exact current market anchors still outrank Elo until an archived
odds-residual replay proves incremental value after market calibration.

`data/logs/player_prop_boundary_audit.md` is the player-family guardrail that
prevents overextending the no-field result. The latest read-only
SportsPredict-settled run has 80 settled player rows and 23 current open
player props. The no-field `raw_scores_else_global_base` policy remains strong
(+305.36 RBP, bootstrap lower bound +137.49), but there are 0 higher-threshold
SOT rows in its chronological holdout. The researched-player asymmetric policy
does not beat field directly with a positive lower bound (`asym075_minus_field`
ci_2.5 -78.94), and above-field player model rows were dangerous historically
(field-minus-raw +76.02 RBP, lower bound +25.60). The current board has 3
source-unvalidated rows and 7 high-threshold SOT rows. Decision: use field or
exact prop odds for unresolved/source-poor/high-threshold rows; apply the
no-field base only inside tested support.

`data/logs/player_minutes_uncertainty_audit.md` is the player-minutes proxy
research gate. The latest StatsBomb tournament replay reconstructs
substitution-aware minutes from raw event JSON for 9,318 player appearances
across 314 matches. Player-recent minutes distributions beat a no-lineup
90-minute point assumption for SOT, scorer, and score-or-assist families, and
role empirical distributions beat the live-style starter-90/substitute-20
point assumption in all three families with positive match-bootstrap lower
bounds. The decision is
`proxy_candidate_minutes_uncertainty_positive_not_live_promoted`: use this as
evidence for future current prop/lineup integration, not as a live override for
manual entries.

`data/logs/card_field_component_audit.md` isolates the highest-leverage weak
part of the count policy. The latest read-only SportsPredict-settled run has
39 settled card rows and 22 settled card-threshold rows. Visible field beat the
raw card model by +157.01 RBP on all card rows, +74.12 RBP in chronological
holdout, with match-bootstrap lower bound +26.64. On deployable card-threshold
rows only, field beat raw by +127.96 RBP with lower bound +20.47. But the
incremental component inside the full count policy still has a negative lower
bound (-47.85), and the no-field card-threshold fallback remains unpromoted
(-69.82 lower bound). The operational decision is therefore narrow: pre-lock
manual entries keep neutral/raw card values unless verified referee/current
source context clears the row. The field-led branch is historical/diagnostic
unless SportsPredict changes the UI and exposes field averages before lock.

`data/logs/referee_card_adjustment_audit.md` is the historical referee-card
proxy gate. The latest Football-Data replay uses 37,822 matches with named
referees, 353 unique referees, and card thresholds 3+, 4+, 5+, and 6+ from
2009-08-07 through 2026-05-24. Referee threshold-rate shrinkage beats the
league prior with a positive match-bootstrap lower bound, but the capped
lenient-ref Poisson variant loses. The decision is
`proxy_candidate_referee_threshold_positive_not_live_promoted`: referee
identity can matter, but live entries still need a verified current official,
independent current card-rate research, and field-first handling for
card-count rows.

The odds source quality gate must confirm that open goal/outcome matches are
not being priced from a stale or mis-oriented single feed. The 2026-06-28 fix
requires checking both Polymarket's generic soccer tag and the
`series_slug=soccer-fifwc` World Cup series, because current match-specific
World Cup 1X2 markets can be absent from the generic tag response while present
in the series response. If the audit reports `BOOK_ONLY`,
`MEDIUM_DISAGREEMENT`, `HIGH_DISAGREEMENT`, or `LOW_VOLUME_PM` for a match,
that match needs targeted odds-source review before lock. The audit also logs
Polymarket liquidity and `blend_shift`, the maximum probability-point movement
from ESPN/DraftKings-only to the current 50/50 book/Polymarket blend. Thin but
near-identical Polymarket rows are monitoring issues; thin rows with material
`blend_shift` are source-quality issues.

`data/logs/odds_total_source_audit.md` is the narrower total-goals source
guardrail. It reconciles current manual coverage total/team-goal questions with
same-question direct total anchors from `polymarket_direct_coverage_audit.md`.
The latest run found 9 current total/team-goal rows: 7 direct anchors, 2 rows
without direct anchors, 3 thin but aligned direct anchors, 0 large direct/model
shifts, and 0 manual-entry/direct-anchor mismatches. Missing or thin direct
total anchors are recheck priorities only; they are not an automatic averaging
or override rule.

The direct Polymarket derivative layer is a current-source hierarchy rule, not
a historical strategy promotion. Matched same-question derivative rows are now
archived to `data/logs/polymarket_direct_anchor_snapshots.csv` and scored by
`data/logs/polymarket_direct_snapshot_score.md` after official settlement. The
latest score report has 100 raw archived snapshot rows, 99 semantically valid
snapshot rows across 21 unique markets, 4 scored latest markets, 17 unscored
latest markets, latest-per-market Brier delta `-0.0289`, and
historical-promotion decision `not_promoted_insufficient_sample`, so the gate
remains intentionally conservative: exact
non-player goal markets require wording/threshold/period/team matching, exact
player-prop markets require same-match player/family/threshold matching, and
exact count markets are limited to same-match total/team corner O/U markets
with a stricter volume floor. Tests cover the most dangerous false matches such
as using a match total as a team total, using a match corner total as a team
corner total, a wrong player-prop threshold, or a "neither team/no goal" first
goal market as a target-team first-goal market. Card and non-corner count
markets are not promoted
through this layer unless the implementation has an exact semantic mapping and
an evidence gate.
`data/logs/polymarket_direct_anchor_risk_audit.md` is a guardrail over the same
current-anchor set, not a probability transform. The latest run reconciles all
17 current exact anchors, flags 3 high-risk large shifts, and keeps the branch
at `guardrail_only_no_probability_transform`: recheck the large player-prop
moves and the Germany halftime-lead direct anchor against lineup, field,
wording, and alternate source evidence before lock, while continuing the
archived latest-per-market scoring.

Penalty/red rows stay under a sparse low-event guardrail. The current
`penalty_red_policy_backtest.md` has only 24 settled rows: the global
asymmetric rule is positive versus current in full sample and in all 6 tested
chronological cutoffs, but the match-bootstrap lower bound remains negative and
the rule is the best cutoff policy only once. This supports avoiding aggressive
movement above the low-event base without a direct current anchor; it does not support a
referee-specific penalty/red transform or a promoted family edge.

The manual research queue is an execution-control gate, not a probability
model. It reuses the live risk audit and ranks only rows where current external
facts can change the manual entry: unresolved player-prop flags, no-field risk
on historically field-led count families, weak special priors, and sparse
penalty/red rows. Run it before nightly sheets and again near lock so research
time is spent on validated leverage rather than low-risk odds-anchored rows.

The player-prop diagnostic is the source-quality gate for the weakest live
family. It now separates eligible exact same-match Polymarket player-prop
anchors from same-player near misses: exact props below the volume floor are
logged as research warnings, and wrong-family or wrong-threshold markets are
summarized without changing probabilities. Do not lower the player-prop volume
gate or use a thin near miss as a manual anchor unless separate current prop
research supports it.

`data/logs/current_player_stats_audit.md` is the current-tournament player
source layer. It uses ESPN public scoreboard/summary roster stats, never
`/predictions`, to aggregate goals, goal involvements, shots on target,
appearances, starts, and substitute entries for every open player prop. The
2026-06-29 run has `current_player_stats_status: COMPLETE`, 2,301 completed
player-appearance rows, and 23 matched open player props. It is a source and
disagreement audit, not a promoted transform: exact prop odds and confirmed
lineups still outrank it.

`data/logs/current_player_stats_replay.md` is the promotion gate for that
source. It replays settled player props by mapping each historical match to an
ESPN event and using only player lines from earlier completed events, so
same-match stats cannot leak into the scored row. The 2026-06-29 replay matched
45 of 80 settled player rows and was point-positive for current-form
probabilities, but the match-bootstrap lower bound was negative
(`ci_2.5=-123.89`, `Pr>0=0.864`). Decision:
`NOT_PROMOTED_DIAGNOSTIC_ONLY`. Use large disagreement as a research flag, not
as an automatic manual-entry override.

The manual evidence coverage audit is the row-level proof gate. It joins the
live risk audit to the strategy evidence matrix, so every open market is mapped
to a status such as `PROMOTED`, `PROMOTED_LIMITED`, `CURRENT_SOURCE_RULE`,
`PROXY_ONLY`, or `GUARDRAIL_ONLY`, plus an unresolved gate such as field
required, player research required, `CURRENT_ANCHOR_RECHECK`, proxy-only, or a
special count-threshold recheck. Use it to verify that the manual sheet is not
silently relying on unpromoted or unexamined branches.

The manual execution leverage audit is a work-ranking layer over the coverage
audit. It uses historical RBP-per-row contribution from the manual policy
backtest to prioritize unresolved gates. It is not an expected-value forecast
and must not change probabilities; its job is to keep scarce research time
focused first on high-leverage execution gaps such as card field capture and
hard player-prop research, while still surfacing special count-threshold proxy
warnings that lack official SportsPredict holdout evidence.

The count no-field fallback remains unpromoted despite a stable point estimate.
The current report's best candidate, `card_threshold_stat_base_else_raw`, is
positive in all 25 tested cutoff/smoothing configurations and is the best
deployable no-field candidate in all 25, but its match-bootstrap lower bound is
still negative. Therefore missing-field count rows should keep raw count model
values unless a validated exact current corner anchor applies; card-threshold
no-field rows are monitoring candidates, not a promoted fallback.

The manual calibration watchlist is also an execution-control gate, not a
probability model. It reads the rejected calibration audit and the latest
manual-pricing sheet, then flags live entries that sit in historically weak
combined-policy reliability buckets. A watchlist flag means recheck the
underlying source quality, exact-market wording, or lineup facts
before lock. It does not authorize a global calibration multiplier, because the
walk-forward calibration layer lost RBP and worsened holdout calibration.

Special-market changes need an even stricter proxy gate because official
SportsPredict special rows are still unsettled. `special_market_backtest.py`
now includes binary leave-one-tournament-out priors, no-match-info count
threshold priors for current total-shot and early-corner questions, oracle
realized-volume scaling, which can only validate the shape of hazard scaling,
chronological in-tournament adaptation, which uses only earlier matches from
the same held-out tournament, and tournament-heterogeneity diagnostics. The
latest heterogeneity check finds 2 high-spread evidence keys, 7 moderate, and 4
low; the high-spread keys are total-shots thresholds, so those rows need extra
count-volume scrutiny. `special_market_live_audit.py` maps every live special
row to those proxy evidence keys and flags weak, far-from-base, and
high-heterogeneity rows; the latest all-open audit has 20 future special rows:
10 `BASE_PRIOR`, 2 `COUNT_THRESHOLD_HIGH_HETEROGENEITY`, 1
`COUNT_THRESHOLD_PROXY`, 1 `COUNT_THRESHOLD_SCALED_FAR_FROM_BASE`, and 6
`WEAK_PRIOR`. It also logs signed `model_minus_base_pp`, so the direction of
proxy disagreement is visible. The manual research queue now promotes
far-from-base count-threshold specials into a distinct high-priority bucket and
keeps high-heterogeneity total-shots specials separate from ordinary special
priors. This audit does not change probabilities. It is a row-level
source/model-volume recheck gate. Do not convert current-tournament event
trends into probability adjustments unless the chronological replay has
positive bootstrap support.

`data/logs/current_tournament_stats_audit.md` is the current-tournament count
source layer. It uses ESPN public scoreboard/summary box scores, never
`/predictions`, to extract completed-match shots, SOT, corners, fouls,
offsides, cards, and red cards. The 2026-06-29 run has
`current_tournament_stats_status: COMPLETE` with 73 completed matches and 146
team-match rows. It can fill one source slot for full-match total-shot special
thresholds, but it is not a promoted transform and it cannot clear
first-hydration corner rows because ESPN box scores do not expose that split.

`data/logs/special_boundary_audit.md` is the special-family boundary guardrail.
The latest run covers 26 open special rows and only 11 official settled special
rows, so the branch remains proxy-backed rather than official-history
validated. It identifies 6 weak near-coinflip rows, 3 count-threshold rows that
are high-heterogeneity or far from the no-match-info base, and a maximum live
scaled-vs-base shift of 40 percentage points. Automatic weak-prior shrinkage
and knockout in-tournament adaptation both remain unpromoted. Decision:
`raw_proxy_with_guardrails_no_shrink_no_official_promotion`.

The strategy evidence matrix is the promotion-control gate. It reads the latest
backtest logs and labels each branch as `PROMOTED`, `PROMOTED_LIMITED`,
`CURRENT_SOURCE_RULE`, `PROXY_ONLY`, `GUARDRAIL_ONLY`, `NOT_PROMOTED`, or
`REJECTED`. A strategy branch should not be described as optimized unless the
matrix status supports that claim and the branch's documented scope matches the
live market being priced.
