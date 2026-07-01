# Strategy Research Findings - 2026-06-27

Status: archived snapshot. Current source counts, direct-anchor coverage, and
promotion decisions are maintained in `docs/methodology_sources.md`,
`docs/strategies.md`, and `docs/full_research_program.md`. Do not use this
dated report as the live operational state without checking those files first.

This is the evidence-backed strategy review for the manual Probability Cup
entry. It separates what is validated from what is only plausible.

The source-backed methodology guardrails are maintained in
`docs/methodology_sources.md`. This report applies those guardrails to the
current tournament data.

## Sources Checked

- SportsPredict API documentation: Brier scoring, RBP, and stage weighting in
  `docs/API-Documentation.pdf`.
- Gneiting and Raftery, "Strictly Proper Scoring Rules, Prediction, and
  Estimation": strict propriety means expected score is optimized by honest
  calibrated probabilities.
- Dixon and Coles, "Modelling Association Football Scores and Inefficiencies in
  the Football Betting Market": Poisson-style football score models are the
  standard structure for scoreline markets.
- Wolfers and Zitzewitz, "Prediction Markets": markets aggregate dispersed
  information and usually outperform simple benchmarks.
- Goto, Takeishi, and Yairi, "Forecast Sports Outcomes under Efficient Market
  Hypothesis": football odds are strong probability benchmarks, but overround
  and favorite-longshot bias need explicit conversion discipline.
- Clegg, Song, and Cartlidge, "A market-calibrated accelerated failure time
  model for in-play football forecasting": market-calibrated team strength is
  the main goal-model accuracy driver, so the manual goal grid remains anchored
  to current 1X2 and totals before adding other features.
- StatsBomb open data: cached tournament event/player data used for half shares,
  penalty/red, offsides, and player rates.
- Football-Data: historical computer-ready football results/odds data used for
  count-model validation.
- The operational translation of these sources is documented in
  `docs/methodology_sources.md`.

Links:
- https://academic.oup.com/jrsssc/article-abstract/46/2/265/6990546
- https://www.aeaweb.org/articles?id=10.1257%2F0895330041371321
- https://arxiv.org/abs/2604.17194
- https://arxiv.org/abs/2605.16066
- https://github.com/statsbomb/open-data
- https://www.football-data.co.uk/

## Non-Negotiable Scoring Implication

RBP is a Brier difference:

```text
RBP = (crowd_brier - user_brier) * 100
```

Stage weighting increases the value of later markets, but it does not change the
optimal prediction. Because Brier is strictly proper, the best strategy is the
best calibrated probability. There is no valid catch-up strategy based on
extremizing or intentionally increasing variance.

## Evidence Generated Locally

Main reproducible report:

```bash
uv run --with pandas --with pyarrow --with tabulate --with requests \
  python scripts/strategy_research.py --history --sports-predict --write
```

Output:

- `data/logs/strategy_research.md`
- live layer: 529 safe settled markets, 57 matches
- offline model layer: 782,157 historical validation rows

The script does not call `/predictions`, does not read bot stored predictions,
and does not submit anything. SportsPredict is used only for read-only
`/results` settlement of older history rows.

## Backtest Integrity Audit

Validated with:

```bash
uv run --with requests python scripts/backtest_integrity_audit.py --sports-predict --write
```

Latest report: `data/logs/backtest_integrity_audit.md`. The 2026-06-28
refresh read 714 SportsPredict `/results` rows.

The audit found 78 V1 history rows. SportsPredict `/results` exposes market IDs
but not historical match names, while V1 rows in `docs/history.md` do not expose
market IDs. Because many question texts repeat across matches, V1 rows are now
settled only when every `/results` row with the same question text implies the
same binary outcome.

Strict safe-settlement result:

- 48 V1 rows had unique, inferable question-level outcomes and were settled.
- 27 V1 rows had ambiguous repeated-question outcomes and were left unsettled.
- 3 V1 rows matched SportsPredict result rows whose submitted probability and
  Brier score were equally compatible with both outcomes, such as 50% with
  0.25 Brier, and were left unsettled.
- 481 V2 rows were already exact from `docs/history.md`.
- Promoted policy reports therefore score 529 safe exact rows, not the old
  question-overwrite set. The extra result rows do not expand the policy
  backtest sample because they lack the historical match join fields needed to
  disambiguate repeated V1 question text.

## Live Manual Layer Results

Fixed field-relative policies on 529 safe exact settled rows:

| policy | RBP delta vs current | Brier |
| --- | ---: | ---: |
| `asym0_075_cap12` | +188.01 | 0.2210 |
| `asym0_100_cap15` | +173.02 | 0.2213 |
| `asym0_075` | +168.64 | 0.2213 |
| `shrink50` | +124.30 | 0.2222 |
| `crowd` | +35.79 | 0.2239 |
| `current` | 0.00 | 0.2245 |
| `expand125` | -124.96 | 0.2269 |
| `flat50` | -1347.36 | 0.2500 |

The matching `history_backtest.py` bootstrap/holdout run showed:

- `asym_bal12` fixed-policy chronological holdout after 120 rows: +127.06 RBP.
- match-level bootstrap point estimate: +188.01 RBP.
- bootstrap interval was wide, so this is a disciplined edge, not certainty.

One-match-ahead category-aware policy selection gained only +111.61 RBP. It
underperformed the simple fixed policy, so do not promote category-specific
dynamic policy mining.

## Combined Operational Policy Validation

The live manual entry workflow no longer uses the global field-relative rule
for every market. It applies the family-specific policy in `src/manual/policy.py`
to the independent model values generated by `scripts/full_manual_pricing.py`.

Validated with:

```bash
uv run --with requests python scripts/manual_policy_backtest.py --sports-predict --write
```

Latest report: `data/logs/manual_policy_backtest.md`.

On 529 safe exact settled rows across 57 matches:

| policy | official RBP | delta vs current | Brier |
| --- | ---: | ---: | ---: |
| `combined_manual_policy` | 1595.09 | +351.37 | 0.2179 |
| `combined_player_field` | 1556.86 | +313.14 | 0.2186 |
| `global_asym075_cap12` | 1431.73 | +188.01 | 0.2210 |
| `shrink50` | 1368.02 | +124.30 | 0.2222 |
| `field` | 1279.51 | +35.79 | 0.2239 |
| `current` | 1243.72 | 0.00 | 0.2245 |

Chronological holdout after 120 prior settled rows:

- `combined_manual_policy`: +279.05 RBP on 401 rows.
- `combined_player_field`: +272.17 RBP on 401 rows.
- `global_asym075_cap12`: +127.06 RBP on 401 rows.

Match-level bootstrap for the combined policy:

- point estimate: +351.37 RBP.
- 95% interval: +114.59 to +610.23 RBP.

Interpretation: the promoted operational edge is not a generic shrink-to-field
rule. It comes from using raw odds-anchored probabilities for ordinary
goal/outcome markets, field-following for card counts, selective model trust in
strong SOT count markets, and a stricter player-prop field policy after player
research flags are cleared.

## Target Gap Scale Check

Validated with:

```bash
uv run --with requests python scripts/tournament_target_audit.py --write
```

Latest report: `data/logs/tournament_target_audit.md`.

As of `2026-06-28T23:10Z`, SportsPredict exposed 120 open markets across 8
matches. Using the manual leaderboard score provided in the thread, 1243.59
RBP:

| target | gap | required RBP per known open market |
| --- | ---: | ---: |
| #10 small prize, 1971.75 | 728.16 | 6.07 |
| #1 first, 2114.68 | 871.09 | 7.26 |

The known-open market mix transferred through the safe-settlement
combined-policy category backtest implies about +98.28 RBP of rough historical
policy lift versus the old current strategy on those currently exposed markets.
The special bucket remains zero in that historical mix table because there are
no settled SportsPredict special-history rows with the same market structure.
The report now includes the separate StatsBomb special-proxy tail-risk table
from `data/logs/leaderboard_tail_risk.md`; that proxy is useful for prize-tail
decisions, but it is deliberately not folded into the historical
`rough_mix_edge`. This is a scale check, not a forecast, and later unopened
markets can add opportunity. The main strategic implication is negative: do
not chase the gap with artificial extremizing. The only defensible path is to
capture every calibrated edge: visible field policy, final lineups,
stale-player cleanup, referee/card discipline, and fresh odds anchors.

## Special Prior Shrinkage Gate

Validated with:

```bash
python3 scripts/special_prior_shrinkage_backtest.py --write
```

Latest report: `data/logs/special_prior_shrinkage_backtest.md`.

This tests a narrow special-market candidate: shrink StatsBomb proxy priors
toward 50% to avoid overtrusting weak special/timing families. Candidates were
predeclared shrink factors 0.00, 0.25, 0.50, 0.75, and 1.00 on the
leave-one-tournament-out StatsBomb replay.

Result:

- all special rows: raw factor 1.00 was best on 4,082 proxy rows.
- weak near-coinflip binary timing rows: best point estimate was factor 0.75,
  +21.79 RBP versus raw on 628 rows.
- tournament bootstrap for the weak timing shrink crossed zero: -51.13 to
  +117.79 RBP, Pr(>0)=0.666.

