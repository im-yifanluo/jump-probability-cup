# Manual Strategy Catalog

Last updated: 2026-06-29.

This file explains the current manual-entry strategies by market family. It is
manual-only documentation: do not submit bot predictions from it, do not read
stored bot predictions, and do not use `/predictions` as a manual input.

The authoritative status table is generated at
`data/logs/strategy_evidence_matrix.md`. This file is the human-readable
operating version. Literature sources and evidence gates are maintained in
`docs/methodology_sources.md`.

Question-family ownership is audited separately in
`data/logs/question_family_coverage_audit.md`. The latest run covers 62
observed historical/open family keys and 41 parser family names. Its status is
`COMPLETE_WITH_LIMITED_PROXY_OR_GUARDRAIL_ROWS`, meaning every observed family
has an owner, but player, special, penalty/red, and other weak branches remain
limited/proxy/guardrail where the evidence says they should.

Match-level lock status is generated at
`data/logs/match_lock_readiness_audit.md`. It is an execution checklist, not a
probability model: a match is only cleanly ready for final manual entry when
the decision is `LOCK_READY`.

Row-level packet status is generated at
`data/logs/manual_submission_packet_audit.md`. This is also an execution
checklist: `READY_TO_SUBMIT` is the only clean packet status, while
`NOT_LOCKED_REQUIRED_INPUTS` means generated probabilities are still
provisional because hard player or special-count inputs are unresolved. Field
averages are not visible before prediction lock and are not a live missing
input. The same packet now splits non-ready rows by actionability: `NOW_*`
rows are the overnight/morning research work, `NEAR_LOCK_*` rows are the
pre-match lineup/current-source recheck work, `LOW_CONFIDENCE_*` rows are
explicit provisional entries, and `NO_FIELD_ACTION_PRELOCK` rows are accepted
no-field risk rather than research tasks. Completed negative research is stored
in `data/manual_research_resolutions.csv`; it moves a row out of `NOW_*` and
into `NEAR_LOCK_RESEARCHED_SOURCE_RECHECK`, which still requires a final
lineup/current-source check before lock.

## Status Labels

| status | meaning |
| --- | --- |
| `PROMOTED` | Full-sample and chronological/holdout evidence are positive, with adequate uncertainty support. |
| `PROMOTED_LIMITED` | Evidence is positive only inside an explicit support set. Do not extrapolate. |
| `CURRENT_SOURCE_RULE` | Usable current market/source hierarchy, but not a historical edge claim. |
| `PROXY_ONLY` | External proxy is useful, but official SportsPredict settlement is too thin. |
| `GUARDRAIL_ONLY` | Use as a warning or recheck list, not as a probability transform. |
| `NOT_PROMOTED` | Tested and not good enough to use yet. |
| `REJECTED` | Failed evidence gates; do not spend active time unless new evidence changes the premise. |

## Main Manual Policy

Status: `PROMOTED`

Historical replay validates `src/manual/policy.py::manual_decision` when field
averages are supplied. The current pre-lock SportsPredict UI does not expose
those field values, so live manual entries use no-field/current-source branches
unless an exact current source or lineup/role evidence is available. Latest
validated replay:

- 529 safe exact settled rows.
- +351.37 RBP versus current.
- +279.05 RBP chronological holdout.
- Match-bootstrap lower bound +114.59 RBP.

Allowed action: use the family-specific manual policy only when the required
live input exists. Before lock, do not wait for or fabricate field values.

## Goal, Win, Team Goal, And Total Goal Markets

Status: goal policy `PROMOTED`; source quality `CURRENT_SOURCE_RULE`.

Method:

1. Prefer exact same-question current Polymarket derivative anchors when
   wording, team, threshold, period, and volume gates pass.
2. Otherwise use public bookmaker odds and Polymarket 1X2/totals as anchors.
3. Fit the Dixon-Coles-style score grid to current market-implied 1X2 and total
   goals.
4. Price all goal-derived questions from the same score grid.
5. Do not shrink ordinary goal rows to field average.

Evidence:

- Raw odds-goal values beat field on 169 goal rows.
- Holdout delta +91.49 RBP.
- Bootstrap probability positive, but lower bound is slightly negative, so the
  promotion is operationally useful but still soft.

