# Jump Probability Cup Manual Research System

This repository prices SportsPredict Probability Cup markets for the 2026 World
Cup. It contains bot tooling, but the current research workflow is for the
manual leaderboard entry.

Manual-entry rule: do not read stored bot predictions, do not submit through the
API, and do not use bot values as inputs. The API key in `.env` is for read-only
GET calls needed to discover markets and settled results.

## Contest Mechanics

SportsPredict Probability Cup markets are binary yes/no questions. Entries are
integer probabilities from 1 to 99, submitted before the market close time,
which is usually kickoff. The latest value before close is scored.

Scoring is Brier:

```text
Brier = (p - outcome)^2
RBP = (crowd_brier - user_brier) * 100
```

Stage weighting applies: group stage 1x, knockout 2x, final 3x. Weighting
changes the value of a market, not the optimal probability. Since Brier is a
strictly proper scoring rule, the target is the best calibrated probability.
Intentional extremizing or catch-up shading is rejected unless the objective is
explicitly changed away from expected RBP.

Primary rule source: `docs/API-Documentation.pdf`.

## Entries

| entry | how it is run | current rule |
| --- | --- | --- |
| bot 1 | API submission pipeline | Do not touch for manual work. |
| bot 2 | API submission pipeline / experiment | Do not touch for manual work. |
| manual | human entry on the website | Use this repo's manual strategy and logs. |

The manual workflow may call SportsPredict read-only endpoints:

- `GET /events`
- `GET /lobbies`
- `GET /matches`
- `GET /markets`
- `GET /results`

It must not call or rely on `/predictions`.

## What To Read First

| file | purpose |
| --- | --- |
| `docs/manual_strategy.md` | Current manual-entry operating policy. |
| `docs/strategies.md` | Strategy catalog: promoted, limited, guardrail, proxy, rejected. |
| `docs/full_research_program.md` | Full no-shortcuts research plan, method backlog, and deferred low-value tasks. |
| `docs/methodology_sources.md` | Literature, source hierarchy, and promotion gates. |
| `data/logs/strategy_evidence_matrix.md` | Current status of every strategy rule. |
| `data/logs/brier_decomposition_audit.md` | Reliability/resolution diagnostic for calibration warnings. |
| `data/logs/evidence_freshness_audit.md` | Whether evidence artifacts are internally consistent. |
| `data/logs/manual_research_queue.md` | Ranked live worklist for player, card, current-source, and special rows. |
| `data/logs/match_lock_readiness_audit.md` | Match-level lock checklist built from row-level evidence coverage. |
| `data/logs/manual_predictions.md` | Append-only manual pricing log. |
| `data/logs/goal_completion_audit.md` | Requirement-by-requirement status for the full manual research goal. |

## Manual Pipeline

One-command audited refresh:

```bash
python3 scripts/manual_research_refresh.py --profile full --local-date YYYY-MM-DD --espn-date YYYYMMDD --max-seconds 28800 --command-timeout-seconds 900
```

The refresh runner is an orchestrator over the existing read-only scripts. It
does not read `/predictions`, does not call submission code, and writes an
audit log to `data/logs/manual_research_refresh.md`. The log is checkpointed
after every command, so an interrupted long refresh still leaves a partial
record of PASS/FAIL/SKIPPED/TIMEOUT results. Use `--dry-run` to inspect the
exact command plan without executing anything. Profiles:

| profile | purpose |
| --- | --- |
| `live` | open-board pricing, field diagnostic, player/special source checks, and readiness reports |
| `evidence` | historical/proxy backtests plus readiness reports |
| `readiness` | cheap downstream coverage, lock, packet, strategy, and freshness checks |
| `full` | live + evidence + readiness |

Freshness-sensitive dependencies are handled inside the runner. In particular,
`polymarket_direct_snapshot_score.py` is part of the evidence profile, and the
readiness phase rebuilds `manual_calibration_watchlist.py` plus
`card_field_component_audit.py` after their upstream open-board, referee, and
execution-leverage inputs. This ordering is required for
`evidence_freshness_audit.py` to be a meaningful gate.

The `--max-seconds 28800` option is the eight-hour guardrail for the full
research refresh. It skips commands that have not started after the wall-clock
budget is reached, then records that in the markdown log.
`--command-timeout-seconds` is a per-script guardrail for hung live/API/source
checks; a timed-out command is recorded as `TIMEOUT` and makes the runner exit
nonzero. A successful runner exit does not mean a match is final;
`match_lock_readiness_audit.py` and
`manual_submission_packet_audit.py` remain authoritative for lock status.