Decision: do not promote automatic shrinkage. Weak special priors stay as
research/field-source warnings; the live probability remains the raw special
proxy unless direct current evidence says otherwise.

## Special Boundary Audit

Validated with:

```bash
python3 scripts/special_boundary_audit.py --write
```

Latest report: `data/logs/special_boundary_audit.md`.

This audit ties together the live special audit, special proxy backtest,
special shrinkage replay, and official special-outcome sample so special rows
cannot be overclaimed as official-history edge.

Current result:

- 20 open special rows.
- 5 official settled special rows, all usable but one per family, which is
  below the historical-promotion sample threshold.
- 6 weak near-coinflip rows.
- 3 count-threshold rows with high heterogeneity or a far-from-base live
  scaled probability.
- maximum live scaled-vs-base shift: 40 percentage points.
- automatic shrinkage: not promoted.
- knockout in-tournament adaptation: not promoted.

Decision:
`raw_proxy_with_guardrails_no_shrink_no_official_promotion`. Use raw special
proxy priors as lower-confidence entries with weak-prior/count-volume rechecks.
Do not add field, shrinkage, current-tournament trend, or catch-up transforms
without a new promotion gate.

## Live Research Queue

Implemented and refreshed on 2026-06-28:

```bash
uv run --with pandas --with numpy --with scipy --with pyarrow --with pyyaml --with requests --with statsmodels \
  python scripts/manual_research_queue.py --all-open --write
```

Latest report: `data/logs/manual_research_queue.md`.

This is not a probability transform. It is an execution-control layer built on
the same read-only pricing stack and risk classifications as
`live_market_risk_audit.py`. It ranks only rows where the evidence says current
external facts can change the correct manual entry. The 2026-06-29 01:13Z run
ranked 74 actionable future rows: 20 HIGH and 54 MEDIUM. The top priorities were
unresolved player props, missing card-field averages, explicit recent
player-role risks, and the AUS/EGY far-from-base total-shots special warning,
which matches the backtests and proxy gates:

- player props: largest validated family leak; unresolved flags are field-led
  unless exact prop odds or confirmed lineup facts clear them.
- recent player-role risk: 5 open rows are now routed through a distinct
  `player_role_risk` bucket using exact `market_id` joins from the role audit;
  this is an execution/lock-check warning, not a probability transform.
- card counts: validated field-led count family; missing field is a direct
  execution gap.
- non-card count rows: field-relative policy only when the visible field is
  available.
- hydration-goal specials: weak near-coinflip StatsBomb priors; treat large
  field/source disagreement as a research flag rather than strong edge.
- count-threshold specials: far-from-base and high-heterogeneity rows are
  explicit count-volume/source checks, not probability transforms.

Operational decision: run the queue before nightly sheets and near lock. Use it
to decide what to research first, not to alter odds-anchored low-risk rows.

## Manual Evidence Coverage Audit

Implemented and refreshed on 2026-06-28:

```bash
uv run --with pandas --with numpy --with scipy --with pyarrow --with pyyaml --with requests --with statsmodels \
  python scripts/manual_evidence_coverage.py --all-open --write
```

Latest report: `data/logs/manual_evidence_coverage.md`.

This is the row-level evidence proof layer. It joins each live priced market to
the strategy evidence matrix, then records the unresolved gate before lock. It
does not change probabilities. It answers whether each manual entry is backed
by a promoted branch, a current-source rule, a limited-support branch, a proxy,
or a guardrail.

The 2026-06-29 02:16Z future-open run covered 120 markets, excluding
already-started RSA/CAN rows:

- Evidence status: 56 `PROMOTED`, 22 `PROMOTED_LIMITED`, 16
  `CURRENT_SOURCE_RULE`, 20 `PROXY_ONLY`, and 6 `GUARDRAIL_ONLY`.
- Live gate: 32 `COVERED`, 14 `CURRENT_ANCHOR_RECHECK`, 25 `FIELD_REQUIRED`,
  16 `PROXY_ONLY`, 3 `SPECIAL_COUNT_RECHECK`, 1
  `SPECIAL_COUNT_RESEARCH_REQUIRED`, 15 `PLAYER_RECHECK`, 8
  `PLAYER_RESEARCH_REQUIRED`, and 6 `GUARDRAIL_ONLY`.
- Evidence areas: 31 count-field-policy rows, 25 odds-goal rows, 20 special
  rows, 22 player-no-field rows, 16 exact Polymarket anchors, and 6
  penalty/red rows.

Operational decision: use the coverage audit after the full daily sheet and
before manual entry. Rows in `FIELD_REQUIRED`, `PLAYER_RECHECK`, and
`PLAYER_RESEARCH_REQUIRED` are execution work. `SPECIAL_COUNT_RECHECK` and
`SPECIAL_COUNT_RESEARCH_REQUIRED` are special-proxy count-volume gates, not
probability transforms. Rows in `CURRENT_ANCHOR_RECHECK` are exact current
source anchors whose historical replay gate has not promoted them as edge.
Rows in `PROXY_ONLY` and `GUARDRAIL_ONLY` should be entered from the documented
model/guardrail but treated as lower confidence than promoted official-history
branches.

## Manual Execution Leverage Audit

Implemented and refreshed on 2026-06-28:

```bash
python3 scripts/manual_execution_leverage.py --write
```

Latest report: `data/logs/manual_execution_leverage.md`.

This is a work-prioritization layer over `manual_evidence_coverage.md`, not a
prediction model. It reads historical category contribution from
`manual_policy_backtest.md` and assigns each open market a conservative
historical RBP-per-row context. It deliberately leaves special proxy rows as
`n/a` because there are no official settled SportsPredict special rows.

The 2026-06-29 02:16Z run ranked the 120 open markets:

- `HIGH`: 20 rows. First are 11 card-field rows, using the count-card
  historical contribution of about +4.03 RBP per settled row. Next are 8 hard
  player-prop research rows, using the player historical contribution of about
  +2.07 RBP per settled row. The remaining high row is the AUS/EGY 20+
  total-shots special count-threshold research gate.
- `MEDIUM`: 32 rows. Mostly player rechecks, count-field rows, and three
  special count-threshold rechecks; corner field rows carry about +0.74
  historical RBP per row.
- `MONITOR`: 36 rows. Proxy-only specials, penalty/red guardrails, and
  `CURRENT_ANCHOR_RECHECK` exact anchors where source verification matters but
  historical lift is not a promoted optimization branch.
- `LOW`: 32 rows, already covered by promoted branches with no unresolved live
  gate.

The separate research queue now exposes 5 `player_role_risk` rows inside the
15 player rechecks, while execution leverage keeps them under the broader
`PLAYER_RECHECK` state because role history is a guardrail rather than a
validated probability branch.

Operational decision: when time is scarce, capture visible card fields first,
then clear high-risk player props with exact prop odds, lineup facts, or field
fallback. Do not spend comparable time trying to tune low-risk odds-goal rows
or proxy-only special rows unless a source-quality alarm appears.

## Manual Calibration Watchlist

Implemented on 2026-06-28:

```bash
uv run --with pandas --with numpy --with scipy --with pyarrow --with pyyaml --with requests --with statsmodels \
  python scripts/manual_calibration_watchlist.py --all-open --write
```

Latest report: `data/logs/manual_calibration_watchlist.md`.

This operationalizes the rejected global calibration experiment without
promoting an overfit transform. The calibration audit found that full-sample
factor 1.10 looked slightly best, but one-match-ahead walk-forward calibration
lost -21.80 RBP versus the uncalibrated combined policy and worsened holdout
ECE. The watchlist therefore does not alter probabilities. It flags current
manual entries that sit in combined-policy reliability buckets with at least 10
historical rows and at least five points of absolute calibration error.

Current flagged buckets:

- 35-45%: 135 rows, mean 0.400, hit 0.311, abs error 0.089,
  historically overestimated.
- 65-80%: 37 rows, mean 0.704, hit 0.757, abs error 0.053,
  historically underestimated.
- 80-100%: 13 rows, mean 0.862, hit 0.769, abs error 0.092,
  historically overestimated.

Operational decision: treat watchlist rows as source-quality and execution
checks. Recheck exact market wording, liquidity, visible field averages,
lineups, and special-prior confidence before lock. Do not apply a blanket
probability multiplier unless future walk-forward evidence promotes one.

## Manual Family Blend Backtest

Implemented on 2026-06-29:

```bash
uv run --with requests python scripts/manual_family_blend_backtest.py --sports-predict --write
```

Latest report: `data/logs/manual_family_blend_backtest.md`.

This tests a legitimate forecast-combination idea: a predeclared linear pool of
the submitted model probability and the visible field average, with
one-match-ahead weights selected globally or by broad category when at least 20
prior category rows exist. It is a guard against replacing the promoted
family-specific policy with a generic adaptive model/field blend.

Result on the same 529 safe exact rows:

- Full-sample 50/50 global linear pool: +124.30 RBP vs current, still far below
  the combined manual policy's +351.37 RBP.
- Walk-forward holdout after 120 prior rows: category linear pool +133.36 RBP
  vs current, compared with +279.05 RBP for the combined manual policy.