Execution guardrails:

- Run `scripts/odds_anchor_audit.py` for stale/misoriented source detection.
- Run `scripts/odds_total_source_audit.py` for total/team-goal anchor coverage.
- Thin or disagreed odds rows need near-lock source review.

## Bookmaker Overround Conversion

Status: `NOT_PROMOTED`

Current live rule: keep proportional multiplicative de-margin.

Tested methods:

- multiplicative
- additive
- power
- Shin
- odds-ratio
- OO-EPC
- FL-GLM walk-forward

Latest Football-Data replay:

- 115,809 1X2 closing-odds rows.
- 47,344 over/under 2.5 rows.
- Power has the strongest full-sample 1X2 point estimate.
- OO-EPC has the strongest recent 1X2 point estimate and the strongest totals
  point estimate.
- FL-GLM is trained only on past calendar years in the replay.
- 1X2 candidate bootstrap lower bounds are positive.
- Total-goals candidate bootstrap lower bounds are negative.

Allowed action: no live probability change. Keep monitoring
favorite-longshot-aware conversion until both 1X2 and totals clear the gate.

## Elo History Residual For National Teams

Status: `PROXY_ONLY`

Method:

- Build chronological national-team Elo ratings from the bundled international
  results CSV.
- Test recent Elo trajectory as a 10-match rating-change feature.
- Score only future rows; do not use future results to set a team's rating.
- Compare the best history model against plain Elo with a year-block bootstrap.

Evidence:

- 49,405 source international matches.
- 29,902 scored matches since 1994.
- Best history model: `elo_history_w0.75`.
- Year-bootstrap lower bound versus plain Elo: +2,079.95 x100 Brier units.

Allowed action: use only as no-odds fallback evidence or as a candidate for a
future archived odds-residual replay. Do not move priced goal/outcome markets
away from public odds anchors.

## Count Markets

Status: historical field policy `PROMOTED`, but field values are unavailable
before prediction lock; no-field fallback `NOT_PROMOTED`; intensity tuning
`NOT_PROMOTED`; total-count dependence `PROXY_ONLY`.

Families include cards, corners, SOT, offsides, fouls, and head-to-head count
comparisons.

Pre-lock rule:

- Cards: use no-field/current-source value and carry `FIELD_UNAVAILABLE_PRELOCK`.
- Strong SOT team/head-to-head rows: use raw model.
- Other count rows: use no-field/current-source value and carry
  `FIELD_UNAVAILABLE_PRELOCK`.

Evidence:

- 256 count rows.
- +186.56 RBP full sample.
- +117.70 RBP holdout.
- Bootstrap lower bound +37.48 RBP.

No-field rule:

- Use raw count model unless an exact current corner anchor passes gates.
- Do not apply general no-field card/count fallback.
- Because field values are unavailable before lock, ordinary card/count rows
  are `FIELD_UNAVAILABLE_PRELOCK` low-confidence rows, not actionable field
  capture tasks.

Total-count shape:

- The production model still convolves independent home/away NB count
  distributions.
- A direct-total NB overdispersion model is proxy-positive on Football-Data
  club history, but the settled Cup total-count replay did not promote it on
  the deployable manual policy.
- Keep the independent convolution live unless future official replay or exact
  current count-source evidence clears the gate.

Execution guardrails:

- Do not wait for or fabricate field averages before lock.
- Do not mine narrower count-family transforms from point estimates alone.
- Count-intensity variants are monitored but not promoted.
- Treat direct-total NB as a research lead for ordinary total-count thresholds,
  not as permission to override exact current anchors, the historical
  field-policy evidence, or the current independent model.

## Card And Referee Markets

Status: card field component `GUARDRAIL_ONLY`; referee context
`GUARDRAIL_ONLY`; referee card adjustment `PROXY_ONLY`.

Method:

- If SportsPredict changes the UI and exposes card field averages before lock,
  card-count rows are field-led under the historical policy.
- Without field, keep neutral/raw card model unless a verified referee
  assignment and independent card-rate research are both archived before lock.
- Do not add a generic strict-referee upward push.

