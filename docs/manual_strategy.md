# Manual Entry Strategy

Last updated: 2026-06-29.

This note is for the **manual** SportsPredict entry only.  It does not change bot 1
or bot 2, and none of the commands here submit predictions.

## Scheduled Run Ownership

The active scheduled workflow is the main-chat Codex heartbeat
`probability-cup-nightly-manual-predictions-in-chat`. It runs the manual-only
pipeline in this thread and covers the 09:00 check, 23:00 next-day sheet, and
half-hour pre-match polling window. The older standalone cron automations are
legacy jobs and should remain paused unless they are fully updated to this
manual workflow.

Validate this before relying on scheduled recommendations:

```bash
python3 scripts/automation_inventory_audit.py --write
python3 scripts/match_lock_readiness_audit.py --write
python3 scripts/manual_workflow_readiness_audit.py --local-date YYYY-MM-DD --write
```

`data/logs/automation_inventory_audit.md` should report
`PRIMARY_HEARTBEAT_READY`, with zero active stale or duplicate automations.

## Operating Rule

1. Build the manual probability independently.  Bot 1 and bot 2 outputs are not
   inputs to the manual recommendation.
2. Use the strongest available independent anchors first:

   - Goal/win/totals markets: exact same-question current Polymarket
     derivative markets when the question wording, threshold, period, team
     subject, and market-level volume checks pass; otherwise current public
     odds from ESPN/DraftKings and Polymarket 1X2/totals, then the
     odds-anchored goal engine.
   - Player props: exact current player-prop anchors from a supplied CSV first,
     then exact same-match Polymarket player-prop markets when player, family,
     threshold, and the stricter player volume gate pass; otherwise the repo's
     player model plus fresh lineup, injury, and prop-market research.
   - Count/referee markets: exact same-match Polymarket total/team corner
     markets when the corner type, threshold, and stricter count-volume gate
     pass; otherwise the repo's count/referee models plus fresh referee,
     injury, field, and prop-market research.
3. Use the full read-only manual pricing workflow for daily sheets:

   ```bash
   uv run --with pandas --with numpy --with scipy --with pyarrow --with pyyaml --with requests --with statsmodels \
     python scripts/full_manual_pricing.py --local-date YYYY-MM-DD
   ```

   This reads current open markets, exact current Polymarket derivative anchors
   for eligible non-player markets, exact same-match Polymarket player-prop
   anchors, exact same-match Polymarket corner-count anchors, odds anchors,
   lineup pages, count/player models, and the knockout special-market pricer.
   It logs to
   `data/logs/manual_predictions.md` by default. The direct Polymarket anchor
   layer is enabled by default with a `$1,000` individual-market volume floor
   and is logged as `current_polymarket_direct` when used. Disable it only for
   source debugging with `--no-polymarket-direct`, or change the volume floor
   with `--polymarket-direct-min-volume`. The Polymarket player-prop layer is
   separately logged as `polymarket_player_props`, defaults to the same
   `$1,000` volume floor, and can be disabled with
   `--no-polymarket-player-props` or tuned with
   `--polymarket-player-min-volume`. The corner-count direct layer is logged as
   `polymarket_count_direct`, defaults to a stricter `$5,000` volume floor, and
   can be disabled with `--no-polymarket-count-direct` or tuned with
   `--polymarket-count-min-volume`.
   Field averages are not available before the prediction lock. Do not wait for
   them, do not fabricate them, and do not pass a `--field-csv` for pre-lock
   manual entries. The field-capture tools are diagnostic only for historical
   replay or post-lock analysis:

   ```bash
   uv run --with pandas --with numpy --with scipy --with pyarrow --with pyyaml --with requests --with statsmodels \
     python scripts/live_market_risk_audit.py --all-open --write --field-template

   uv run --with pandas --with numpy --with scipy --with pyarrow --with pyyaml --with requests --with statsmodels \
     python scripts/manual_field_capture_audit.py --write

   uv run --with pandas --with numpy --with scipy --with pyarrow --with pyyaml --with requests --with statsmodels \
     python scripts/full_manual_pricing.py --local-date YYYY-MM-DD --field-csv data/field_averages_template.csv
   ```

   In normal pre-lock manual operation, no field CSV is supplied. `manual_entry`
   is the independent current-source/model value except for the validated
   no-field player-prop fallback described below, and affected count/card rows
   are carried as no-field risk rather than as missing actionable inputs.
   If current opponent-specific player-prop odds are available, put them in a
   CSV keyed by `market_id` or exact `match,question`, with either a de-vigged
   `probability` or both `yes_american,no_american`, and run:

   ```bash
   uv run --with pandas --with numpy --with scipy --with pyarrow --with pyyaml --with requests --with statsmodels \
     python scripts/full_manual_pricing.py --local-date YYYY-MM-DD --player-prop-csv path/to/player_props.csv
   ```

   A matching player-prop CSV row replaces the player model only for that exact
   market and is logged as `current_player_prop_anchor`. It outranks automatic
   Polymarket player-prop anchors because it is an explicit manual research
   input. If no CSV anchor is supplied, the script may use an exact same-match
   Polymarket player prop when player, market family, threshold, and volume all
   pass. Current prop anchors can clear stale player-rate/prop flags, but they
   do not clear lineup risk; still re-run after confirmed lineups.
   `player_prop_diagnostics.py` reports both `player_prop_anchor_status` for
   the filled manual CSV and `current_prop_anchor_status` for automatic exact
   Polymarket prop coverage. Treat these as separate gates: the CSV can remain
   incomplete while a few automatic anchors are usable, and automatic anchors
   do not excuse high-priority rows without exact current prop evidence. If no
   exact prop anchor is supplied, `full_manual_pricing.py` uses the no-field
   player fallback validated in
   `data/logs/player_no_field_policy_backtest.md`: keep raw `player_scores`
   probabilities, use the current safe-settlement global player base only for
   validated non-scorer support (`player_scores_or_assists` and 1+ SOT rows),
   and keep raw model probabilities for higher-threshold player SOT props until
   settled rows validate that extrapolation. The source-proxy diagnostic now
   shows 46 holdout rows with current `override` source, 3 with `statsbomb`,
   and 0 with `prior`, so `PRIOR_ONLY`/`TEAM_UNKNOWN` rows stay raw and are
   source-unvalidated until exact prop odds or structured player research clears
   the flag. Confirmed-bench rows and posted lineup rows where
   the player cannot be matched do not use the global no-field base; use the
   minutes-adjusted model or verify the player manually.
   The 2026-06-28 run used 80 settled player rows and a 28% base.
   Run the current-player source audit before player-heavy slates:

   ```bash
   python3 scripts/current_player_stats_audit.py --start-date 2026-06-11 --end-date YYYY-MM-DD --sports-predict --write

   uv run --with requests python scripts/current_player_stats_replay.py --start-date 2026-06-11 --end-date YYYY-MM-DD --sports-predict --write

   uv run --with pandas --with numpy --with scipy --with pyarrow --with pyyaml --with requests --with statsmodels python scripts/lineup_source_audit.py --sports-predict --all-open --write
   ```

   This writes `data/logs/current_player_stats_audit.md` from ESPN public
   summary roster stats. Treat it as a cross-check for current form and source
   coverage, not as a promoted probability transform or a substitute for exact
   prop odds or confirmed lineups.
   `current_player_stats_replay.py` is the promotion gate for that source. The
   2026-06-29 leakage-safe replay was point-positive, but its match-bootstrap
   lower bound remained negative, so current player form is a disagreement flag
   and research priority rather than an automatic player-prop override.
   `lineup_source_audit.py` writes `data/logs/lineup_source_audit.md` and
   reports `lineup_source_status`. `PENDING_NOT_IN_WINDOW` is normal before the
   final pre-match window; `LINEUP_NOT_POSTED_IN_PULL_WINDOW` and
   `LINEUP_SOURCE_ISSUES` keep player rows unlocked unless exact prop odds or
   structured role/player research supersedes them.