The full manual pricing command is:

```bash
uv run --with pandas --with numpy --with scipy --with pyarrow --with pyyaml --with requests --with statsmodels \
  python scripts/full_manual_pricing.py --local-date YYYY-MM-DD
```

Field averages are not visible before the prediction lock in the current
SportsPredict UI. Do not wait for them, do not fabricate them, and do not pass
a `--field-csv` for pre-lock manual entries. The field-capture commands below
remain diagnostic only unless the UI behavior changes:

```bash
uv run --with pandas --with numpy --with scipy --with pyarrow --with pyyaml --with requests --with statsmodels \
  python scripts/live_market_risk_audit.py --all-open --write --field-template

uv run --with pandas --with numpy --with scipy --with pyarrow --with pyyaml --with requests --with statsmodels \
  python scripts/manual_field_capture_audit.py --write
```

`manual_field_capture_audit.py` is intentionally strict, but pre-lock
`INCOMPLETE` is expected because field values are unavailable before lock.
For manual entries, use the no-field/current-source values from
`full_manual_pricing.py`; the readiness audits label affected card/count rows
as `FIELD_UNAVAILABLE_PRELOCK` rather than as missing actionable inputs.

With researched exact player prop anchors:

```bash
uv run --with pandas --with numpy --with scipy --with pyarrow --with pyyaml --with requests --with statsmodels \
  python scripts/player_prop_diagnostics.py --sports-predict --all-open --write --anchor-template --player-prop-csv data/player_prop_anchors_template.csv

python3 scripts/current_player_stats_audit.py --start-date 2026-06-11 --end-date YYYY-MM-DD --sports-predict --write

uv run --with requests \
  python scripts/current_player_stats_replay.py --start-date 2026-06-11 --end-date YYYY-MM-DD --sports-predict --write

uv run --with pandas --with numpy --with scipy --with pyarrow --with pyyaml --with requests --with statsmodels \
  python scripts/lineup_source_audit.py --sports-predict --all-open --write

uv run --with pandas --with numpy --with scipy --with pyarrow --with pyyaml --with requests --with statsmodels \
  python scripts/full_manual_pricing.py --local-date YYYY-MM-DD --player-prop-csv data/player_prop_anchors_template.csv
```

`player_prop_diagnostics.py` reports `player_prop_anchor_status`. Treat
`INCOMPLETE` as an explicit research queue: high-priority player rows still
need exact current prop odds, lineup/role confirmation, or structured player
research before lock. It also reports `current_prop_anchor_status`, which is separate from the
manual CSV: this tells you how many exact same-match Polymarket player-prop
anchors cleared the live volume/semantic gates. A blank manual CSV can
therefore coexist with usable automatic current anchors, but those anchors do
not clear lineup recheck risk. `current_player_stats_audit.py` reports
`current_player_stats_status` from ESPN public summary roster stats for open
player props. It is a cross-check and research source, not a replacement for
exact prop odds or confirmed lineups.
`current_player_stats_replay.py` is the promotion gate for that source. The
2026-06-29 replay is point-positive but not bootstrap-robust, so current-player
stats remain a disagreement flag instead of an automatic probability override.
`lineup_source_audit.py` reports `lineup_source_status` from ESPN confirmed-XI
availability for every open player-prop match. `PENDING_NOT_IN_WINDOW` is
normal before the final pre-match window; `LINEUP_NOT_POSTED_IN_PULL_WINDOW`,
`LINEUP_SOURCE_ISSUES`, or posted-lineup unmatched players block player-prop
lock unless exact prop odds or structured role/player research supersedes them.

For special count-threshold rows such as total shots and early corners:

```bash
uv run --with pandas --with numpy --with scipy --with pyarrow --with pyyaml --with requests --with statsmodels \
  python scripts/special_market_live_audit.py --all-open --write --count-source-template

uv run --with requests \
  python scripts/current_tournament_stats_audit.py --start-date 2026-06-11 --end-date YYYY-MM-DD --sports-predict --write --update-special-count-csv

uv run --with requests \
  python scripts/special_count_source_discovery.py --write

python3 scripts/special_count_source_audit.py --write
```