- Paired walk-forward delta versus combined policy: -145.69 RBP, bootstrap
  95% interval -251.41 to -42.22, Pr(>0)=0.005.
- Decision: `REJECTED` in `data/logs/strategy_evidence_matrix.md`.

Operational decision: do not use adaptive category linear pooling as an
optimization. The promoted policy's asymmetric/player/card/goal special cases
are doing real work that generic pooling destroys, especially in player rows.

## Strategy Evidence Matrix

Implemented on 2026-06-28:

```bash
python3 scripts/strategy_evidence_matrix.py --write
```

Latest report: `data/logs/strategy_evidence_matrix.md`.

This consolidates the promotion state from the current backtest logs. The
2026-06-29T02:49Z refresh classified the branches as follows:

- `PROMOTED`: combined manual policy, raw odds-goal rows, count field policy.
- `PROMOTED_LIMITED`: researched player props, player no-field fallback inside
  tested support only.
- `CURRENT_SOURCE_RULE`: exact Polymarket non-player/player/corner anchors.
  These now have row-level snapshots in
  `data/logs/polymarket_direct_anchor_snapshots.csv` so the branch can be
  replayed against official outcomes when results settle. The scorer report
  `data/logs/polymarket_direct_snapshot_score.md` currently has 82
  semantically valid archived snapshot rows across 20 unique markets, 4 scored
  latest markets, 16 unscored latest markets, and latest-per-market Brier
  delta `-0.0289`, so this remains a current-source rule surfaced as
  `CURRENT_ANCHOR_RECHECK` rather than a historical promotion.
- `PROXY_ONLY`: special markets, because the official SportsPredict special
  sample is still too small for family-level conclusions.
- `GUARDRAIL_ONLY`: calibration watchlist, player recent-role history,
  penalty/red, exact-anchor risk, total-goals source quality, card-field
  component boundaries, player-prop boundaries, and special-market boundaries.
  Role history is routed into execution risk and research priority by exact
  `market_id`, but the role-policy backtest has not promoted a role-history
  probability rule.
- `NOT_PROMOTED`: odds-conversion power de-vig, no-field count fallback,
  count-intensity tuning, and automatic special-prior shrinkage.
- `REJECTED`: global post-calibration, category-wise linear pooling, and hard
  catch-up extremizing.

Operational decision: any future strategy change must improve this matrix with
fresh evidence, not only improve one full-sample table.

## Promotion Strength Audit

Implemented on 2026-06-29:

```bash
python3 scripts/promotion_strength_audit.py --write
```

Latest report: `data/logs/promotion_strength_audit.md`.

This report does not price markets. It audits whether the matrix's promoted
labels are strong, soft, limited, not-promoted, or only current-source/proxy
claims. The current run covers 8 major strategy areas and has 6 warning rows:

- `combined_manual_policy`: `PROMOTED_STRONG`; holdout delta +279.05 RBP and
  match-bootstrap lower bound +114.59.
- `odds_goal_rows`: `PROMOTED_SOFT`; raw odds beat field in full sample and
  holdout, with Pr(>0)=0.967, but the direct raw-vs-field bootstrap lower
  bound is -6.69 and raw-vs-shrink75 is weaker. Keep raw odds, but do not add
  extremization or new odds transforms from this branch alone.
- `odds_conversion_power`: `NOT_PROMOTED_TOTALS_GATE`; power de-vig beats
  proportional de-margin on the 1X2 historical odds sample with ci_2.5
  +1429.35 x100 Brier units and Pr(>0)=1.000, but the totals gate has ci_2.5
  -21.29 despite Pr(>0)=0.961. Because the score-grid model uses both 1X2 and
  totals, keep proportional de-margin for now.
- `count_field_policy`: `PROMOTED_STRONG_COMPONENT_WARNING`; the overall count
  policy lower bound is +37.48, but the incremental comparison versus the
  strong-SOT/no-card-field alternative has lower bound -46.53 and Pr(>0)=0.720.
  The dedicated card component audit now records
  `field_visible_required_no_referee_override_no_new_transform`: keep the
  documented count policy, but do not mine extra card/corner transforms.
- `researched_player_props`: `PROMOTED_LIMITED_STRONG_FIELD_WARNING`; the
  overall player policy lower bound is +15.71, but its direct comparison with
  field has holdout -4.34 and lower bound -70.90. This is why unresolved
  visible-field player rows remain field-led until exact prop/lineup research
  clears them. The dedicated player boundary audit records
  `field_or_exact_prop_required_for_unresolved_players_limited_no_field_base`,
  so the no-field base is not generalized to source-poor or higher-threshold
  player rows.
- `exact_polymarket_anchors` and `special_markets`: not historical promotions.
  Exact anchors are current-source only with 4 scored latest markets and
  negative early replay; special markets are StatsBomb proxy-only with only 5
  official settled special rows and boundary decision
  `raw_proxy_with_guardrails_no_shrink_no_official_promotion`.

Operational decision: treat `PROMOTED_SOFT` and `*_WARNING` rows as usable
policies with tighter interpretation. They can guide entries, but they cannot
justify extra transforms, broader generalization, or claims of fully proven
edge.

## Odds Conversion Gate

Validated with:

```bash
uv run --with numpy python scripts/odds_conversion_backtest.py --write
```

Latest report: `data/logs/odds_conversion_backtest.md`.

This tests whether the live bookmaker overround conversion should change from
proportional de-margin to a favorite-longshot-aware method. The report uses
local Football-Data closing odds only and does not call SportsPredict.

Current result:

- 115,809 1X2 rows and 47,344 over/under 2.5 rows from 2009-07-31 to
  2026-05-31.
- Power de-vig is best on the full 1X2 sample: Brier 0.598516 vs 0.598719 for
  proportional de-margin, +2348.91 x100 total Brier units, +20.283 Brier
  points per 1,000 markets.
- Power remains positive in 2021+ and 2023+ 1X2 holdouts, and the
  season/league-block bootstrap lower bound is positive: ci_2.5 +1429.35,
  Pr(>0)=1.000.
- For over/under 2.5, power has a positive point estimate (+187.95 x100 total
  Brier units), but the block-bootstrap lower bound is negative: ci_2.5 -21.29,
  Pr(>0)=0.961.

Decision: `not_promoted_keep_multiplicative_devig`. The 1X2 signal is real
enough to monitor, but totals do not clear the same gate. Do not change the
live score-grid odds conversion until both 1X2 and totals pass, or until an
archived SportsPredict-specific odds replay proves the change directly.

## Leaderboard Tail-Risk Objective

Validated with:

```bash
uv run --with requests --with numpy --with scipy python scripts/leaderboard_tail_risk.py --sports-predict --open-mix --special-proxy statsbomb --trials 20000 --stage-weight 2.0 --write
```

Latest report: `data/logs/leaderboard_tail_risk.md`.

This diagnostic addresses a different question than the mean-RBP policy
backtests: if the target is a prize threshold, does variance-seeking improve
the chance of catching up? It samples 20,000 slates of 120 markets from the
safe settled-history rows, stratified to the current open-market mix, scores
each strategy with a 2x stage multiplier, and replaces the 20 open special
markets with leave-one-tournament-out StatsBomb proxy rows for the exact
current special questions. Timing and player-aggregate specials use empirical
leave-one-tournament-out rates; shot-total and early-corner special thresholds
use leave-one-tournament-out negative-binomial count probabilities, matching
the live special pricer's count family more closely.

| strategy | mean RBP | p95 | p99 | Pr >= #10 gap | Pr >= #1 gap |
| --- | ---: | ---: | ---: | ---: | ---: |
| `combined_raw_special` | 801.40 | 1105.00 | 1225.37 | 66.05% | 36.70% |
| `combined_manual_policy` | 801.10 | 1104.62 | 1224.88 | 66.02% | 36.73% |
| `combined_penalty_red_field` | 798.16 | 1101.26 | 1221.88 | 65.44% | 36.10% |
| `combined_player_field` | 778.95 | 1065.32 | 1175.38 | 62.56% | 31.13% |
| `combined_count_field` | 772.59 | 1061.28 | 1172.80 | 60.99% | 30.64% |
| `combined_goal_field` | 745.00 | 1035.64 | 1141.28 | 55.27% | 25.12% |
| `global_asym075_cap12` | 631.97 | 821.10 | 900.66 | 21.04% | 1.96% |
| `current` | 605.26 | 988.59 | 1143.02 | 31.16% | 13.39% |
| `expand125` | 525.84 | 992.67 | 1184.59 | 25.19% | 11.77% |
| `field` | 487.10 | 506.74 | 515.66 | 0.00% | 0.00% |
| `expand150` | 420.58 | 964.48 | 1189.22 | 18.88% | 9.09% |
| `hard_model_side_99_1` | -2351.07 | -947.76 | -383.50 | 0.01% | 0.01% |
| `hard_field_deviation_99_1` | -3727.64 | -2088.86 | -1402.18 | 0.00% | 0.00% |

Interpretation:

- The promoted manual policy now includes the raw-special branch; it is best in
  this replay on both mean RBP and target-hit probability.
- The simpler `combined_player_field` challenger is now included because the
  player-only direct comparison is weak; it still trails the promoted policy in
  the full current-mix prize-threshold replay.