4. Before spending time on manual research, run the read-only research queue:

   ```bash
   uv run --with pandas --with numpy --with scipy --with pyarrow --with pyyaml --with requests --with statsmodels \
     python scripts/manual_research_queue.py --all-open --write
   ```

   This writes `data/logs/manual_research_queue.md` and ranks the rows where
   new information can change the manual entry: unresolved player props,
   recent player-role risk, weak special priors, count-threshold special rows
   with far-from-base or high-heterogeneity proxy evidence, and sparse
   penalty/red rows. Field-led historical rows are no longer treated as
   actionable pre-lock research because the field value is unavailable before
   the prediction lock. Use the queue as the nightly and near-lock worklist. Do
   not spend scarce research time on low-risk odds-anchored rows unless the
   odds audit reports source disagreement.
5. If the full model stack is unavailable, use the safe read-only fallback:

   ```bash
   python3 scripts/manual_odds_anchor.py --date YYYYMMDD --sports-predict
   ```

   This gives market-anchored goal probabilities from ESPN/DraftKings and
   Polymarket.  With a SportsPredict key present, it also reads open market
   questions via GET-only API calls.  It intentionally does not read stored bot
   predictions.
6. Never invent count/player/referee/special prices from the fallback.  If the fallback
   prints `NEEDS_RESEARCH`, run the full model stack read-only or do targeted
   research before making the manual entry.
7. The validated manual-only field policy below is historical/diagnostic unless
   SportsPredict changes the UI to expose field averages before lock. If that
   ever happens, apply it to the independent manual model probability:

   ```text
   if unresolved player-prop research flag:
       manual = field
   elif odds_anchored_goal_or_outcome_market:
       manual = manual_model
   elif count_market and stat == cards:
       manual = field
   elif count_market and stat == sot and family in {team_count_ge, team_more_count_than}:
       manual = manual_model
   elif player_prop:
       cap = 10
       if manual_model >= field:
           manual = field
       else:
           manual = field - min(cap, round(0.75 * (field - manual_model)))
   elif special_market:
       manual = manual_model
   elif manual_model >= field:
       manual = field
   else:
       manual = field - min(12, round(0.75 * (field - manual_model)))
   ```

   Round to an integer percentage and clip to 1-99.  This is a manual-entry
   policy only; it is separate from the bot strategies.
8. Under the current contest UI, the field average is not visible before
   locking the manual entry. Use the independent manual model probability unless
   a confirmed lineup/current-market/source fact creates a clear correction. Do
   not invent a field-relative adjustment without the field.

## Why This Is The Current Policy

The methodology guardrails and primary research sources are in
`docs/methodology_sources.md`. The longer no-shortcuts research program,
including active rule families and candidate methods that are not yet promoted,
is in `docs/full_research_program.md`.

The scoring rule is proper: expected RBP is maximized by minimizing Brier, so
the best forecast is the best calibrated probability, not the boldest one.
The live-history backtest therefore evaluates strategy changes by exact Brier
deltas from the official per-market RBP shown in `docs/history.md`.

V1 history rows are settled only when read-only SportsPredict `/results` gives
a unique, inferable outcome for that question text. The refreshed integrity
audit in `data/logs/backtest_integrity_audit.md` read 714 SportsPredict
`/results` rows, but still leaves 27 ambiguous repeated-question V1 rows and
3 Brier-uninferable V1 rows unsettled, so promoted policy numbers use 529 safe
exact rows rather than the old unsafe question-overwrite set.

The current operational policy validation is the combined backtest, because it
uses the same `manual_decision()` policy that `scripts/full_manual_pricing.py`
uses when UI field averages are supplied:

```text
uv run --with requests python scripts/manual_policy_backtest.py --sports-predict --write

settled rows: 529 across 57 matches
current official RBP: 1243.72
combined manual policy official RBP: 1595.09
combined manual policy delta: +351.37 RBP
combined manual policy Brier: 0.2179 vs current 0.2245
chronological holdout after 120 prior rows: +279.05 RBP on 401 rows
match-bootstrap 95% interval: +114.59 to +610.23 RBP
```

Generic model/field blending was tested and rejected. The
`manual_family_blend_backtest.md` report evaluates a predeclared
one-match-ahead category-wise linear pool of submitted model probability and
field average on the same 529 safe exact rows. It lost -145.69 RBP versus the
combined manual policy on the 401-row chronological holdout, with bootstrap
95% interval -251.41 to -42.22 and Pr(>0)=0.005. Do not replace the
family-specific manual policy with adaptive category pooling.

The catch-up/tail-risk diagnostic in `data/logs/leaderboard_tail_risk.md`
tests whether variance-seeking entries are justified by the prize thresholds.
Using the current 195-market open mix, 20,000 sampled slates, a 2x stage
multiplier, and a StatsBomb proxy for the 26 open special markets, the promoted
manual policy with raw special markets led both expected RBP and target-hit
probability. The proxy is leave-one-tournament-out empirical for timing and
player-aggregate specials, and leave-one-tournament-out negative-binomial for
shots and early-corner count thresholds. The current open special proxy
diagnostic classifies 16 of the 26 special markets as `usable_base_prior` and
9 as `weak_near_coinflip`; the weak rows are hydration-goal timing questions,
not markets with strong historical edge:

```text
combined_raw_special:   mean 801.40, Pr(>=#10 gap 728.16)=66.05%, Pr(>=#1 gap 871.09)=36.70%
combined_manual_policy: mean 801.10, Pr(>=#10 gap 728.16)=66.02%, Pr(>=#1 gap 871.09)=36.73%
combined_penalty_red_field: mean 798.16, Pr(>=#10 gap 728.16)=65.44%, Pr(>=#1 gap 871.09)=36.10%
combined_player_field:  mean 778.95, Pr(>=#10 gap 728.16)=62.56%, Pr(>=#1 gap 871.09)=31.13%
combined_count_field:   mean 772.59, Pr(>=#10 gap 728.16)=60.99%, Pr(>=#1 gap 871.09)=30.64%
combined_goal_field:    mean 745.00, Pr(>=#10 gap 728.16)=55.27%, Pr(>=#1 gap 871.09)=25.12%
current:                mean 605.26, Pr(>=#10 gap 728.16)=31.16%, Pr(>=#1 gap 871.09)=13.39%
hard 99/1 strategies:   negative mean and 0.00% target-hit rate in this replay
```

This does not prove the future slate will pay that much; the special-market
piece is an empirical StatsBomb proxy, not official SportsPredict special
history. It does show that the useful catch-up lever is not blanket
extremization. It is a narrow promotion: keep raw special-market priors instead
of shrinking them to the field with the generic global rule.

The official special-outcome audit in
`data/logs/special_official_results_audit.md` now checks read-only
SportsPredict `/results` for settled special/timing rows. The 2026-06-28
refresh read 714 result rows and found 5 settled special rows; all 5 parsed as
special and had usable inferred outcomes, but each covered a different family.
That is useful coverage evidence, not enough sample to validate or reject
`raw_special_model`. Until the official special sample is large enough for
stable family-level conclusions, keep the StatsBomb special proxy as the active
evidence source and treat these markets as lower-confidence than ordinary
odds-anchored goal markets.

