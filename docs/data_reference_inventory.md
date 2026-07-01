# Local Data Reference Inventory

`data_reference/` is a local-only research dataset used by the manual
Probability Cup workflow when the ignored `data/` directory is not available.
It is intentionally not committed because it contains bulky raw data, generated
backtest artifacts, local run logs, and SportsPredict-derived history.

The code and tests treat this directory as an optional fallback. A checkout
without `data_reference/` should still be able to run unit tests that use
synthetic fixtures; research and replay commands that need the historical
dataset will either skip those checks or require the files to be restored
locally.

## Contents

### Raw Public Reference Data

- `raw/fduk/`: historical Football-Data CSVs by season and league. These are
  used for odds conversion, count-model calibration, and referee/card research.
- `raw/statsbomb/`: StatsBomb open-data event and match JSON for international
  tournaments, including World Cup, Euros, Copa America, and AFCON samples.
  These files support player-rate audits, special-market priors, penalty/red
  baselines, stoppage/hydration timing priors, and tournament count checks.
- `raw/internationals/results.csv`: international match results used for
  history/strength residual diagnostics.

### Derived Research Tables

- `backtest_results.parquet`: offline model-validation rows used by strategy
  research and calibration audits.
- `club_matches.parquet`: normalized historical club-match table used by count
  and dependence diagnostics.
- `statsbomb_players.parquet` and `statsbomb_team_halves.parquet`: compact
  derived tables from StatsBomb event data for player and team-half models.
- `count_models.json` and `family_blend.json`: local model calibration outputs.

### SportsPredict-Derived Local History

- `snapshot.json`: local market/match snapshot used by parser coverage tests.
- `rbp_full.csv` and `rbp_analysis.csv`: settled live-history rows with
  probabilities, crowd/field context, outcomes, and relative Brier information.
- `backtest_polymarket.csv`, `backtest_report.csv`, and `analysis_today.csv`:
  local research summaries from prior runs.

### Local Workflow Inputs And Logs

- `player_overrides.yaml`, `referee_overrides.yaml`, `match_lineups.yaml`, and
  `bot2_experiment.yaml`: local research inputs and experiment state copied
  from prior workflow runs.
- `logs/`: local run records, submission-history extracts, experiment notes,
  matchday reports, and cron/test output.
- `predictions_*.csv`: small historical prediction exports used only for local
  analysis.

## Reproducibility Notes

The committed repository keeps the code, tests, and documentation needed to
rebuild or refresh this dataset. Restoring the full local research environment
requires fetching or regenerating the public Football-Data and StatsBomb inputs
and refreshing SportsPredict-derived history through read-only API calls where
permitted.