- The simpler `combined_count_field` challenger is also included because count
  policies depend heavily on field information; it trails the promoted mixed
  count rule in the full current-mix prize-threshold replay.
- The `combined_goal_field` challenger is included because goal/outcome markets
  are the largest current category; it trails badly, confirming that ordinary
  goal markets should stay raw odds-anchored rather than field-led.
- The `combined_penalty_red_field` challenger is included because penalty/red
  evidence is sparse; it is close but still trails the promoted global
  asymmetric policy on mean and both target-hit rates.
- The old combined policy left tail value on the table by applying the generic
  global field transform to special markets with no settled SportsPredict
  field backtest.
- The latest open-special boundary audit classifies 14 current special markets
  as `usable_base_prior` and 6 as `weak_near_coinflip`. The weak group is
  hydration-goal timing, so those rows should be treated as noisy priors rather
  than strong edge.
- Mild extremizing increases variance but still underperforms the targeted
  raw-special promotion on mean and both prize thresholds.
- Hard 99/1 strategies are not a rational catch-up plan; they destroy both
  expected score and target-hit rate.
- Limitation: special rows are a StatsBomb proxy scored against a 50% neutral
  baseline, not official SportsPredict special-market history. This makes the
  tail-risk report a decision guardrail, not a guarantee.
- The official special-outcome audit now exists as a separate check:

  ```bash
  uv run --with requests --with numpy --with scipy python scripts/special_official_results_audit.py --sports-predict --write
  ```

  Latest report: `data/logs/special_official_results_audit.md`. The 2026-06-28
  refresh read 714 SportsPredict `/results` rows and found 5 settled
  special/timing rows. All 5 parsed as special and had usable inferred
  outcomes, with no unsupported or Brier-uninferable special rows. Because the
  sample is one row per family, no new official-history promotion is justified;
  the raw-special branch remains proxy-backed, not official-history backed.

## Current Strategy Diagnostics

## Current Direct Polymarket Derivative Anchors

The 2026-06-28 pipeline update found that Polymarket exposes exact derivative
markets for some current World Cup questions, not just 1X2 match-winner
markets. The manual workflow now uses those direct prices for eligible
non-player goal/scoreline markets only when the semantic match is exact and the
individual Polymarket market volume clears `$1,000`.

Implemented and tested constraints:

- eligible families are narrow non-player goal/scoreline derivatives such as
  BTTS, halftime leader/tie, clean sheet, first goal, team total goals, win by
  margin, score in half, and match total goals.
- the matcher verifies wording, threshold, period, and team subject. Team totals
  must name the team after the colon before `O/U`; this prevents using a match
  total such as `Brazil vs. Japan: O/U 1.5` as `Brazil 2+ goals`.
- card and non-corner count markets are not broadly promoted through this
  layer. Corner counts use a separate stricter gate: exact same-match
  total/team corner O/U markets can replace the count model only when corner
  type, threshold, and a `$5,000` market-volume floor pass. Player props use a
  separate exact-player gate: CSV prop anchors still outrank Polymarket, and
  Polymarket can replace the player model only when the same match, player,
  family, threshold, and market-volume checks pass.
- the latest future-only coverage audit at `2026-06-29T02:14Z` captured 14
  non-player direct anchors, 1 exact player-prop anchor, and 1 exact
  corner-count anchor.
- the player-prop anchor was Matheus Cunha to score: the exact Polymarket
  player-prop price on roughly `$4,000` volume moved the row from 41% raw
  model to 30%.
- the current corner-count anchor is BRA/JPN 9+ corners: the coverage audit
  showed the row moving from 50% model to 56% market on roughly `$12,000`
  volume.

Promotion status: operational current-source rule, not historical calibration
promotion. We now archive exact derivative-market snapshots and score them
against read-only official outcomes as they settle, but the latest scorer
report has only 4 scored latest-per-market rows, 16 unscored latest markets,
82 semantically valid snapshot rows, and a negative latest-per-market anchor
delta (`-0.0289`, 1 anchor win). It
also excludes one older archived first-goal row whose Polymarket question was
"neither team to score first" rather than the target team's first-goal market.
The historical-promotion gate is `not_promoted_insufficient_sample` with a 30
scored latest-market minimum. The evidence gate is therefore semantic
correctness plus live source quality today, tested by
`tests/test_polymarket_direct.py`, `tests/test_full_manual_pricing.py`, and
`tests/test_live_market_risk_audit.py`, with `polymarket_direct_snapshot_score`
as the future replay gate.

Implemented on 2026-06-28 after the first replay sample remained small and
negative: `scripts/polymarket_direct_anchor_risk_audit.py` reads the current
coverage report and snapshot scorer, then writes
`data/logs/polymarket_direct_anchor_risk_audit.md`. It does not call the API,
read `/predictions`, or transform probabilities. The current report reconciles
all 16 exact anchors, flags 3 high-risk large shifts, and marks the remaining
13 as medium risk because exact anchors are still a `CURRENT_SOURCE_RULE`, not
a historical promotion. The high-risk rows are BRA/JPN 9+ corners, where the
exact corner anchor moves 50% to 56%; Matheus Cunha to score, where the exact
player-prop anchor moves 41% to 30%; and France to lead at halftime, where the
exact anchor moves 54% to 59%. For those rows, verify wording, lineup/minutes,
visible field, and at least one independent source before lock.
The follow-up integration joins that report into
`manual_evidence_coverage.md` and `manual_execution_leverage.md` as
`direct_anchor_risk`, so high-risk large exact-anchor shifts are visible in the
daily checklist without changing the underlying probability or replacing the
player/field execution gate.

Implemented on 2026-06-29: `scripts/odds_total_source_audit.py` reconciles the
current manual coverage total/team-goal rows against exact direct total anchors
and the odds-source audit. The current report has 9 total/team-goal rows, 7
direct total anchors, 2 rows without direct total anchors, 3 thin-but-aligned
direct anchors, 0 large direct/model shifts, and 0 entry/direct-anchor
mismatches. This is a guardrail only: thin or missing direct total anchors
trigger near-lock source review, not a probability transform.

By category, current model vs field:

| category | n | skill vs field | brier model | brier field | finding |
| --- | ---: | ---: | ---: | ---: | --- |
| goal markets | 169 | +120.52 | 0.2171 | 0.2242 | market-anchored goal model is acceptable |
| event base rates | 24 | +5.28 | 0.1976 | 0.1998 | penalty/red base rates are acceptable |
| count h2h | 130 | -15.35 | 0.2298 | 0.2286 | no category-wide edge, but more-SOT is strong |
| count thresholds | 126 | -19.04 | 0.2354 | 0.2339 | mixed; SOT/offsides good, cards/total SOT weak |
| player props | 80 | -127.20 | 0.2227 | 0.2068 | biggest leak; lineup and prop-market discipline required |

Top live families:

- `team_more_count_than/sot/2h`: +87.38 skill vs field.
- `team_count_ge/sot`: +101.41 skill vs field.
- `team_count_ge/offsides`: +45.32 skill vs field.

Weak live families:

- player props overall, especially score-or-assist.
- card count threshold markets.
- total SOT / second-half total SOT.
- corner head-to-head has noisy negative live evidence despite positive offline
  model evidence.

## Offline Model Layer Results

Large historical validation on 782,157 rows:

- raw model OOF Brier: 0.2303.
- simple cross-season post-calibration OOF Brier: 0.2303.
- delta: -0.0001, i.e. no useful improvement.

Family edges vs base-rate baseline were positive for almost every family.
Strongest:

- `more_sot`: +0.0355 Brier edge vs base.
- `team_win`: +0.0286.
- `team_sot_ge4`: +0.0222.
- `more_corners`: +0.0221.
- `team_goals_ge2`: +0.0207.

Weakest:

- `total_cards_ge4`: -0.0004 vs base.
- `h2_more_than_h1`: +0.0001.
- `more_fouls`: +0.0004.

Conclusion: the core model stack is not globally broken; simple recalibration
does not improve it on large historical data. The live leak is mostly the manual
interaction with field/player/lineup markets, not a proven reason to replace the
entire count model.

## Promoted Manual Strategy

Use this family-specific policy when the UI field average is visible:

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
    if manual_model >= field:
        manual = field
    else:
        manual = field - min(10 points, round(0.75 * (field - manual_model)))
elif special_market:
    manual = manual_model
elif manual_model >= field:
    manual = field
else:
    manual = field - min(12 points, round(0.75 * (field - manual_model)))