Automatic shrinkage of special priors toward 50% was tested and not promoted
in `data/logs/special_prior_shrinkage_backtest.md`. On 4,082
leave-one-tournament-out proxy rows, the raw prior was the best all-special
point estimate. The weak near-coinflip timing families had a mild-shrink point
estimate of +21.79 RBP versus raw on 628 rows, but the tournament bootstrap
95% interval crossed zero (-51.13 to +117.79, Pr(>0)=0.666). Treat those
weak-prior rows as current-source recheck warnings, not as an automatic
probability transform.

The operational policy usage in that backtest:

```text
global_asym075_cap12: 176 rows
raw_odds_goal_model: 169 rows
player_asym075_cap10: 80 rows
strong_sot_count_model: 65 rows
card_count_field: 39 rows
```

The broader field-relative diagnostics still explain why the policy is
asymmetric:

```text
Deviation <=3pt from field:  -12.82 skill RBP
Deviation 3-8pt:             -17.76
Deviation 8-15pt:           +197.35
Deviation >15pt:            -180.54

Below field:                +110.37
Above field:                -124.14
```

Interpretation:

- The edge is real mostly when the model is **moderately below** the field on
  over-loved event props.
- Very large deviations have been negative and should be treated as a red flag.
- Family-by-family tuning overfits the current history sample.
- The best operational rule is the family-specific policy above, not a blanket
  global transform. Its strongest validated pieces are player under-field
  discipline, card-count field-following, raw goal-market odds anchors, and
  selective trust in strong SOT count markets.
- Bootstrap uncertainty remains wide, so this is a disciplined edge, not a
  guarantee.
- Player props have a separate validated cap from
  `data/logs/player_policy_backtest.md`: on 80 safe settled player rows,
  `asym075_cap10` gained +166.79 RBP in-sample, +171.86 after 30 prior settled
  player rows, and had a match-bootstrap 95% interval of +15.71 to +340.15 RBP.
  The direct `asym075_cap10` versus pure-field comparison is weaker:
  +39.59 RBP in-sample, -4.34 in chronological holdout, and a bootstrap interval
  of -70.90 to +146.50 with Pr(>0)=0.751. The current prize-threshold replay
  still rejects switching all player rows to field (`combined_player_field`
  trails the promoted policy on mean and both target-hit rates), so keep the
  researched-player cap but monitor this as more rows settle.
  Do not use the field cap for pre-lock researched player props because field
  values are not available before prediction lock. For unresolved `PRIOR_ONLY`,
  `TEAM_UNKNOWN`, stale-minutes, or stale-prop flags, require exact prop,
  lineup, or structured player research.
- When the field is not visible and no exact current prop-market CSV anchor is
  available, use the no-field player fallback from
  `data/logs/player_no_field_policy_backtest.md` rather than raw non-scorer
  player priors only inside the validated support. On the 49-row chronological
  holdout after 30 prior settled player rows, `raw_scores_else_global_base` beat
  raw player model values by +305.36 RBP, Brier 0.2063 vs 0.2686, with
  match-bootstrap 95% interval +137.18 to +478.82 RBP. The 2026-06-28
  sensitivity grid varied the chronological cutoff from 10 to 50 prior rows and
  the global beta strength from 0 to 20; all 25 configurations remained
  positive versus raw, with worst tested delta +130.42 RBP. The holdout
  contains scorer, score/assist, and 1+ SOT rows, but no 2+/3+ SOT threshold
  rows; higher player-SOT thresholds keep the raw model unless an exact prop
  anchor is available. This fallback is subordinate to current prop odds,
  confirmed lineup information, and player-source
  evidence. The current source-proxy split is 46 `override`, 3 `statsbomb`, and
  0 `prior` holdout rows, so current `PRIOR_ONLY`/`TEAM_UNKNOWN` rows are
  source-unvalidated and stay raw until exact prop odds or structured player
  research clears the source flag.
  Confirmed-bench rows keep the reduced minutes-adjusted model, and
  posted-lineup unmatched rows require manual verification instead of the global
  base.
- The player boundary is isolated in `data/logs/player_prop_boundary_audit.md`.
  The latest read-only SportsPredict-settled run confirms the no-field base is
  strong inside support (+305.36 RBP, lower bound +137.49), but there are 0
  higher-threshold SOT rows in that holdout. It also shows the researched-player
  asymmetric policy does not beat field directly with a positive lower bound
  (`asym075_minus_field` lower bound -78.94), while above-field player model
  rows were historically dangerous (field-minus-raw +76.02 RBP, lower bound
  +25.60). The current board has 23 player props, 3 source-unvalidated rows,
  7 high-threshold SOT rows, and 1 exact current prop anchor. Therefore use
  exact prop odds or structured player research for
  unresolved/source-poor/high-threshold rows; use the no-field base only for
  tested support.
- Historical lineup-status reconstruction is now logged in
  `data/logs/player_lineup_status_backtest.md`: ESPN rosters classified all 80
  settled player rows with zero missing match contexts. Bench rows had negative
  submitted-model skill versus field (-2.07 RBP-equivalent), but the
  player-specific `asym075_cap10` policy still beat using the field on bench
  rows (+26.22 vs +2.07 delta). Treat confirmed bench/not-listed status as a
  mandatory research flag and a reason not to go model-above-field; do not
  promote a new starter-only policy from this small split.
- Recent role history is audited in
  `data/logs/player_role_research_audit.md` and tested in
  `data/logs/player_role_policy_backtest.md`. The 2026-06-28 role audit flags
  current bench/rotation risk before final lineups, but the policy replay does
  not promote a new role-history probability rule: using field only for
  `bench_or_rotation_risk` rows beat `player_asym075_cap10` by +17.95 RBP on
  the 80-row sample and +17.95 after 30 prior rows, but match-bootstrap
  uncertainty crossed zero (-26.35 to +68.31, Pr(>0)=0.770). Treat recent role
  history as a research warning; final lineup or exact prop odds remain stronger
  pre-lock evidence.
- Count markets have a separate validated operating rule from
  `data/logs/count_policy_backtest.md`: on 256 safe settled count rows,
  `strong_sot_current_cards_field_else_global` gained +186.56 RBP in-sample,
  +117.70 after 60 prior settled count rows, and had a match-bootstrap 95%
  interval of +37.48 to +345.29 RBP. The direct comparison against pure field
  is +152.17 RBP in-sample and +76.24 in chronological holdout, but its
  bootstrap interval crosses zero (-33.48 to +328.38, Pr(>0)=0.956). The
  current prize-threshold replay still rejects all-count-field
  (`combined_count_field` trails the promoted policy on mean and both target
  rates). Use the independent model for `team_count_ge/sot` and
  `team_more_count_than/sot`; for card and other count rows, keep the
  no-field/current-source value and carry `FIELD_UNAVAILABLE_PRELOCK` risk.
- Current referee availability is audited in
  `data/logs/referee_research_audit.md`. The 2026-06-28 run found ESPN summary
  payloads for all 8 currently open matches with card-like markets but no
  official/referee fields, 16 card-like markets, and no local referee overrides
  for those matches. Therefore no new referee/card adjustment is promoted. Keep
  no-field card rows on the neutral card model unless a verified referee/current
  source justifies movement.
- The card branch is isolated in `data/logs/card_field_component_audit.md`.
  The latest read-only SportsPredict-settled run has 39 settled card rows,
  22 settled card-threshold rows, 16 current open card-like markets, and 11
  current card-count rows where field would matter if visible. Visible field beat
  the raw card model by +157.01 RBP on all card rows, +74.12 in chronological
  holdout, and a +26.64 match-bootstrap lower bound. On card-threshold rows
  only, field beat raw by +127.96 RBP with lower bound +20.47. But the
  incremental field-for-cards component inside the full count policy still has
  lower bound -47.85, and the no-field card-threshold fallback still has lower
  bound -69.82. Therefore this is a low-confidence pre-lock warning, not a new
  transform: because field averages are unavailable pre-lock, keep
  neutral/raw card values unless verified referee/current-source context clears
  the row.