`special_count_source_audit.py` reports `special_count_source_status`.
`INCOMPLETE` means the proxy-only count-threshold rows still lack enough
independent current count-volume evidence and should remain unresolved in the
research queue. The audit joins `special_count_source_discovery.md` and reports
when public-market discovery found no usable exact source; ordinary goal totals,
first-half corner totals, and other near misses must not be copied in as
shots/hydration evidence. `current_tournament_stats_audit.py` reports
`current_tournament_stats_status` from ESPN public box scores and may fill one
current-source slot for full-match count thresholds; it is a source check, not
an automatic probability transform. `special_count_source_discovery.py` reports
`special_count_source_discovery_status` and searches public Polymarket markets
for exact count sources. Only `EXACT_USABLE` rows may fill a current source
slot; first-half corner totals and ordinary goal totals are logged as near
misses or non-sources.

Before treating generated entries as locked:

```bash
python3 scripts/manual_evidence_coverage.py --all-open --write
python3 scripts/match_lock_readiness_audit.py --write
python3 scripts/manual_submission_packet_audit.py --write
```

If targeted research finds no usable exact source, record that negative result
in `data/manual_research_resolutions.csv` instead of leaving the row as
untouched work. The packet audit then moves that row from the nightly
`NOW_*` queue to `NEAR_LOCK_RESEARCHED_SOURCE_RECHECK`: keep the provisional
model/current-source value, but recheck lineups and exact sources before lock.

`manual_submission_packet_audit.py` reports `submission_packet_status`.
`NOT_LOCKED_REQUIRED_INPUTS` means the sheet is still provisional because hard
player or special-count gates remain unresolved. Field-unavailable rows are
low-confidence no-field rows, not missing inputs. `READY_TO_SUBMIT` is the only
fully clean packet state; `READY_WITH_LOW_CONFIDENCE_ROWS` means the packet is
usable when deadline pressure forces entry, but proxy/guardrail/no-field rows
remain explicitly labelled. The packet also reports actionability counts:
`NOW_PROP_ROLE_RESEARCH_THEN_LINEUP_RECHECK` and `NOW_COUNT_SOURCE_RESEARCH`
are the nightly/morning research queue, `NEAR_LOCK_*` rows are the pre-match
lineup/current-source recheck queue, and `NO_FIELD_ACTION_PRELOCK` is
unavoidable no-field risk that should not consume research time.

Before treating any match as final for manual entry, run:

```bash
python3 scripts/manual_evidence_coverage.py --all-open --write
python3 scripts/match_lock_readiness_audit.py --write
```

`match_lock_readiness_audit.py` reports `match_lock_decision`. A match is not
fully cleanly locked while it still has `PLAYER_RESEARCH_REQUIRED`,
`SPECIAL_COUNT_RESEARCH_REQUIRED`, or near-lock recheck rows. Rows labelled
`FIELD_UNAVAILABLE_PRELOCK` are unavoidable no-field risk, not a reason to wait
for a value the UI will not expose before lock.

Fallback when the full model stack is unavailable:

```bash
python3 scripts/manual_odds_anchor.py --date YYYYMMDD --sports-predict
```

The fallback is intentionally narrow. It can price goal/win/totals from public
odds and current open markets. It must not invent player, count, referee, card,
or special-market probabilities.

## Evidence Refresh

Before trusting the strategy matrix, run the freshness audit:

```bash
python3 scripts/evidence_freshness_audit.py --write
```

Important reports:

```bash
uv run --with requests python scripts/backtest_integrity_audit.py --sports-predict --write
uv run --with requests python scripts/manual_policy_backtest.py --sports-predict --write
uv run --with requests python scripts/brier_decomposition_audit.py --sports-predict --write
uv run --with requests python scripts/goal_policy_backtest.py --sports-predict --write
uv run --with requests python scripts/count_policy_backtest.py --sports-predict --write
uv run --with requests python scripts/player_policy_backtest.py --sports-predict --write
uv run --with numpy python scripts/odds_conversion_backtest.py --write
uv run --with numpy python scripts/player_minutes_uncertainty_audit.py --write
uv run --with pandas --with numpy --with scipy --with statsmodels --with pyarrow python scripts/count_total_dependence_audit.py --write
uv run --with pandas --with numpy --with scipy --with statsmodels --with pyarrow --with requests python scripts/count_total_dependence_policy_replay.py --sports-predict --write
python3 scripts/player_sot_rate_audit.py --write
python3 scripts/player_goal_involvement_rate_audit.py --write
python3 scripts/referee_card_adjustment_audit.py --write
python3 scripts/special_count_threshold_audit.py --write
uv run --with requests python scripts/current_tournament_stats_audit.py --start-date 2026-06-11 --end-date YYYY-MM-DD --sports-predict --write --update-special-count-csv
python3 scripts/current_player_stats_audit.py --start-date 2026-06-11 --end-date YYYY-MM-DD --sports-predict --write
uv run --with requests python scripts/current_player_stats_replay.py --start-date 2026-06-11 --end-date YYYY-MM-DD --sports-predict --write
uv run --with pandas --with numpy --with scipy --with pyarrow --with pyyaml --with requests --with statsmodels python scripts/lineup_source_audit.py --sports-predict --all-open --write
uv run --with requests python scripts/special_count_source_discovery.py --write
python3 scripts/question_family_coverage_audit.py --write
python3 scripts/strategy_evidence_matrix.py --write
python3 scripts/promotion_strength_audit.py --write
python3 scripts/evidence_freshness_audit.py --write
python3 scripts/automation_inventory_audit.py --write
python3 scripts/match_lock_readiness_audit.py --write
python3 scripts/manual_submission_packet_audit.py --write
python3 scripts/manual_workflow_readiness_audit.py --local-date YYYY-MM-DD --write
python3 scripts/goal_completion_audit.py --write
```

Latest recorded full-suite verification:

```text
uv run --with pandas --with numpy --with scipy --with pyarrow --with pyyaml --with requests --with statsmodels --with pytest python -m pytest
339 passed in 63.55s
```

Lightweight focused verification for the newest source/family-coverage gates:

```text
uv run --with pandas --with numpy --with scipy --with pyarrow --with pyyaml --with requests --with statsmodels python -m pytest tests/test_automation_inventory_audit.py tests/test_brier_decomposition_audit.py tests/test_current_tournament_stats_audit.py tests/test_current_player_stats_audit.py tests/test_lineup_source_audit.py tests/test_manual_submission_packet_audit.py tests/test_manual_workflow_readiness_audit.py tests/test_match_lock_readiness_audit.py tests/test_special_count_source_audit.py tests/test_special_count_source_discovery.py tests/test_question_family_coverage_audit.py tests/test_evidence_freshness_audit.py tests/test_polymarket_direct_coverage_audit.py tests/test_polymarket_direct_snapshot_score.py tests/test_polymarket_direct_anchor_risk_audit.py tests/test_player_prop_diagnostics.py
76 passed in 1.86s
```

Latest focused field/readiness verification run in this session:

```text
uv run --with pandas --with numpy --with scipy --with pyarrow --with pyyaml --with requests --with statsmodels --with pytest python -m pytest tests/test_live_market_risk_audit.py tests/test_manual_evidence_coverage.py tests/test_manual_research_queue.py tests/test_manual_field_capture_audit.py tests/test_manual_submission_packet_audit.py tests/test_match_lock_readiness_audit.py tests/test_manual_execution_leverage.py tests/test_manual_workflow_readiness_audit.py tests/test_referee_research_audit.py tests/test_strategy_evidence_matrix.py tests/test_promotion_strength_audit.py tests/test_evidence_freshness_audit.py tests/test_player_prop_boundary_audit.py tests/test_player_no_field_policy_backtest.py tests/test_manual_policy.py
89 passed in 7.65s
```

## Current Strategy Summary

The canonical detailed version is `docs/strategies.md`. Short version:

- Use raw odds-anchored model values for ordinary goal/outcome markets unless
  the source audit flags stale or misoriented feeds.
- Field averages are not visible before lock, so live manual entries use the
  no-field/current-source value; the validated field policy remains historical
  evidence and a diagnostic branch, not a pre-lock input.
- Use raw/neutral card-count model values unless verified referee/current
  source evidence gives a clear correction.
- Use raw model for strong team/head-to-head SOT count markets.
- Treat direct-total NB count-threshold dependence as proxy-positive research
  only; the settled Cup replay did not promote it, so do not replace
  the historical field-policy evidence, exact current anchors, or the current
  independent total-count shape.
- Use player-prop policy only inside tested support; unresolved player rows
  need exact prop odds, confirmed lineup, or structured research.
- Treat player-minutes distributions as proxy-positive research only; keep
  exact prop odds and confirmed lineups above that candidate.
- Treat referee card-rate adjustment as proxy-positive research only; it still
  requires verified current officials and independent card-rate research.