```

Reason:

- ordinary goal/outcome markets are best served by raw odds-anchored model
  probabilities, not field transforms.
- card-count markets have been a large leak, and the field beats the current
  model on settled card counts.
- player props are the highest-risk family; the validated edge is capped
  below-field disagreement after lineup/prop research, not raw player-model
  trust.
- for unresolved player flags, field-following is safer than pretending the
  stale player estimate is researched.
- for remaining non-goal/non-player markets, above-field positions and very
  large deviations remain negative in live history, so use the capped asymmetric
  global rule.

Use the independent model probability only when the field is not visible before
locking the answer.

## Market-Family Operating Policy

Goal/win/totals:

- Use exact current Polymarket same-question anchors first when the
  wording/threshold/period/team/volume gate passes.
- Otherwise anchor to ESPN/DraftKings plus Polymarket 1X2/totals and price
  derivative scoreline markets from the coherent score grid.
- Do not override without a market-quality reason.
- The live odds-source audit now logs Polymarket liquidity and equal-blend
  sensitivity (`blend_shift`), not just book-vs-market disagreement. This
  prevents a thin prediction market from being treated the same as a liquid
  one while also avoiding unnecessary model changes when the thin market is
  numerically aligned.
- Goal policy backtest:

  ```bash
  uv run --with requests python scripts/goal_policy_backtest.py --sports-predict --write
  ```

- Latest report: `data/logs/goal_policy_backtest.md`.
- Settled odds-anchored goal layer, 169 safe exact rows:
  - raw current model Brier 0.2171 vs field 0.2242.
  - raw current model was best among tested fixed policies.
  - field-only transform: -120.52 RBP vs current.
  - chronological holdout after 60 prior settled goal rows: raw current stayed
    best; field-only lost -91.49 RBP.
  - direct raw-current-minus-field comparison: +120.52 RBP in full sample,
    +91.49 in chronological holdout, match-bootstrap -6.69 to +243.03,
    Pr(>0)=0.967.
  - direct raw-current-minus-best-shrink75 comparison: +21.88 RBP in full
    sample, +18.01 in chronological holdout, match-bootstrap -9.72 to +51.58,
    Pr(>0)=0.911.
  - cutoff sensitivity over 30, 45, 60, 75, and 90 prior goal rows: raw beat
    field in 5/5 configs, raw beat the 75% shrink rule in 5/5 configs, and raw
    was best in 4/5 configs. The worst raw-minus-field holdout edge was +72.64
    RBP and the worst raw-minus-shrink75 edge was +14.67 RBP.
  - every tested shrink/asymmetric policy had negative point estimate versus
    raw current.
- If field is visible, do **not** apply the global field policy to ordinary
  win/draw/goal-total/BTTS/halftime scoreline markets. Use exact current
  Polymarket same-question anchors when the direct gate passes; otherwise use
  the raw odds-anchored model probability unless market-quality evidence says
  the odds feed is stale.
- Timing/special goal props are not part of this validation; use
  `data/logs/special_market_backtest.md` for those.

SOT/corners/fouls/offsides/cards:

- Keep the negative-binomial count model as the independent anchor.
- Count policy backtest:

  ```bash
  uv run --with requests python scripts/count_policy_backtest.py --sports-predict --write
  ```

- Latest report: `data/logs/count_policy_backtest.md`.
- Settled count layer, 256 safe exact rows by stat:
  - `cards`: -157.01 skill vs field, Brier 0.3009 vs field 0.2607.
  - `corners`: -38.92 skill vs field, Brier 0.2355 vs field 0.2247.
  - `fouls`: -12.37 skill vs field, Brier 0.2467 vs field 0.2441.
  - `offsides`: +41.61 skill vs field, Brier 0.2390 vs field 0.2482.
  - `sot`: +132.30 skill vs field, Brier 0.1907 vs field 0.2056.
- Best predeclared mixed count policy:
  - `strong_sot_current_cards_field_else_global`: use current model for
    `team_count_ge/sot` and `team_more_count_than/sot`; use field for card
    count markets; use the global asymmetric rule for other count markets.
  - +186.56 RBP vs current on all safe settled count rows.
  - chronological holdout after 60 prior settled count rows: +117.70 RBP.
  - match-bootstrap 95% interval: +37.48 to +345.29 RBP.
  - direct promoted-minus-field comparison: +152.17 RBP in full sample, +76.24
    in chronological holdout, match-bootstrap -33.48 to +328.38, Pr(>0)=0.956.
    This is stronger than the player direct comparison but still not a
    positive-lower-bound proof; the current-mix prize-threshold replay rejects
    switching all count rows to field.
  - direct promoted-minus-strong-SOT-without-card-field comparison: +18.87 RBP
    in full sample, +14.00 in chronological holdout, match-bootstrap -46.53 to
    +87.10, Pr(>0)=0.720. Card-field-following remains useful but less
    independently robust than the full mixed count rule.
- Count-intensity replay:

  ```bash
  uv run --with requests --with numpy --with scipy --with pandas --with pyarrow --with statsmodels \
    python scripts/count_intensity_replay.py --sports-predict --write
  ```

  Latest report: `data/logs/count_intensity_replay.md`.
  This reconstructs settled count markets from ESPN/DraftKings historical
  pickcenter odds and the current CountPricer, then scores predeclared
  `TOURNAMENT_INTENSITY` maps. It does not reconstruct historical Polymarket
  blends, old intraday odds snapshots, or old referee overrides.
  - `current_v4`: +171.74 RBP vs submitted, Brier 0.2260.
  - `sot105_only`: +195.70 RBP vs submitted, Brier 0.2250.
  - `uniform090_v3`: +13.23 RBP vs submitted, Brier 0.2321.
  - `aggressive_bot2`: +208.48 RBP vs submitted, Brier 0.2245.
  Interpretation: the replay supports replacing the old uniform 0.90 map. The
  aggressive map and the narrower SOT-only map are real challengers, but not
  wholesale promotions from raw reconstruction alone: by family, raising SOT
  improves total-SOT thresholds while worsening `team_count_ge/sot` and
  `team_more_count_than/sot/2h`, the exact SOT families the manual policy
  currently trusts raw.
- Count-intensity manual-policy replay:

  ```bash
  uv run --with requests --with numpy --with scipy --with pandas --with pyarrow --with statsmodels \
    python scripts/count_intensity_policy_replay.py --sports-predict --write
  ```

  Latest report: `data/logs/count_intensity_policy_replay.md`.
  This passes each candidate's reconstructed count probability through the
  live `manual_decision()` policy with the historical field average before
  scoring, so it is the deployable test for manual entries when the field is
  visible.
  - `sot105_only`: +196.43 RBP vs submitted, +15.21 versus `current_v4`
    policy, Brier 0.2249.
  - `aggressive_bot2`: +189.44 RBP vs submitted, +8.22 versus
    `current_v4` policy, Brier 0.2252.
  - `current_v4`: +181.22 RBP vs submitted, Brier 0.2255.
  - match-bootstrap difference for `sot105_only` versus `current_v4`:
    point +15.21, 95% interval -13.87 to +44.03.
  - promotion decision: keep `current_v4`; the challenger point estimate is
    positive but not robust enough to pass a positive-lower-bound gate.
- Trust `team_count_ge/sot`, `team_more_count_than/sot`, and offsides more than
  the crowd. For total-SOT markets, use the global asymmetric field policy
  rather than full trust in the raw model.
- Count no-field fallback:

  ```bash
  uv run --with requests python scripts/count_no_field_policy_backtest.py --sports-predict --write
  ```

- Latest report: `data/logs/count_no_field_policy_backtest.md`.
- This answers the same operational question as the player no-field fallback,
  but for count rows when no UI field average is visible. On 256 safe settled
  count rows, the script scored only rows after 60 prior settled count rows,
  leaving 193 chronological holdout rows:
  - `card_threshold_stat_base_else_raw`: +88.69 RBP vs raw, Brier 0.2263 vs
    raw 0.2309. This predeclared split excludes card head-to-head/allocation
    markets and only changes card threshold rows.
  - `cards_family_base_else_raw`: +64.47 RBP vs raw, Brier 0.2276 vs raw
    0.2309.
  - visible field context, for comparison only: +41.46, Brier 0.2288.
  - raw model: 0.00, Brier 0.2309.
  - match-bootstrap for `card_threshold_stat_base_else_raw_minus_raw_model`:
    95% interval -65.83 to +245.94, Pr(>0)=0.877.
  - sensitivity grid: 25/25 cutoff/smoothing configurations were positive and
    ranked `card_threshold_stat_base_else_raw` first, with worst tested delta
    +60.63 RBP and median +105.54 RBP.
- Promotion decision: no count no-field fallback is promoted. The point
  estimate suggests card no-field monitoring is worthwhile, but the interval is
  too wide and the family split shows mixed card behavior. Keep raw count
  probabilities when the field is unavailable unless an exact current
  Polymarket corner-count market clears the stricter direct gate, then apply
  the validated field-relative count policy if the UI field average becomes
  visible.
- Treat card thresholds cautiously. Lenient referees can reduce card means; do
  not inflate for strict referees because that was not validated.
- Current referee availability audit:

  ```bash
  uv run --with pyyaml python scripts/referee_research_audit.py --write
  ```

  Latest report: `data/logs/referee_research_audit.md`. The 2026-06-28 run
  found ESPN summary payloads for all 8 currently open matches with card-like
  markets, 16 card-like markets total, no official/referee fields, and no local
  overrides for those matches. Promotion decision: no live referee adjustment is
  promoted. Keep no-field card rows on the neutral card model unless a current
  verified referee plus researched cards-per-game estimate appears; if the
  field is visible for card-count markets, use the field.
- Card-field component audit:

  ```bash
  uv run --with requests python scripts/card_field_component_audit.py --sports-predict --write
  ```

  Latest report: `data/logs/card_field_component_audit.md`. The read-only
  SportsPredict-settled run has 39 settled card rows, 22 settled
  card-threshold rows, 16 current open card-like markets, and 11 current
  field-required card-count rows. Field beat raw by +157.01 RBP on all card
  rows, +74.12 in chronological holdout, with lower bound +26.64; on
  card-threshold rows only, field beat raw by +127.96 with lower bound +20.47.
  The incremental card-field component inside the full count policy still has
  lower bound -47.85, and the no-field card-threshold fallback still has lower
  bound -69.82. Decision: visible card-field capture is high priority, but
  no broader or no-field card transform is promoted.
- If field is visible for card count markets, use the field unless a direct
  card-market price supports a lower probability.
- Do not promote category-specific shrinkage; it failed one-match-ahead testing.

Player props:

- Highest-risk family.
- Current diagnostic command:

  ```bash
  uv run --with pandas --with numpy --with scipy --with pyarrow --with pyyaml --with requests --with statsmodels \
    python scripts/player_prop_diagnostics.py --sports-predict --all-open --write --anchor-template
  ```

- Latest report: `data/logs/player_prop_diagnostics.md`.
- Exact prop-odds template: `data/player_prop_anchors_template.csv`.
- Default selection now excludes matches that have already kicked off, with a
  10-minute grace window; `--include-started` is reserved for post-hoc audits.
  Use `--local-date YYYY-MM-DD` for a single local slate or `--match` for an
  exact match.
- Latest all-open future template: 23 selected player rows, 8 high-priority
  research rows, and 15 medium-priority rows. Until reliable current prop odds
  are filled, those unresolved rows stay governed by visible-field/player-flag
  policy, exact current prop anchors, exact same-match Polymarket player-prop
  anchors that clear the separate volume gate, or the no-field fallback below
  rather than blind raw player priors.
- The player-prop diagnostic now includes a Polymarket candidate audit. The
  latest all-open audit found 252 same-player Polymarket prop candidates, but
  only 1 eligible exact player-prop anchor and 14 exact-prop near misses below
  the `$1,000` volume floor; the remaining 237 candidates were wrong family or
  wrong threshold for the SportsPredict question. These near misses are useful
  source-review leads, not automatic manual-entry probabilities.
- The current open-player table now prints `raw_model`, `current_anchor`,
  `model`, and `manual`. `raw_model` is the player-model probability;
  `current_anchor` is an exact same-match Polymarket player-prop anchor when
  one clears the volume gate; `model` is the probability that feeds the manual
  policy; `manual` applies the same no-field player fallback and
  source/threshold guardrails used by `full_manual_pricing.py`. For no-field
  manual execution, enter `manual`, not the raw model, unless exact prop odds
  or a visible field average supersede it. Exact Polymarket player anchors
  clear stale source/minutes flags but not lineup recheck risk.
- Manual pricing now runs the player model with volatile global overrides
  disabled. Season-rate overrides such as SOT/match and goal share still feed
  the model, but global `expected_minutes` and opponent-specific
  `scorer_prop_prob` values are ignored unless a current pulled lineup or exact
  current prop anchor supplies them. They remain visible as
  `GLOBAL_MINUTES_UNCONFIRMED` or `SCORER_PROP_RECHECK` flags. This was added
  after the all-open audit showed Folarin Balogun's future USA/BIH scorer row
  would otherwise inherit a stale 20-minute/10% setting from a prior match; the
  manual-safe model is now 47% with both recheck flags.
- Manual-only structured prior research:

  ```bash
  uv run --with pandas --with numpy --with scipy --with pyarrow --with pyyaml --with requests --with statsmodels \
    python scripts/player_prior_research.py --write
  ```

- Latest report: `data/logs/player_prior_research.md`.
- The 2026-06-28 pass first fixed the API-Football identity matcher so
  `Sørloth`/`Ødegaard` transliterate correctly, `Ivory Coast`/`Côte d'Ivoire`
  aliases match, and surname-only/near-name hits are rejected. It accepted two
  structured overrides:
  - Ermedin Demirović: 68 apps, 1.309 SOT/match, 0.309 goals/match, 0.441 G+A/match.
  - Nestory Irankunda: 37 apps, 0.162 SOT/match, 0.162 goals/match, 0.243 G+A/match.