Evidence:

- Visible field beat raw card model by +157.01 RBP on settled card rows.
- The incremental component inside the full count policy has a negative lower
  bound, so this is an execution priority, not a broader no-field transform.
- Referee-specific historical threshold-rate shrinkage is proxy-positive on
  37,822 Football-Data matches with 353 named referees, but the capped
  lenient-ref Poisson variant failed. This supports current-referee research
  when an official is verified; it does not authorize automatic card movement.

Execution guardrails:

- Card rows are low-confidence no-field rows unless a verified referee/current
  source justifies movement.
- Referee assignment must be verified; missing ESPN official fields are not
  evidence.

## Player Props

Status: researched player policy `PROMOTED_LIMITED`; no-field fallback
`PROMOTED_LIMITED`; current-player-stat replay `PROXY_ONLY`; boundaries and
recent role `GUARDRAIL_ONLY`; minutes uncertainty and SOT-rate shrinkage
`PROXY_ONLY`; goal/involvement rate shrinkage `NOT_PROMOTED`.

Families include player scorer, score-or-assist, and shots on target.

Preferred evidence order:

1. Exact current player prop CSV supplied by manual research.
2. Exact same-match Polymarket player prop if player, family, threshold, and
   volume gates pass.
3. Confirmed lineup/minutes and exact current role information.
4. ESPN current-tournament player stat audit as a source cross-check.
5. Model/no-field fallback only inside tested support.

No-field fallback support:

- Raw scorer probabilities.
- Global-base fallback for score-or-assist and 1+ SOT rows.
- Excludes prior-only, team-unknown, confirmed bench, unmatched lineup, and
  2+/3+ SOT rows.

Current warnings:

- Recent bench/rotation history is a research warning, not a transform.
- Current-tournament ESPN player stats are now audited for every open player
  prop and replayed leakage-safely on settled player rows. The 2026-06-29
  replay is point-positive but not bootstrap-robust, so it remains a
  disagreement/source-coverage check rather than a promoted replacement for
  exact prop odds or confirmed lineups.
- `lineup_source_status` tracks whether ESPN has posted verified lineups for
  open player-prop matches. `PENDING_NOT_IN_WINDOW` keeps scheduled rechecks
  alive; posted lineups with unmatched players are a source issue, not a
  reason to trust default minutes.
- `player_prop_anchor_status` tracks the filled manual prop CSV, while
  `current_prop_anchor_status` tracks automatic exact Polymarket player-prop
  coverage. Automatic anchors can replace the model probability for that exact
  market, but they do not clear final-lineup risk or make unanchored
  high-priority player rows researched.
- Final XI, exact prop odds, or structured role/player research must resolve
  live player risk.
- Above-field player model rows were historically dangerous.
- Empirical minutes distributions beat point-minutes assumptions in a
  StatsBomb proxy replay over 9,318 appearances and 314 matches, with positive
  lower bounds for SOT, scorer, and score-or-assist families. This is not yet
  official SportsPredict validation, so it supports future current-prop/lineup
  integration work but does not override the live point-minutes policy.
- Stronger app-based SOT-rate shrinkage beats the current k=1 approximation
  for 1+ SOT in a chronological StatsBomb proxy replay, but it does not clear
  the lower-bound gate for 2+ SOT. Treat this as support for 1+ SOT research
  and as a reason to keep high-threshold SOT rows prop/source/lineup-led.
- Stronger scorer and score-or-assist shrinkage has positive point estimates,
  but bootstrap lower bounds cross zero. Keep the current goal/involvement
  shrinkage unless official replay or current prop integration clears a
  stronger gate.

## Penalty And Red-Card Markets

Status: `GUARDRAIL_ONLY`

Method:

- Use sparse low-event base/global asymmetric policy.
- Avoid moving above the low-event base without direct current evidence.
- No referee-specific penalty/red transform is promoted.

Evidence:

- 24 settled penalty/red rows.
- Point estimates are small and uncertainty crosses zero.

## Special Markets

Status: `PROXY_ONLY`; special boundary `GUARDRAIL_ONLY`; count-threshold
adaptation `PROXY_ONLY`; shrinkage `NOT_PROMOTED`.

