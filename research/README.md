# Research

Every tuned constant in [`models/calibration.py`](../src/probcup/models/calibration.py) and
every measured table in the models traces back to a study here, or to the live A/B bench
([agent/logs/experiment_log.md](../agent/logs/experiment_log.md)). Each script runs on its own
(`python research/<kind>/<name>.py`) once the package is installed; most need the raw data
from `probcup download-data`. Their output, re-run on 2026-10-31, is in [outputs/](outputs/).

A change reached the live model only after a large-sample check like these. A live drift on a
handful of settled markets was treated as a hypothesis, never as evidence: three of the four
drift flags raised during the knockouts died this way.

## Validations

| Study | Question | Data | Result | Decision |
|---|---|---|---|---|
| [player_sot_loto](validations/player_sot_loto.py) | Does the rebuilt player shots-on-target model (club rate × 0.839, shrunk to 0.72, Poisson) calibrate on tournaments? | 4,989 player-matches, leave-one-tournament-out | Brier 0.174 vs 0.212 for the previous model and 0.250 for a coin flip ([output](outputs/player_sot_loto.txt)) | Shipped (`INTL_DISCOUNT`, `PRIOR_SOT_RATE`, `SOT_SHRINK_APPS`) |
| [sot1_tempo](validations/sot1_tempo.py) | Team tempo over-moves "1+ shot on target" for genuine shooters. Compress it? | 234 StatsBomb shooter-matches | Held-out Brier 0.275 → 0.255 at β = 0.5 ([output](outputs/sot1_tempo.txt)) | `SOT1_TEMPO_BETA = 0.5` |
| [timing_scale](validations/timing_scale.py) | Timing-window markets (before the first hydration break, stoppage time, …) settled far below our prices. Scale them down? | Every settled timing market, walk-forward | At decision time (Jul 10, n = 22 out of sample) 0.85 beat 1.0 by 1.83 Brier points. Re-run on all settled markets (n = 29): 0.85 still best, ahead of 1.0 and the 0.70 probe ([output](outputs/timing_scale.txt)) | `TIMING_SCALE` 1.0 → 0.85 |
| [btts_calibration](validations/btts_calibration.py) | A live drift flag on "both teams score". Is the odds-fitted grid miscalibrated? | 6,000 club matches with closing odds | Predicted 0.514 vs realized 0.519 (z = 0.8), calibrated by decile ([output](outputs/btts_calibration.txt)) | No change: knockout noise |
| [team_goals_calibration](validations/team_goals_calibration.py) | Same question for "team scores N+ goals" | 12,000 club team-matches | Calibrated overall (0.379 vs 0.381); the top decile under-predicts by 0.038 (z = 2.9) ([output](outputs/team_goals_calibration.txt)) | Overall unchanged; the favourite tail documented but unshipped (no remaining market was affected) |
| [first_goal_drift](validations/first_goal_drift.py) | Favourites scored first "too often" live (gap −0.19, n = 10). Is the Poisson race wrong? | 219 StatsBomb matches | Observed 0.886 vs model 0.878 (z = 0.35) ([output](outputs/first_goal_drift.txt)) | No change: goals are time-exchangeable |
| [knockout_tempo](validations/knockout_tempo.py) | Before the final: do knockout matches produce fewer counting events, so the total-level scales need a stage haircut? | 314 matches (86 knockout), 6 tournaments | No stat differs significantly (total SOT 0.92×, p = 0.09; cards and goals 1.01×), and the two World Cups ran *higher* in the knockouts ([output](outputs/knockout_tempo.txt)) | No knockout correction |

## Derivations

Conditional tables measured on StatsBomb open data and used directly by the models.

| Study | Produces | Used by |
|---|---|---|
| [goal_methods](derivations/goal_methods.py) | P(≥1 own goal / header / outside-the-box goal \| total goals) ([output](outputs/goal_methods.txt)) | `counts._OWN_GOAL`, `_HEADER_GOAL`, `_OUTSIDE_GOAL` |
| [substitute_scorers](derivations/substitute_scorers.py) | P(a substitute scores \| total goals), five-substitution era ([output](outputs/substitute_scorers.txt)) | `counts._SUB_GOAL` |
| [knockout_families](derivations/knockout_families.py) | Hold-the-lead conditionals; full-90-minute rates by position ([output](outputs/knockout_families.txt)) | `goals.p_hold_lead`, `players.FULL90_RATE` |

A few smaller tables were measured in one-off sessions whose scripts were not kept:
`SUB_AT_HT_RATE` (0.497), `SUB_FIRST_RATE` (0.50), `PEN_CONVERSION` (0.725, n = 109),
`FIRST_GOAL_ASSISTED` (0.582, n = 285), the distinct-shooters curve (n = 627) and the
single-digit-shirt scorer share (0.383, n = 703). `VAR_REVIEW_RATE` (0.30) is an estimate.
The pricer's `BLEND_WEIGHTS` are rounded from the club backtest's per-family weights in
[`data/models/family_blend.json`](../data/models/family_blend.json); the fitting code was not kept.

## Studies

| Study | Question | Result |
|---|---|---|
| [polymarket_derived_markets](studies/polymarket_derived_markets.py) | Would Polymarket's derived markets, one hour before kickoff, improve our prices? (needs Polymarket's live API) | No: the match-winner price is already blended in; the derived markets were thin or missing. Not used. |

## Rejected

Built, measured, and kept out of production. Each folder explains why.

- [monte_carlo_simulator](rejected/monte_carlo_simulator/) — a minute-by-minute game-state
  simulator. It reproduced the analytic prices (its test passes) but moved them by ~0.0003
  Brier: game state redistributes events within a match without changing the marginals the
  markets ask about.
- [deep_model](rejected/deep_model/) — a GRU over each team's last ten matches on top of the
  odds features, gated to earn weight only if it beat the statistical stack on held-out Brier.
  National teams play too rarely for the sequences it needs; it never earned weight.