- Count no-field fallback was tested separately in
  `data/logs/count_no_field_policy_backtest.md`. On 193 chronological holdout
  rows after 60 prior settled count rows, the best no-field challenger
  (`card_threshold_stat_base_else_raw`) gained +88.69 RBP versus raw count
  values and Brier 0.2263 vs 0.2309. This predeclared split excludes card
  head-to-head/allocation rows and only changes card threshold rows, but
  match-bootstrap uncertainty is still too wide (-65.83 to +245.94 RBP,
  Pr(>0)=0.877). The 2026-06-28 sensitivity grid is positive in 25/25 cutoff
  and smoothing configurations, with worst tested delta +60.63 RBP; this makes
  the card-threshold signal worth monitoring, but it still does not clear the
  promotion gate. Do not promote a no-field count fallback from that evidence.
  Because field values are unavailable before prediction lock, keep raw count
  model values unless a direct current market/referee fact creates a clear
  correction. Use the validated count field policy only for historical replay,
  post-lock diagnostics, or a future UI behavior change that exposes field
  averages before lock.
- Count mean multipliers have a separate reconstructed replay in
  `data/logs/count_intensity_replay.md`: on 256 safe settled count rows,
  current v4 beats the old uniform 0.90 map (+170.95 vs +12.44 RBP against the
  submitted probabilities). The aggressive challenger wins the raw
  reconstructed replay (+207.68), and a narrower `sot105_only` challenger also
  improves the point estimate (+194.91), but these replays are directional
  because old Polymarket blends, referee overrides, and intraday odds snapshots
  are not reconstructed.
- The deployable field-policy version of that replay is now logged in
  `data/logs/count_intensity_policy_replay.md`: after applying the live
  `manual_decision()` policy to each candidate's reconstructed model
  probability and the historical field average, `sot105_only` led the point
  estimate by +15.21 RBP versus `current_v4`, ahead of `aggressive_bot2`
  (+8.22). Its match-bootstrap 2.5% bound was still negative (-13.87), so the
  promotion gate keeps `current_v4`. Do not change the live SOT intensity from
  1.00 to 1.05 until the lower bound turns positive or newer settled rows
  confirm the effect.
- Total-count dependence has a separate proxy replay in
  `data/logs/count_total_dependence_audit.md`. The latest Football-Data
  club-history audit scores 535,122 chronological total-count threshold rows
  and finds `direct_total_nb` positive versus the current independent-team NB
  convolution, including a positive match-block lower bound. The stricter
  settled Cup replay in `data/logs/count_total_dependence_policy_replay.md`
  did not promote the candidate: 32 total-count rows, manual-policy delta
  -0.24 RBP versus current, and a negative bootstrap lower bound. It must not
  override exact current count anchors, the promoted count policy when its live
  inputs exist, or the current independent total-count shape.
- Odds-anchored goal/outcome markets have a separate validation from
  `data/logs/goal_policy_backtest.md`: on 169 safe settled goal rows, the raw
  current model beat every tested field-relative policy. The field transform
  lost -120.52 RBP in-sample and -91.49 RBP after 60 prior settled goal rows.
  Direct raw-minus-field comparison was +120.52 RBP in full sample, +91.49 in
  chronological holdout, and -6.69 to +243.03 in match-bootstrap
  (Pr(>0)=0.967). The 2026-06-28 cutoff-sensitivity grid is deliberately not
  used as a tuning search; it varies the chronological cutoff over 30, 45, 60,
  75, and 90 prior goal rows. Raw odds values beat field in 5/5 configs, beat
  the best 75% shrink candidate in 5/5 configs, and were the top fixed policy
  in 4/5 configs; the worst raw-minus-field holdout edge was still +72.64 RBP.
  The current prize-threshold replay also rejects
  all-goal-field (`combined_goal_field` trails the promoted policy on mean and
  both target rates).
  Use exact current Polymarket same-question anchors first when the direct
  wording/threshold/period/team/volume gate passes; otherwise use the raw
  odds-anchored model for team win, draw, goal totals, BTTS, halftime goal
  state, and related scoreline markets. The direct-anchor layer is a current
  source-quality hierarchy rule, not a historical promotion. We now archive
  matched derivative-market snapshots and score them against official outcomes
  after settlement; the latest score report has 4 scored latest rows and is
  negative for the anchors (`-0.0289` Brier delta, 1 anchor win). The sample is
  still below the 30-market historical-promotion gate, so exact anchors remain
  a current-source hierarchy rule only and are surfaced in live coverage as
  `CURRENT_ANCHOR_RECHECK`, not as a promoted historical edge. The regression
  tests deliberately guard against false edge from matching a match total as a
  team total.
- Penalty/red markets have a separate sparse-sample guardrail in
  `data/logs/penalty_red_policy_backtest.md`: on 24 safe settled rows, the
  global asymmetric policy is +0.82 RBP versus current, while pure field is
  -5.28. The direct global-minus-field comparison is +6.10 RBP in-sample, -0.19
  in chronological holdout, and -1.84 to +14.26 in match-bootstrap. A cutoff
  sensitivity grid is positive in 6/6 chronological cutoffs, but the global
  policy is the top policy in only 1/6 and the bootstrap lower bound remains
  negative. The current prize-threshold replay also keeps the global policy over
  all-penalty/red-field (`combined_penalty_red_field` trails the promoted
  policy on mean and both target rates). Treat this as a small-sample guardrail,
  not a referee-specific edge.

## Pre-Match Checklist

- After refreshing API-backed evidence reports, run the consistency gate before
  relying on the strategy matrix:

  ```bash
  python3 scripts/evidence_freshness_audit.py --write
  ```

  Latest report: `data/logs/evidence_freshness_audit.md`. The current audit
  shows 714 SportsPredict result rows, 529 safe exact strategy rows, 28 rows
  blocked by unsafe V1 question-only joins, 120 known open markets, and 34/34
  strict freshness checks passing. The gate now verifies exact-history sample
  sizes, `/results` snapshot alignment, open-board count consistency, execution
  coverage-state consistency, calibration-watchlist coverage of the current
  all-open board, exact Polymarket anchor-count reconciliation, live
  family/category reconciliation for odds-goal, player-role, player-prop,
  player-boundary, special, special-boundary, and referee/card reports,
  card-field component reconciliation against the current open card worklist, that
  `strategy_evidence_matrix.md` is newer than the timestamped reports it
  summarizes, and that `promotion_strength_audit.md` is newer than the strategy
  matrix it interprets. If any artifact is
  `STALE_OR_INCONSISTENT`, rerun that report before using the matrix to make a
  strategy decision.

- When evaluating strategy claims, run the promotion-strength audit:

  ```bash
  python3 scripts/promotion_strength_audit.py --write
  ```

  Latest report: `data/logs/promotion_strength_audit.md`. The current run
  audits 8 major strategy areas and flags 6 warning rows. Combined manual
  policy is a strong promotion. Raw odds-goal rows are still the right
  operational default, but only a soft promotion because the raw-vs-field
  bootstrap lower bound is slightly negative. Power de-vig is not promoted
  because the 1X2 historical signal is strong but total-goals odds do not clear
  the bootstrap lower-bound gate. Count field policy is overall strong but has
  a component warning on the incremental card-field piece; the
  dedicated card audit decision is
  `field_visible_required_no_referee_override_no_new_transform`.
  Researched player props are limited and overall-positive, but unresolved
  pre-lock rows remain exact-prop/lineup led because field is unavailable. The
  dedicated boundary audit decision is
  `field_or_exact_prop_required_for_unresolved_players_limited_no_field_base`,
  kept as a historical label. Exact Polymarket anchors stay current-source only, and
  special markets stay proxy-only with boundary decision
  `raw_proxy_with_guardrails_no_shrink_no_official_promotion`. This report is
  classification-only and does not change probabilities.