Examples:

- goal before/after hydration break
- goal in stoppage time
- offside before hydration
- card after hydration
- substitution before halftime
- substitute scores
- any player 2+ SOT
- any player 2+ goals
- total shots/corners before hydration or high total-shot thresholds

Method:

- Use StatsBomb tournament-event priors as proxy evidence.
- Do not apply generic field transforms.
- Do not shrink all weak priors toward 50%.
- Treat weak timing priors and count-threshold volume scaling as research
  warnings.

Evidence:

- 314 StatsBomb matches support proxy baselines.
- Only 11 official settled SportsPredict special rows are currently usable.
- Official sample is too small for family-level promotion.
- Automatic special-prior shrinkage failed the bootstrap gate.
- Count-threshold special adaptation is proxy-positive on 942 leave-one-
  tournament-out threshold rows, but it is not a live transform without
  current-tournament count outcomes, ex-ante count-volume data, or official
  SportsPredict replay.

Execution guardrails:

- Recheck weak near-coinflip rows against current-source disagreement.
- Verify count-volume inputs for total-shots and early-corner count-threshold
  specials.
- Use `special_count_source_discovery.py` before accepting public-market count
  evidence; exact same stat/threshold/period is required, and first-half
  corners or ordinary goal totals are non-sources.

## Exact Polymarket Anchors

Status: `CURRENT_SOURCE_RULE`; risk audit `GUARDRAIL_ONLY`.

Method:

- Use exact same-question Polymarket anchors only when semantic and volume gates
  pass.
- Gates include team/player identity, market family, threshold, period, and
  enough market volume.
- Separate stricter gate applies to corner-count anchors.

Evidence:

- Current direct-anchor replay sample is still too small.
- Latest scored snapshot sample is negative and insufficient.

Allowed action:

- Use exact anchors as current source hierarchy, not as a historical promotion.
- Keep archiving latest matched rows and score them after results settle.

## Calibration And Blending

Status: watchlist `GUARDRAIL_ONLY`; Brier decomposition `GUARDRAIL_ONLY`;
global calibration `REJECTED`; category linear pooling `REJECTED`.

Method:

- Use calibration watchlist buckets as recheck prompts only.
- Use Brier decomposition to separate reliability and resolution diagnostics.
- Do not apply a global distance-from-50 multiplier.
- Do not replace family policy with adaptive category model/field linear pools.

Evidence:

- Global post-calibration lost on walk-forward paired delta.
- Category linear pooling lost badly to the combined manual policy.
- The latest decomposition shows combined manual policy Brier `0.2179`,
  reliability `0.0027`, resolution `0.0303`, and worst bucket `0.80-1.00`.
  The improvement over current is mainly resolution, not a reason to add a
  bucket transform. Weak category reliability is concentrated in card counts,
  corners, and player rows, matching the field-unavailable and player research queue.

## Leaderboard Catch-Up

Status: `REJECTED`

Method:

- Do not hard-extremize probabilities to chase the leaderboard.
- The only valid catch-up lever is better calibrated edge: lineups, exact prop
  odds, source checks, and special/count research. Field averages remain useful
  historical evidence, but they are unavailable before lock.

Evidence:

- Tail-risk simulation showed hard 99/1 strategies had negative mean and near
  zero target-hit probability.

## Active Research Backlog

Still worth researching or monitoring:

- Archived odds-residual replay for Elo-history features once enough
  pre-lock odds snapshots are available.
- Official SportsPredict replay/current-prop integration for the
  proxy-positive player-minutes uncertainty result.
- Official SportsPredict replay/current-prop integration for the 1+ only
  player SOT-rate shrinkage proxy result.
- Official SportsPredict replay/current-prop integration before changing
  scorer or score-or-assist shrinkage.
- More settled SportsPredict total-count rows or archived exact current count
  anchors before replacing independent total-count convolution with
  direct-total NB.
- Official current-referee verification and independent rate research for the
  proxy-positive referee card adjustment result.
- Official SportsPredict special-market replay as more results settle.
- Better current prop-odds automation.

Do not promote any of these without the gate in `docs/full_research_program.md`.