- It rejected Alexander Sørloth, Amad Diallo, and Martin Ødegaard because the
  API still did not return acceptable profile/stat matches. Those rows remain
  `PRIOR_ONLY` and should use exact current prop odds or visible-field fallback
  until auditable evidence exists.
- No-field player fallback:

  ```bash
  uv run --with requests \
    python scripts/player_no_field_policy_backtest.py --sports-predict --write
  ```

- Latest report: `data/logs/player_no_field_policy_backtest.md`.
- On 80 safe settled player rows, the no-field backtest scored only rows after
  at least 30 prior settled player rows and used no field information. On the
  49-row chronological holdout:
  - `raw_scores_else_global_base`: +305.36 RBP vs raw player model, Brier
    0.2063, mean probability 0.282.
  - `prior_global_base`: +300.20, Brier 0.2073.
  - visible field context, for comparison only: +176.20, Brier 0.2326.
  - raw model: 0.00, Brier 0.2686.
  - match-bootstrap for `raw_scores_else_global_base_minus_raw_model`: 95%
    interval +137.18 to +478.82, Pr(>0)=1.000.
  - sensitivity grid: 25/25 cutoff/smoothing configurations were positive
    versus raw, with worst tested delta +130.42 RBP and median +255.43 RBP.
- Promotion: when no field average and no exact current player-prop CSV anchor
  are available, keep raw `player_scores` probabilities, but use the current
  safe-settlement global player base only for validated non-scorer support:
  `player_scores_or_assists` and 1+ SOT rows. The 2026-06-28 daily run computed
  a 28% base from 80 settled player rows. The chronological holdout has no 2+/3+
  SOT threshold rows, so higher-threshold player SOT props keep the raw model
  unless visible field or exact prop odds are available. This is still
  subordinate to current prop odds, visible field averages, and confirmed lineup
  information: confirmed-bench rows use the reduced minutes-adjusted model, and
  posted-lineup unmatched rows require manual verification instead of the global
  no-field base. The current source-proxy split is 46 `override`, 3
  `statsbomb`, and 0 `prior` holdout rows, so current `PRIOR_ONLY` or
  `TEAM_UNKNOWN` no-field rows now stay raw under
  `research_required_no_field_source_unvalidated` until exact prop odds,
  visible field, or structured player research clears the flag.
- Player prop boundary audit:

  ```bash
  uv run --with pandas --with numpy --with scipy --with pyarrow --with pyyaml --with requests --with statsmodels \
    python scripts/player_prop_boundary_audit.py --sports-predict --write
  ```

- Latest report: `data/logs/player_prop_boundary_audit.md`.
- The read-only SportsPredict-settled run has 80 settled player rows and
  23 current open player props. It confirms the no-field base is strong inside
  tested support (+305.36 RBP, lower bound +137.49), but the chronological
  holdout has 0 higher-threshold SOT rows. The researched-player asymmetric
  policy does not directly beat field with a positive lower bound
  (`asym075_minus_field` ci_2.5 -78.94), and above-field player model rows were
  historically dangerous (field-minus-raw +76.02 RBP, lower bound +25.60).
  Current board context: 3 source-unvalidated rows, 7 high-threshold SOT rows,
  and 1 exact player-prop anchor. Decision: use field or exact prop odds for
  unresolved/source-poor/high-threshold rows; apply the no-field base only
  inside tested support.
- Player policy backtest:

  ```bash
  uv run --with pandas --with pyyaml --with requests \
    python scripts/player_policy_backtest.py --sports-predict --write
  ```

- Latest report: `data/logs/player_policy_backtest.md`.
- Player lineup-status reconstruction:

  ```bash
  uv run --with requests \
    python scripts/player_lineup_status_backtest.py --sports-predict --write
  ```

- Latest report: `data/logs/player_lineup_status_backtest.md`.
- ESPN historical summary rosters classified all 80 safe settled player rows
  across 50 matched match contexts, with zero missing contexts:
  - bench rows: 20, submitted-model skill vs field -2.07, model Brier 0.1672
    vs field 0.1662.
  - starter rows: 60, submitted-model skill vs field -125.13, model Brier
    0.2412 vs field 0.2203.
  - overall policy replay still favored `player_asym075_cap10`: +166.79 RBP vs
    submitted, Brier 0.2018. A starter-only guardrail (`starter_asym_else_field`)
    was worse at +142.64 RBP, and pure field was +127.20 RBP.
  - interpretation: lineups are mandatory operational information, but the
    promoted rule remains the capped asymmetric player field policy. Confirmed
    bench/not-listed status should block model-above-field entries and trigger
    fresh research; it should not by itself replace the validated player policy.
- Recent role audit:

  ```bash
  uv run --with requests --with pyyaml --with pandas --with numpy --with scipy --with statsmodels --with pyarrow \
    python scripts/player_role_research_audit.py --write
  ```