- Refresh the target audit when new knockout markets open:

  ```bash
  uv run --with requests python scripts/tournament_target_audit.py --write
  ```

  This uses only read-only SportsPredict open markets plus the manual leaderboard
  score/target inputs. As of `2026-06-28T23:10Z`, the known open board had 120
  markets across 8 matches. From a manual score of 1243.59, the arithmetic gap
  was 728.16 RBP to #10 and 871.09 RBP to #1, requiring 6.07 and 7.26 RBP per
  currently open market respectively if no later markets are counted. The
  current mix includes 20 special markets after classifying total shots and
  corners before hydration with the special pricer rather than ordinary count
  buckets. The target audit keeps those special markets at zero in the
  historical category mix until official settled SportsPredict special rows
  exist, and shows the StatsBomb special-proxy tail-risk table separately.
  That target gap is much larger than the historical per-row policy lift, so
  the correct response is disciplined calibration plus lineup, prop, referee,
  and source execution, not artificial extremizing.
- Do not add a global post-calibration multiplier to the combined manual
  policy. The calibration audit in
  `data/logs/manual_calibration_backtest.md` tested factors from 0.70 to 1.20
  around 50%. Full sample slightly preferred 1.10, but the chronological
  walk-forward selector lost -21.80 RBP versus the uncalibrated combined policy
  on 401 holdout rows, with match-bootstrap interval -62.98 to +17.26 and
  Pr(>0)=0.126. The refreshed reliability table also rejects the layer:
  `combined_manual_policy` has full-sample Brier 0.2179 and 7-bin ECE 0.041,
  while the walk-forward calibrated holdout worsens ECE from 0.054 to 0.063
  and Brier from 0.2193 to 0.2198. This is a rejected overfit, not a
  promotion. Keep monitoring the 35-45% and 80%+ combined-policy bins as more
  rows settle, because those are the visible calibration warning areas.
- For reliability/resolution diagnostics, run:

  ```bash
  uv run --with requests \
    python scripts/brier_decomposition_audit.py --sports-predict --write
  ```

  This writes `data/logs/brier_decomposition_audit.md`. The latest run uses
  the same 529 safe exact rows as the manual-policy backtest. It shows the
  combined manual policy's lower Brier is primarily a resolution gain
  (`0.0303` vs current `0.0239`) while reliability remains low (`0.0027`).
  Treat high bucket/category reliability errors as field, source, lineup, or
  wording recheck priorities only; the report does not authorize a calibration
  multiplier or bucket-level transform.
- After each evidence refresh, write the all-open calibration watchlist:

  ```bash
  uv run --with pandas --with numpy --with scipy --with pyarrow --with pyyaml --with requests --with statsmodels \
    python scripts/manual_calibration_watchlist.py --all-open --write
  ```

  This prices the current all-open board through the read-only manual workflow
  and compares entries to `data/logs/manual_calibration_backtest.md`, then
  writes `data/logs/manual_calibration_watchlist.md`. Treat watchlist rows as
  source and execution warnings only. They mean recheck exact-market wording,
  source liquidity, field averages, lineup facts, or special-prior confidence
  before lock. They do not change the submitted probability unless that recheck
  finds a real current fact. The refreshed 2026-06-29 all-open run selected
  120 markets and flagged 19 high-severity and 10 medium-severity rows. The
  high rows are concentrated in the historically overestimated 35-45% and 80%+
  buckets; the medium rows are in the historically underestimated 65-80% bucket.
- Re-run the safe read-only command for the relevant ESPN date.
- For exact SportsPredict questions, prefer the full v4 stack when `data/` and
  a read-only key are available.
- Before trusting ordinary goal/outcome anchors, run the read-only odds source
  audit:

  ```bash
  uv run --with requests --with scipy python scripts/odds_anchor_audit.py --write
  ```

  Latest report: `data/logs/odds_anchor_audit.md`. The 2026-06-29 audit checks
  ESPN/DraftKings against Polymarket 1X2 markets from both the generic soccer
  tag and the `series_slug=soccer-fifwc` World Cup series. The latest run found
  7 of 8 open matches `ALIGNED`; AUS/EGY is `LOW_VOLUME_PM` because Polymarket
  volume is thin, but the equal-blend sensitivity is only 0.003. The largest
  current 1X2 book/Polymarket blend shift is 0.010, so these are
  recheck-near-lock issues rather than model-change signals. If a future run
  reports `BOOK_ONLY`, `LOW_VOLUME_PM`, `MEDIUM_DISAGREEMENT`, or
  `HIGH_DISAGREEMENT`, inspect the source row's `blend_shift`: small shifts can
  be monitored, while larger shifts need targeted odds-source research before
  entering those goal/outcome markets.
- Do not change the bookmaker overround conversion from proportional de-margin
  to power/Shin/favorite-longshot de-vig yet. The repeatable odds-conversion
  audit is:

  ```bash
  uv run --with numpy python scripts/odds_conversion_backtest.py --write
  ```

  Latest report: `data/logs/odds_conversion_backtest.md`. The current
  Football-Data run covers 115,809 1X2 closing-odds rows and 47,344
  over/under 2.5 rows. It now replays multiplicative, additive, power, Shin,
  odds-ratio, OO-EPC, and FL-GLM walk-forward conversions. Power has the best
  full-sample 1X2 point estimate, OO-EPC has the best recent 1X2 and totals
  point estimates, and FL-GLM is trained only on past calendar years. The 1X2
  candidate bootstrap lower bounds are positive, but totals still have negative
  lower bounds despite positive point estimates. Because the live score grid
  uses both 1X2 and totals, the decision is
  `not_promoted_keep_multiplicative_devig`.
- For total/team-goal rows specifically, run the direct-total source audit:

  ```bash
  python3 scripts/odds_total_source_audit.py --write
  ```

  Latest report: `data/logs/odds_total_source_audit.md`. The 2026-06-29
  refresh found 9 current total/team-goal rows: 7 have exact same-question
  direct total anchors, 2 do not, 3 exact direct anchors are thin but aligned,
  0 have large model/direct shifts, and 0 have entry/direct-anchor mismatches.
  This is a source-quality guardrail only. It does not change probabilities;
  it tells you which total rows need near-lock odds refreshes, wording checks,
  or direct-market liquidity review.