- Use StatsBomb special-market priors only as proxy evidence with guardrails.
- Treat total-shots and early-corner special-threshold adaptation as
  proxy-positive research only; do not transform live entries without current
  count-volume or official replay evidence.
- Do not use global calibration multipliers, adaptive category pooling, or
  catch-up extremizing.
- Do not change bookmaker overround conversion yet. Multiplicative de-margin
  remains live; power/Shin/odds-ratio/additive are monitored but not promoted.

## Live Execution Workflow

Night run:

1. Refresh open markets.
2. Generate next-day manual sheet.
3. Refresh odds, Polymarket anchors, player-role diagnostics, special audits,
   card/referee queue, no-field risk labels, and research queue.
4. Post recommendations and unresolved rows in the main chat.
5. Append to `data/logs/manual_predictions.md`.

Morning run:

1. Re-run the manual sheet for today's markets.
2. Compare against the night sheet.
3. Check odds movement, new exact anchors, player-prop odds,
   referee assignments, and lineup news.
4. Confirm field values are still unavailable before lock; do not fabricate them.
5. Post changes only where evidence changed.

Pre-match run:

1. Re-run shortly before kickoff.
2. Confirm lineups and player roles.
3. Check final current prop/odds anchors.
4. Confirm field values are still unavailable before lock and keep
   `FIELD_UNAVAILABLE_PRELOCK` rows as low-confidence no-field entries.
5. Post any changes before market close.

## Live Research Priorities

Highest value:

- Current exact player prop odds.
- Confirmed lineups and minutes/role risk.
- Exact Polymarket anchors with semantic and volume gates.
- Referee assignment and card rate for card-heavy rows.
- Weak special/count-threshold rows where current context can break a proxy tie.

Lower value:

- New global calibration transforms.
- Broad model blending.
- Extremizing or variance strategies.
- Unvalidated special-market shrinkage.
- Referee/card transforms without verified officials.

## Project Structure

```text
src/
  backtest/             history parsing and Brier/RBP helpers
  manual/               manual policy, anchors, Polymarket direct matching
  models/               goals, counts, players, specials, simulation
  questions/            question parser and team normalization
  wcdata/               odds, Polymarket, StatsBomb, API-Football data access
scripts/
  full_manual_pricing.py            full read-only manual sheet
  manual_research_queue.py          ranked live worklist
  live_market_risk_audit.py         open-board risk and diagnostic field template
  manual_field_capture_audit.py     diagnostic validation for field averages if UI changes
  current_tournament_stats_audit.py ESPN current-tournament count source audit
  current_player_stats_audit.py     ESPN current-tournament player source audit
  current_player_stats_replay.py    leakage-safe replay gate for player source promotion
  lineup_source_audit.py            ESPN confirmed-XI availability for player props
  manual_submission_packet_audit.py row-level lock status for generated sheets
  special_count_source_discovery.py public exact-source discovery for special counts
  special_count_source_audit.py     validates special count current-source checks
  question_family_coverage_audit.py maps observed/parser families to strategy owners
  manual_policy_backtest.py         combined manual policy replay
  brier_decomposition_audit.py      calibration reliability/resolution diagnostic
  odds_conversion_backtest.py       multiplicative/additive/power/Shin/odds-ratio/OO-EPC/FL-GLM replay
  elo_history_residual_audit.py     national-team Elo-history proxy audit
  strategy_evidence_matrix.py       promotion/rejection matrix
  evidence_freshness_audit.py       consistency checks across logs
  automation_inventory_audit.py     active/paused Codex automation inventory
  match_lock_readiness_audit.py     match-level lock checklist
  manual_workflow_readiness_audit.py manual-entry health check
  goal_completion_audit.py          full objective completion/readiness audit
  manual_research_refresh.py        audited one-command refresh runner
data/
  logs/                            generated reports and manual prediction log
  field_averages_template.csv      field-average input template
  player_prop_anchors_template.csv player prop anchor input template
docs/
  manual_strategy.md
  strategies.md
  full_research_program.md
  methodology_sources.md
  API-Documentation.pdf
```

## Environment

This checkout currently has no `pyproject.toml`, lockfile, or `.venv`.
Reproducibility currently relies on explicit `uv run --with ...` command lines,
now wrapped by `scripts/manual_research_refresh.py` for repeatable audited
runs. A pinned project environment remains a future hardening task.