- Latest report: `data/logs/player_role_research_audit.md`.
- This checks recent ESPN roster roles for currently open player props. The
  2026-06-29T01:10Z refresh wrote exact SportsPredict `market_id`,
  `role_class`, and recent-role strings for 23 open player rows, allowing
  `live_market_risk_audit.py`, `manual_research_queue.py`, and
  `manual_evidence_coverage.py` to attach role context without fuzzy question
  joins. It is an operational warning layer only; it does not write overrides.
- Recent role policy backtest:

  ```bash
  uv run --with requests --with pyyaml \
    python scripts/player_role_policy_backtest.py --sports-predict --write
  ```

- Latest report: `data/logs/player_role_policy_backtest.md`.
- The narrow challenger `benchrisk_field_else_asym` beat the promoted player
  policy by +17.95 RBP in full sample and +17.95 after 30 prior rows, but the
  match-bootstrap interval crossed zero (-26.35 to +68.31, Pr(>0)=0.770). Do
  not promote a role-history probability rule. Use role history to prioritize
  final-lineup/prop-odds/field checks, especially for
  `bench_or_rotation_risk` rows.
- Current queue impact: 5 medium-priority player rows are separated into
  `player_role_risk` rather than generic player recheck, while 10 remain
  generic `player_recheck` and 8 hard-flag rows remain high priority.
- Settled player layer, 80 safe exact rows:
  - `player_sot_ge/sot`: -75.77 skill vs field, Brier 0.2406 vs field 0.2241.
  - `player_scores_or_assists`: -62.69 skill vs field, Brier 0.2225 vs field 0.1877.
  - `player_scores`: +5.13 skill vs field, but only 6 rows.
  - `player_sot_ge/sot/2h`: +6.13 skill vs field, but only 10 rows.
- Player-only field-policy results:
  - `asym075_cap10`: +166.79 RBP vs current on 80 player rows, Brier 0.2018.
  - chronological holdout after 30 prior settled player rows: +171.86 RBP on
    49 player rows. Pure field was slightly higher in this player-only holdout
    (+176.20), but the combined policy backtest still favored the current
    player asymmetric rule over a `combined_player_field` challenger.
  - match-bootstrap 95% interval: +15.71 to +340.15 RBP.
  - direct `asym075_cap10_minus_field`: +39.59 RBP in full sample, -4.34 in
    chronological holdout, match-bootstrap -70.90 to +146.50, Pr(>0)=0.751.
    This is not strong enough to claim that the cap robustly beats field by
    itself; the reason not to switch to all-player-field is that the combined
    policy and prize-threshold simulations still favor the current researched
    player rule.
  - interpretation: the edge is not "trust the player model"; it is "never go
    above field on players, and only keep a capped part of below-field model
    disagreement after research clears operational flags."
- Confirm lineups in the last hour.
- Bench -> 20 minutes; ruled out -> 0 minutes.
- Use actual scorer/prop-market odds when available and current for that
  opponent; fill them in `data/player_prop_anchors_template.csv` and pass that
  file via `--player-prop-csv`. Stale player props are not valid evidence.
- Do not push player props above field unless a current market price supports it.
- When field is visible for a researched player prop, use the player-specific
  manual rule:

  ```text
  if model >= field:
      manual = field
  else:
      manual = field - min(10 points, round(0.75 * (field - model)))
  ```

- When a player prop still has `PRIOR_ONLY`, `TEAM_UNKNOWN`,
  `GLOBAL_MINUTES_UNCONFIRMED`, or `SCORER_PROP_RECHECK`, use the field until
  targeted research or confirmed lineups clears the flag.
- If a player market contains an explicit parenthetical team, the parser now
  preserves it as a side hint for player pricing. This avoids the prior-only
  fallback using match-average team strength for markets like `Erling Haaland
  (Norway)` or `Amad Diallo (Ivory Coast)`. It is not enough to clear
  `PRIOR_ONLY`; those rows still require targeted player research.

Penalty/red:

- Keep tournament base rate unless a real penalty/red market exists.
- Dedicated guardrail command:

  ```bash
  uv run --with requests python scripts/penalty_red_policy_backtest.py --sports-predict --write
  ```

- Latest report: `data/logs/penalty_red_policy_backtest.md`.
- On 24 safe settled penalty/red rows, pure field lost -5.28 RBP versus current,
  while the global asymmetric policy gained +0.82 RBP.
- Direct global-asym-minus-field comparison: +6.10 RBP in full sample, -0.19
  in chronological holdout, match-bootstrap -1.84 to +14.26, Pr(>0)=0.932.
- Direct global-asym-minus-current comparison: +0.82 RBP in full sample, +4.08
  in chronological holdout, match-bootstrap -11.19 to +12.41, Pr(>0)=0.539.
- Cutoff sensitivity: global asymmetric policy is positive in 6/6
  chronological cutoffs, with minimum +2.75 RBP, but it is the top policy in
  only 1/6 cutoffs.
- No referee-specific penalty/red transform is currently validated. Treat this
  as a sparse low-event guardrail, not a strong family edge.

Knockout special markets:

- Implemented in `src/models/specials.py` and exercised by
  `scripts/full_manual_pricing.py`.
- Validated with:

  ```bash
  uv run --with numpy --with scipy python scripts/special_market_backtest.py --write
  ```

- The report is `data/logs/special_market_backtest.md`.
- Evidence base: 314 StatsBomb international tournament matches across AFCON
  2023, Copa 2024, Euro 2020, Euro 2024, WC 2018, and WC 2022.
- Leave-one-tournament-out base-rate checks with Wilson uncertainty:

| family | hit rate | Wilson 95% | OOF Brier | edge vs 50 | confidence |
| --- | ---: | --- | ---: | ---: | --- |
| goal before first hydration | 0.439 | 0.386-0.495 | 0.2493 | +0.0007 | weak_near_coinflip |
| goal after second hydration | 0.452 | 0.398-0.508 | 0.2487 | +0.0013 | weak_near_coinflip |
| first-half stoppage goal | 0.070 | 0.047-0.104 | 0.0653 | +0.1847 | usable_base_prior |
| second-half stoppage goal | 0.207 | 0.166-0.255 | 0.1654 | +0.0846 | usable_base_prior |
| offside before first hydration | 0.627 | 0.573-0.679 | 0.2354 | +0.0146 | usable_base_prior |
| card after second hydration | 0.656 | 0.602-0.706 | 0.2265 | +0.0235 | usable_base_prior |
| substitution before halftime | 0.178 | 0.140-0.224 | 0.1473 | +0.1027 | usable_base_prior |
| substitute goal | 0.271 | 0.225-0.322 | 0.1998 | +0.0502 | usable_base_prior |
| any player brace | 0.162 | 0.126-0.207 | 0.1389 | +0.1111 | usable_base_prior |
| any player 2+ SOT | 0.707 | 0.654-0.755 | 0.2084 | +0.0416 | usable_base_prior |

- Count-threshold special priors were added for the currently exposed total
  shot and early-corner questions. These are no-match-info priors, not a
  validation of live count-volume scaling:

| key | hit rate | Wilson 95% | OOF Brier | edge vs 50 | confidence |
| --- | ---: | --- | ---: | ---: | --- |
| 20+ total shots | 0.736 | 0.684-0.781 | 0.1994 | +0.0506 | usable_base_prior |
| 22+ total shots | 0.618 | 0.563-0.670 | 0.2432 | +0.0068 | usable_base_prior |
| 2+ corners before first hydration | 0.701 | 0.648-0.749 | 0.2129 | +0.0371 | usable_base_prior |

- The 2026-06-28 report also validates the hazard-scaling shape used by
  `SpecialPricer` with a leave-one-tournament-out realized-volume diagnostic.
  This is not an ex-ante betting backtest because it uses the held-out match's
  realized total goals/cards/offsides/SOT as the volume input, but it answers
  whether scaling probability with match volume is directionally correct.
  Every scaled family beat its constant held-out tournament baseline:

| family | volume | constant Brier | scaled oracle-volume Brier | delta |
| --- | --- | ---: | ---: | ---: |
| goal before first hydration | goals | 0.2493 | 0.1964 | -0.0529 |
| goal after second hydration | goals | 0.2487 | 0.2013 | -0.0475 |
| first-half stoppage goal | goals | 0.0653 | 0.0611 | -0.0042 |
| second-half stoppage goal | goals | 0.1654 | 0.1480 | -0.0174 |
| offside before first hydration | offsides | 0.2354 | 0.1835 | -0.0519 |
| card after second hydration | cards | 0.2265 | 0.1707 | -0.0558 |
| substitute goal | goals | 0.1998 | 0.1623 | -0.0375 |
| any player brace | goals | 0.1389 | 0.1178 | -0.0211 |
| any player 2+ SOT | SOT | 0.2084 | 0.1523 | -0.0560 |

- The latest report now adds reliability diagnostics:
  - Constant leave-one-tournament-out base priors are calibrated at the family
    level: most bins have absolute calibration error near 0.001-0.002; the
    main exception is second-half stoppage goals, where the 5-bin ECE is 0.040.
  - Oracle-volume scaling improves Brier for every scaled family, but it is not
    uniformly calibrated by bin: offside-before-30 has ECE 0.072, card-after-75
    has ECE 0.067, and any-player 2+ SOT has ECE 0.114 under realized-volume
    scaling. This supports directional volume adjustment but rejects treating
    the scaling layer as a separately validated aggressive edge.