- Before manual entry, run the all-open live execution-risk audit:

  ```bash
  uv run --with pandas --with numpy --with scipy --with pyarrow --with pyyaml --with requests --with statsmodels \
    python scripts/live_market_risk_audit.py --all-open --write --field-template
  ```

  Latest report: `data/logs/live_market_risk_audit.md`; latest field template:
  `data/field_averages_template.csv`. The `2026-06-29T10:04Z` all-open audit
  found 120 currently exposed markets: 19 high-risk, 55 medium-risk, and 46
  low-risk. Exact current Polymarket direct anchors use separate volume gates:
  `$1,000` for non-player goal/player props and `$5,000` for corner-count
  anchors. The current direct-anchor coverage audit is
  `data/logs/polymarket_direct_coverage_audit.md`; replay-ready matched rows
  are archived in `data/logs/polymarket_direct_anchor_snapshots.csv` via:

  ```bash
  uv run --with pandas --with numpy --with scipy --with pyarrow --with pyyaml --with requests --with statsmodels \
    python scripts/polymarket_direct_coverage_audit.py --all-open --write --snapshot-csv
  ```

  The latest future-only snapshot captured 17 exact-anchor rows: 14
  non-player, 2 player props, and 1 corner-count anchor. Score the archived
  exact-market rows after official results settle with:

  ```bash
  uv run --with requests \
    python scripts/polymarket_direct_snapshot_score.py --sports-predict --write
  ```

  The latest score report is
  `data/logs/polymarket_direct_snapshot_score.md`: 99 semantically valid
  archived snapshot rows across 21 unique markets, 4 scored latest markets, 17
  unscored latest markets, and latest-per-market Brier delta `-0.0289` with 1
  anchor win. One older archived first-goal snapshot is excluded because it
  matched a "neither team to score first" market, not the target team's
  first-goal market. The scored sample now covers RSA/CAN BTTS, Canada HT lead, 9+ total corners, and
  Cyle Larin to score; it is still far too small and currently negative. The
  historical-promotion gate is therefore `not_promoted_insufficient_sample`
  with a 30 scored latest-market minimum. Do not promote this branch beyond
  `CURRENT_SOURCE_RULE` until enough independent latest-per-market rows settle
  and score positively.
  The live guardrail report is
  `data/logs/polymarket_direct_anchor_risk_audit.md`, generated with:

  ```bash
  python3 scripts/polymarket_direct_anchor_risk_audit.py --write
  ```

  The latest run covers all 17 exact anchors from the coverage audit and makes
  no probability transform. It flags 3 high-risk large shifts while the replay
  sample remains small and negative: Matheus Cunha to score moved 42% to 30%,
  Germany to lead at halftime moved 49% to 55%, and Erling Haaland to score
  moved 65% to 56%. Those rows need wording, lineup, and/or alternate-source
  review before lock; the other 14 current
  anchors are medium risk only because this current-source layer is not yet
  historically promoted. The manual evidence coverage and execution leverage
  reports propagate this direct-anchor risk onto the affected market rows, so
  the daily checklist shows both the normal field-unavailable/player gate and the
  large-shift direct-anchor recheck.
  The high-risk set is concentrated in unresolved player props and card-count
  markets where the historical best branch used field, but the current UI does
  not expose field values before lock.
  Recent player-role evidence is joined by exact SportsPredict `market_id` from
  `data/logs/player_role_research_audit.md`: the latest `2026-06-29T01:10Z`
  refresh found 23 open player rows and marks rotation/bench history as
  execution risk. The risk audit and research queue surface those rows, but the
  role audit is a guardrail only; it does not write player overrides or change
  probabilities without final XI, exact prop odds, or structured role evidence.
  Treat these as execution priorities, not as reasons to extremize. The
  default `--field-template` file is keyed by exact SportsPredict `market_id`
  and is diagnostic only. Field values are not available before prediction
  lock, so `manual_field_capture_audit.md` normally remains `INCOMPLETE` and
  should not block a pre-lock manual packet. Keep field-led historical branches
  as `FIELD_UNAVAILABLE_PRELOCK` low-confidence no-field rows.
- Before trusting a daily sheet as complete, run the all-open evidence coverage
  audit:

  ```bash
  uv run --with pandas --with numpy --with scipy --with pyarrow --with pyyaml --with requests --with statsmodels \
    python scripts/manual_evidence_coverage.py --all-open --write
  ```

  Latest report: `data/logs/manual_evidence_coverage.md`. The 2026-06-29
  02:16Z all-open run mapped 120 future open markets, excluding already-started
  RSA/CAN rows, to evidence branches: 56 `PROMOTED`, 22 `PROMOTED_LIMITED`,
  16 `CURRENT_SOURCE_RULE`, 20 `PROXY_ONLY`, and 6 `GUARDRAIL_ONLY`. By live
  gate, 32 rows were fully `COVERED`; 14 were
  `CURRENT_ANCHOR_RECHECK` exact anchors, 25 were field-unavailable pre-lock
  rows, 16 were proxy-only special rows, 3 were special count-threshold rechecks, 1 was a
  special count-threshold research requirement, 15 needed player recheck, 8
  needed player research, and 6 were penalty/red guardrails. Use this as the
  row-level proof that each manual entry is backed by the intended evidence
  branch, not as a probability transform. The `direct_anchor_risk` column is
  joined from `polymarket_direct_anchor_risk_audit.md`, so high-risk large
  exact-anchor shifts remain visible even when the primary coverage state is
  `FIELD_UNAVAILABLE_PRELOCK` or `PLAYER_RECHECK`.
- After evidence coverage, run the match-level lock audit:

  ```bash
  python3 scripts/match_lock_readiness_audit.py --write
  ```

  Latest report: `data/logs/match_lock_readiness_audit.md`. It groups the
  row-level coverage gates by match and reports `match_lock_decision`. A match
  with `LOCK_BLOCKED_BY_REQUIRED_INPUTS` still has hard player or
  special-count source work outstanding. `LOCK_RECHECK_REQUIRED` still needs
  near-lock current-source, player, or special-count rechecks. Treat only
  `LOCK_READY` as ready for final manual entry without additional research.
- After evidence coverage, run the packet audit:

  ```bash
  python3 scripts/manual_submission_packet_audit.py --write
  ```

  Latest report: `data/logs/manual_submission_packet_audit.md`. It reports
  both `submission_packet_status` and actionability buckets. `NOW_*` buckets
  are the nightly/morning research queue: current player prop/role work and
  current count-source work. `NEAR_LOCK_*` buckets are the pre-match recheck
  queue: exact anchors, final lineups, prop markets, and special count-source
  checks. `LOW_CONFIDENCE_*` buckets are provisional proxy/guardrail entries,
  and `NO_FIELD_ACTION_PRELOCK` rows are unavoidable because field averages are
  unavailable before prediction lock.
  When a targeted source pass has been completed but no usable exact source was
  found, record the negative result in `data/manual_research_resolutions.csv`.
  Those rows become `NEAR_LOCK_RESEARCHED_SOURCE_RECHECK`, not ready rows: keep
  the provisional model value and recheck lineups/current sources before lock.
- After evidence coverage, run the execution leverage audit:

  ```bash
  python3 scripts/manual_execution_leverage.py --write
  ```

  Latest report: `data/logs/manual_execution_leverage.md`. The 2026-06-29
  02:16Z run ranked the same 120 future open markets by historical RBP-per-row
  context from `manual_policy_backtest.md`: 20 `HIGH`, 32 `MEDIUM`, 36
  `MONITOR`, and 32 `LOW`. The high group is exactly where manual effort
  should go first: 11 card-field rows at roughly +4.03 historical RBP per
  settled row, 8 hard player-prop research rows at roughly +2.07, and the
  AUS/EGY 20+ total-shots special count-threshold row as a proxy evidence
  research requirement. This is a work-ranking report, not an expected-value
  forecast and not a probability transform.
- For goal-heavy days, re-run the read-only goal policy diagnostic when new
  results have settled:

  ```bash
  uv run --with requests python scripts/goal_policy_backtest.py --sports-predict --write
  ```

  Use exact current Polymarket same-question anchors for eligible non-player
  goal/win/totals markets when the direct gate passes; otherwise use raw
  odds-anchored model probabilities unless the market is a timing/special prop.
  Timing/special props use their own StatsBomb special-market evidence. If a
  direct anchor sharply contradicts the score-grid model, inspect the
  Polymarket wording and team/period/threshold match before entry rather than
  blindly taking either value.
- For days with player props, run the read-only player diagnostic:

  ```bash
  uv run --with pandas --with numpy --with scipy --with pyarrow --with pyyaml --with requests --with statsmodels \
    python scripts/player_prop_diagnostics.py --sports-predict --all-open --write --anchor-template

  python3 scripts/current_player_stats_audit.py --start-date 2026-06-11 --end-date YYYY-MM-DD --sports-predict --write

  uv run --with requests python scripts/current_player_stats_replay.py --start-date 2026-06-11 --end-date YYYY-MM-DD --sports-predict --write

  uv run --with pandas --with numpy --with scipy --with pyarrow --with pyyaml --with requests --with statsmodels python scripts/lineup_source_audit.py --sports-predict --all-open --write
  ```

  Treat `PRIOR_ONLY`, `TEAM_UNKNOWN`, `GLOBAL_MINUTES_UNCONFIRMED`, and
  `SCORER_PROP_RECHECK` as mandatory research flags before manual entry.
  Treat `current_player_stats_replay.py` as the explicit promotion gate for
  ESPN current-tournament player stats; unless it returns
  `PROMOTION_CANDIDATE_REVIEW`, use those stats only to prioritize player
  research and disagreements.
  The `--anchor-template` option writes
  `data/player_prop_anchors_template.csv`; fill exact current prop odds there
  with either a de-vigged `probability` or both `yes_american,no_american`, then
  rerun the diagnostic with
  `--all-open --player-prop-csv data/player_prop_anchors_template.csv`. The
  report's `player_prop_anchor_status` is the manual-CSV gate, while
  `current_prop_anchor_status` records automatic exact Polymarket prop anchors
  already found by the live diagnostic. `player_prop_anchor_status:
  INCOMPLETE` means high-priority player rows still need exact manual CSV prop
  odds or lineup/role confirmation before lock.
  `current_prop_anchor_status: PARTIAL_HIGH_PRIORITY_MISSING` means automatic
  exact anchors exist but high-priority player rows remain unanchored. After
  the manual CSV audit is acceptable for the slate, pass the CSV to
  `full_manual_pricing.py` or
  `live_market_risk_audit.py` with `--player-prop-csv`. The
  default selection excludes matches that have already kicked off, with a
  10-minute grace window; add `--include-started` only for post-hoc audits.
  Use `--local-date YYYY-MM-DD` for a single local slate or `--match` for an
  exact match. The current report shows 23 selected future player rows, 7
  high-priority research rows, and 16 medium rows. The current open-player
  table now shows `raw_model`, `current_anchor`, `model`, and `manual`:
  `raw_model` is the player-model probability, `current_anchor` is an exact
  same-match Polymarket player-prop anchor when one clears the volume gate,
  `model` is the probability that will feed the manual policy, and `manual`
  applies the same no-field player fallback as the daily sheet. Use `manual`
  for no-field execution unless exact prop odds supersede it. Exact Polymarket
  player anchors clear stale source/minutes
  flags but still leave lineup recheck risk until lineups post. High priority
  disagreement between manual/model values and `current_player_stats_audit.md`
  is a research flag, not an automatic override. `lineup_source_status:
  PENDING_NOT_IN_WINDOW` keeps scheduled rechecks alive; posted lineups with
  unmatched players require manual verification before trusting default
  minutes. `HIGH` player priority means the row has an unresolved
  player-policy flag such as `PRIOR_ONLY`, `TEAM_UNKNOWN`,
  `GLOBAL_MINUTES_UNCONFIRMED`, `SCORER_PROP_RECHECK`, or confirmed lineup
  trouble. Use this exact-market CSV for opponent-specific player scorer/SOT
  anchors instead of writing stale global `scorer_prop_prob` values into
  `data/player_overrides.yaml`. The report now also includes a Polymarket
  player-prop candidate audit: it counts same-player markets, lists every
  exact-prop candidate that
  is eligible or below the `$1,000` volume floor, and shows only a bounded
  sample of wrong-family/wrong-threshold mismatches. The current all-open audit
  found 252 same-player candidates, but only 1 eligible exact anchor and 14
  exact-prop below-volume near misses, so do not lower the volume gate or copy
  a thin Polymarket prop directly into the manual entry without separate
  current prop research.
  The manual pricer now disables volatile global player values: past-match
  `expected_minutes` and opponent-specific
  `scorer_prop_prob` values remain as recheck flags, but do not feed the
  manual model unless a current pulled lineup or exact current prop anchor
  supplies them. The 2026-06-28 refresh changed Folarin Balogun's no-field raw
  scorer row from the stale 10%/20-minute prior-match setting to a 47% model
  with `GLOBAL_MINUTES_UNCONFIRMED,SCORER_PROP_RECHECK`, meaning it requires
  current prop odds or confirmed lineup before lock.
  To clear unsupported global player priors with structured season-rate
  evidence, run the manual-only player prior research pass:

  ```bash
  uv run --with pandas --with numpy --with scipy --with pyarrow --with pyyaml --with requests --with statsmodels \
    python scripts/player_prior_research.py --write
  ```

  The 2026-06-28 pass accepted Ermedin Demirović and Nestory Irankunda
  API-Football overrides and rejected Alexander Sørloth, Amad Diallo, and
  Martin Ødegaard because the API did not return acceptable identity/stat
  matches. Accepted season-rate overrides clear `PRIOR_ONLY`; they do not clear
  lineup risk or replace exact current prop odds.
  If that CSV is still blank and the field is not visible, the daily sheet's
  `player_no_field_policy` line should show the current global fallback base;
  use that `manual_entry` for validated non-scorer player props rather than the
  raw `model_probability`. `PRIOR_ONLY`/`TEAM_UNKNOWN` rows now stay raw under
  `research_required_no_field_source_unvalidated`; use exact prop odds or
  structured player research before treating them as validated edges. Confirmed
  bench and posted-lineup unmatched flags block the global base.
  Higher-threshold player SOT rows now show
  `player_no_field_raw_model_unvalidated_threshold` or
  `player_no_field_raw_model_unvalidated_threshold_and_source` and should stay
  raw unless an exact prop anchor is available.
  Record accepted and rejected player-rate evidence in
  `data/logs/player_research.md`.
- For player-prop policy boundaries, run:

  ```bash
  uv run --with pandas --with numpy --with scipy --with pyarrow --with pyyaml --with requests --with statsmodels \
    python scripts/player_prop_boundary_audit.py --sports-predict --write
  ```

  Treat this as a guardrail: it does not create new probabilities, but it tells
  whether current open player rows are inside the tested no-field support.
- When new player results settle, refresh the historical lineup-status
  diagnostic:

  ```bash
  uv run --with requests python scripts/player_lineup_status_backtest.py --sports-predict --write
  ```

  This checks whether confirmed starter/bench/not-listed handling is still
  aligned with the player field policy.
- For count-heavy days, run the read-only count policy diagnostic when new
  results have settled:

  ```bash
  uv run --with requests python scripts/count_policy_backtest.py --sports-predict --write
  ```

  For the card-specific branch, run the component audit:

  ```bash
  uv run --with requests python scripts/card_field_component_audit.py --sports-predict --write
  ```

  Treat card count markets as no-field/current-source rows before lock unless
  there is a directly priced card market or confirmed referee information strong
  enough to justify a probability change. Do not use strict-referee information
  to push above the neutral/base card model.
  Exact current Polymarket total/team corner markets are allowed as the
  independent count probability only when the corner type and threshold match
  exactly and the market clears the stricter `$5,000` count-volume gate. This
  promotion is current-source evidence, not a historical derivative-market
  replay; the gate is intentionally narrower than the ordinary count model.
- For count-heavy days where the field is not visible, refresh the no-field
  guardrail before considering any raw-count override:

  ```bash
  uv run --with requests python scripts/count_no_field_policy_backtest.py --sports-predict --write
  ```

  The current result does not promote a no-field replacement for raw count
  probabilities; it only confirms that card no-field fallbacks should be
  monitored as more rows settle.