- Tournament heterogeneity diagnostics are now logged. Among the 13 special
  evidence keys, 2 are `high` heterogeneity, 7 are `moderate`, and 4 are `low`.
  The high-heterogeneity keys are total-shots thresholds: 22+ shots ranges from
  0.469 to 0.781 across source tournaments, and 20+ shots ranges from 0.625 to
  0.891. This is a transfer-fragility warning for total-shots special rows, not
  a probability transform.
- Ex-ante volume replay availability: not available from the current
  StatsBomb cache. The raw event files contain realized goals/cards/offsides/SOT
  but no historical pre-match odds or projected count means; a no-match-info
  proxy equals the training mean and collapses to the same probability as the
  constant leave-one-tournament-out prior. This is now logged in
  `data/logs/special_market_backtest.md` so oracle-volume evidence is not
  mistaken for deployable ex-ante edge.
- Chronological in-tournament adaptation replay: also not promoted. This replay
  uses a true ex-ante proxy for "the tournament so far looks high/low event":
  for each held-out tournament, estimate the global prior from all other
  tournaments, then blend it with only earlier matches in the same held-out
  tournament. Simultaneous kickoffs are scored as one batch before updating.
  Candidate strengths 8, 16, 32, 64, and 128 were tested. The best all-match
  point estimate was strength 128 with delta -0.0002 Brier versus the constant
  prior, but tournament-bootstrap uncertainty crossed zero (ci_2.5 -0.0008,
  ci_97.5 +0.0003). The knockout-only replay was worse: best strength 128,
  delta +0.0003, ci_2.5 -0.0004, ci_97.5 +0.0009. Current-tournament special
  trends should be treated as a research warning, not an automatic probability
  adjustment.
- Interpretation: hydration-goal timing props are weak, near-coin-flip priors.
  Stoppage/substitution/brace/card-after-75/offside-before-30 priors have more
  stable historical signal. These are still lower-confidence than odds-anchored
  win/goal markets because they are not directly market-priced. The scaling
  diagnostic supports adjusting these priors for match goal/count volume, but
  the ex-ante quality still depends on the live goal/count models used as volume
  inputs and is not separately validated on historical pre-match odds. The
  current open-special boundary audit counts 14 `usable_base_prior` markets and
  6 `weak_near_coinflip` markets. The leaderboard tail-risk special-proxy run
  supports `raw_special_model`: use the raw special model rather than the
  generic global field transform for timing/special rows.
- The live special audit is now a separate row-level execution check:

  ```bash
  uv run --with pandas --with numpy --with scipy --with pyarrow --with pyyaml --with requests --with statsmodels python scripts/special_market_live_audit.py --all-open --write
  ```

  Latest report: `data/logs/special_market_live_audit.md`. It maps each open
  special question to the binary or count-threshold evidence key and labels
  risk without changing probabilities. The 2026-06-28 all-open run has 20
  future special rows after excluding started matches: 10 `BASE_PRIOR`, 2
  `COUNT_THRESHOLD_HIGH_HETEROGENEITY`, 1 `COUNT_THRESHOLD_PROXY`, 1
  `COUNT_THRESHOLD_SCALED_FAR_FROM_BASE`, and 6 `WEAK_PRIOR`. The current
  high-heterogeneity transfer rows are USA/BIH and ARG/CPV 22+ total shots.
  The far-from-base warning is AUS/EGY 20+ total shots: the live entry is 33%
  versus a 73.6% no-match-info StatsBomb base rate. The refreshed audit logs
  signed `model_minus_base_pp`, and the research queue promotes this row to the
  `special_count_far_from_base` high-priority bucket, so count-volume inputs
  need a near-lock recheck before manual entry.
- The boundary audit is the current special-family promotion guardrail:

  ```bash
  python3 scripts/special_boundary_audit.py --write
  ```

  Latest report: `data/logs/special_boundary_audit.md`. It records
  `raw_proxy_with_guardrails_no_shrink_no_official_promotion`, with 20 open
  special rows, 5 official settled rows, 6 weak-prior rows, 3 high-heterogeneity
  or far-from-base count-threshold rows, and a 40-point maximum live
  scaled-vs-base shift. This keeps special rows proxy-only and prevents
  treating shrinkage, current-tournament trends, or catch-up transforms as
  optimized strategy.
- Count conversion parameters now used by the pipeline:
  shots/SOT `3.161`, total-shots NB alpha `0.022`, early-corner share `0.295`,
  early-corners alpha `0.048`, extra-time goal factor `0.758`, extra-time card
  factor `1.434`.

Player-name and lineup hygiene:

- Parenthetical team labels in knockout markets are stripped before player lookup
  (`Kylian Mbappe (France)` -> `Kylian Mbappe`).
- The parenthetical team is retained as `player_team` metadata and used for
  side/team-tempo resolution when player history or overrides do not identify a
  team.
- The player resolver now accepts a conservative unique-token expansion so
  `Kylian Mbappe` can match StatsBomb's `Kylian Mbappe Lottin`.
- This reduces a real prior-fallback leak in player props, but it does not solve
  stale role/minutes risk. Confirm lineups and current prop odds before lock.

Live execution-risk audit:

- Latest command:

  ```bash
  uv run --with pandas --with numpy --with scipy --with pyarrow --with pyyaml --with requests --with statsmodels \
    python scripts/live_market_risk_audit.py --all-open --write --field-template
  ```

- Latest report: `data/logs/live_market_risk_audit.md`.
- Latest fillable field template: `data/field_averages_template.csv`.
- The default `--field-template` export is now an actionable exact-ID field
  template rather than a full market dump. It keeps rows where a visible field
  average can change the documented manual policy, and excludes raw odds-goal
  rows, raw special priors, and strong SOT count rows. Use
  `--field-template-mode all` only when a diagnostic full-row export is needed.
- The `2026-06-29T00:53Z` all-open execution-leverage audit classified 120
  currently exposed markets as 20 high priority, 32 medium, 36 monitor, and
  32 low.
- High-risk markets are not model-optimization opportunities by themselves.
  They are execution gates: unresolved player props need current prop/lineup
  research, an exact `--player-prop-csv` market anchor, or visible-field
  fallback, and card-count rows need the field average because the
  safe-settlement count backtest promotes field-following for card counts.
- Fill the template's `field` column from the UI before lock and pass it back as
  `--field-csv data/field_averages_template.csv`. The pricing reader rejects
  blank retained rows by design, so the audit either proves complete field
  coverage for the selected slate or forces an explicit partial-slate decision.
- Low-risk markets are mostly raw odds-anchored goal/outcome rows and promoted
  strong-SOT count rows. Rerun those if public odds move materially; do not
  field-transform ordinary goal markets.

## Rejected Ideas

- Extremizing to catch up: invalid under Brier and negative in live tests.
- Flat 50: catastrophic.
- Category-aware one-match-ahead policy mining: underperformed simple fixed
  policy.
- Broad post-calibration of the offline model: no OOF Brier improvement.
- Global post-calibration of the promoted manual policy: rejected by
  `data/logs/manual_calibration_backtest.md`. A full-sample factor of 1.10
  looked marginally best, but the chronological walk-forward calibration layer
  lost -21.80 RBP versus uncalibrated `combined_manual_policy` on 401 holdout
  rows, with match-bootstrap interval -62.98 to +17.26 and Pr(>0)=0.126.
  The refreshed reliability diagnostics tell the same story: full-sample
  `combined_manual_policy` Brier is 0.2179 with 7-bin ECE 0.041, while
  walk-forward calibration worsens holdout Brier from 0.2193 to 0.2198 and ECE
  from 0.054 to 0.063. Do not globally extremize or shrink the promoted policy.
- Automatic in-tournament special-market adaptation: rejected for now. The
  chronological leave-one-tournament-out replay in
  `data/logs/special_market_backtest.md` found no robust Brier gain, and the
  knockout-only subset was directionally worse.
- Raising live SOT count intensity from 1.00 to 1.05: rejected for now. The
  narrow `sot105_only` challenger leads the count manual-policy replay point
  estimate by +15.21 RBP versus `current_v4`, but its match-bootstrap interval
  is -13.87 to +44.03, so it fails the positive-lower-bound promotion gate.
- Strict-referee upward card inflation: not validated; keep the asymmetric card
  cap.

## Remaining Work

This review improves the manual strategy layer, but it does not prove every
underlying model parameter is globally optimal. The next high-value work is:

1. Extend the count-intensity replay only if exact archived Polymarket,
   referee, and intraday odds snapshots become available. The current replay is
   useful directional evidence, not a perfect time-machine reconstruction.
2. Populate exact current `--player-prop-csv` anchors for high-risk player rows
   in `data/player_prop_anchors_template.csv` whenever a reliable two-sided or
   de-vigged prop quote is available. Future automation should fetch these from
   an auditable odds feed; until then, do not promote manually scraped one-sided
   quotes into the model.
3. For knockout rounds, re-check count levels after the first 8-12 KO matches
   settle; do not refit earlier unless the signal is large enough to overcome
   small-sample risk.