- When considering count mean/intensity changes, run both the raw replay and the
  deployable manual-policy replay:

  ```bash
  uv run --with requests --with numpy --with scipy --with pandas --with pyarrow --with statsmodels \
    python scripts/count_intensity_replay.py --sports-predict --write

  uv run --with requests --with numpy --with scipy --with pandas --with pyarrow --with statsmodels \
    python scripts/count_intensity_policy_replay.py --sports-predict --write
  ```

  Do not promote a count-intensity challenger unless it beats `current_v4`
  through the actual manual policy and has a positive match-bootstrap lower
  bound.
- To refresh the total-count dependence proxy gate, run:

  ```bash
  uv run --with pandas --with numpy --with scipy --with statsmodels --with pyarrow \
    python scripts/count_total_dependence_audit.py --write

  uv run --with pandas --with numpy --with scipy --with statsmodels --with pyarrow --with requests \
    python scripts/count_total_dependence_policy_replay.py --sports-predict --write
  ```

  The current Cup replay keeps this as proxy-only; a future positive promotion
  requires enough settled Cup rows, positive manual-policy bootstrap lower
  bound, and non-negative raw-model support.
- Check confirmed lineups in the final hour.  A confirmed benching reduces
  player SOT/scorer prices; do not hand-cut players on rumor.
- Check referee only for card-threshold markets. Use the repeatable live audit:

  ```bash
  uv run --with pyyaml python scripts/referee_research_audit.py --write
  ```

  Do not add or change a referee override unless the match official is verified
  from a current source and an independent cards-per-game estimate is researched.
- Do not push low-base-rate player/event props above the field unless there is
  a confirmed material fact and a market anchor.
- Knockout-only timing/special markets are priced from
  `src/models/specials.py`; this includes timing props, any-player brace/SOT
  props, total shots on and off target, and corners before hydration.
  Validation is logged in
  `data/logs/special_market_backtest.md`. The 2026-06-28 report shows
  leave-one-tournament-out base-rate evidence plus an oracle-volume diagnostic:
  scaling by realized match goals/cards/offsides/SOT beat constant tournament
  baselines for every scaled special family. This supports the shape of the
  pricer but is not a full ex-ante odds backtest. The latest report also logs
  reliability bins: constant leave-one-tournament-out family priors are
  well-calibrated as base rates, while oracle-volume scaling improves Brier but
  raises calibration error for some high-rate families such as offsides, late
  cards, and any-player 2+ SOT. The current StatsBomb cache has no historical
  pre-match odds or projected count means, so an ex-ante volume-scaling replay
  is not available; the report logs this explicitly. The report also tests a
  chronological in-tournament adaptation candidate: for each held-out
  tournament, global priors are blended with only earlier matches from the same
  tournament, with simultaneous kickoffs scored before updating. This is the
  right proxy for "the current tournament has been high/low event so far." It
  is not promoted: the best all-match point estimate improves Brier by only
  0.0002 with bootstrap ci_97.5 above zero, while the knockout-only replay is
  worse by 0.0003 Brier. Current-tournament special trends are therefore
  research warnings, not automatic probability adjustments. The 2026-06-28
  tail-risk proxy nevertheless shows that shrinking these special priors with
  the generic field rule is worse than using the raw special model, so manual
  policy keeps `raw_special_model` for timing/special rows. The latest report
  also adds tournament-heterogeneity diagnostics: 2 evidence keys are `high`
  heterogeneity, 7 are `moderate`, and 4 are `low`. The high-heterogeneity keys
  are total-shots thresholds, so total-shots special rows need extra count-input
  scrutiny before lock. Treat hydration-goal rows as noisy priors, not
  validated edge, and keep all timing/special props lower-confidence than
  ordinary odds-anchored goal markets. In the uncertainty table, goal before
  first hydration and goal after second hydration are both `weak_near_coinflip`
  priors: +0.0007 and +0.0013 Brier edge versus 50%, respectively. Before
  locking a special-heavy slate, run the live row-level audit:

  ```bash
  uv run --with pandas --with numpy --with scipy --with pyarrow --with pyyaml --with requests --with statsmodels \
    python scripts/special_market_live_audit.py --all-open --write --count-source-template

  uv run --with requests \
    python scripts/current_tournament_stats_audit.py --start-date 2026-06-11 --end-date YYYY-MM-DD --sports-predict --write --update-special-count-csv

  uv run --with requests \
    python scripts/special_count_source_discovery.py --write

  python3 scripts/special_count_source_audit.py --write
  ```

  Latest report: `data/logs/special_market_live_audit.md`. The 2026-06-28
  all-open audit excludes already-started matches and shows 20 future special
  rows: 10 `BASE_PRIOR`, 2 `COUNT_THRESHOLD_HIGH_HETEROGENEITY`, 1
  `COUNT_THRESHOLD_PROXY`, 1 `COUNT_THRESHOLD_SCALED_FAR_FROM_BASE`, and 6
  `WEAK_PRIOR`. The count threshold priors are now explicit no-match-info
  checks in `special_market_backtest.md`: 20+ total shots hit 0.736, 22+ total
  shots hit 0.618, and 2+ early corners hit 0.701 in 314 StatsBomb matches.
  USA/BIH and ARG/CPV 22+ total-shots rows are high-heterogeneity transfer
  priors; AUS/EGY 20+ total shots is the current far-from-base warning because
  the live entry is 33% versus a 73.6% base hit rate. Recheck count-volume
  inputs before lock. The live audit now logs signed `model_minus_base_pp`,
  exact `market_id`, and writes `data/special_count_sources_template.csv` for
  the 4 live special count-threshold rows. `current_tournament_stats_audit.py`
  uses ESPN public box scores as a current-tournament count source; the
  2026-06-29 run found 73 completed matches, 146 team-match rows, and
  `current_tournament_stats_status: COMPLETE`. It filled one source slot for
  the 3 full-match total-shots rows, but did not fill the early-hydration
  corner row because ESPN does not expose first-hydration corner counts.
  `special_count_source_discovery.py` checks public Polymarket markets for
  exact current count sources and writes
  `data/logs/special_count_source_discovery.md`. The 2026-06-29 discovery found
  0 `EXACT_USABLE` rows, one first-half-corners near-miss for USA/BIH, and
  goal-total-only markets for the 3 total-shots rows. Do not fill the source
  template from those near-misses. `special_count_source_audit.py` now joins
  that discovery log and reports `no_usable_public_exact_rows`, so the readiness
  packet distinguishes true usable exact count sources from same-match goal
  totals and period-mismatched corner markets. Fill the remaining template slots
  with independent current count-volume sources, then require
  `data/logs/special_count_source_audit.md` to be acceptable before treating
  those rows as researched. `manual_research_queue.md` promotes
  the AUS/EGY far-from-base count-threshold row to `HIGH` plus keeps the 22+
  total-shots rows in a distinct `special_count_heterogeneity` bucket. These
  audits are not probability transforms.

- For special-market policy boundaries, run:

  ```bash
  python3 scripts/special_boundary_audit.py --write
  ```

  Latest report: `data/logs/special_boundary_audit.md`. The current run covers
  26 open special rows and only 11 official settled special rows, so the special
  branch remains proxy-backed rather than official-history validated. It flags
  9 weak near-coinflip rows, 7 high-heterogeneity or far-from-base
  count-threshold rows, and a maximum live scaled-vs-base shift of 41
  percentage points. Automatic special-prior shrinkage and knockout
  in-tournament trend adaptation both remain unpromoted. Use raw special proxy
  priors with these guardrails; do not add field, shrinkage, trend, or
  catch-up transforms without a new promotion gate.
