"""Every tuned constant in the pricing models, with the evidence behind it.

The models read these at call time as `cal.NAME`, which is what lets bot 2's
A/B experiment swap a value for one pricing pass (probcup.pipeline.experiment)
and restore it afterwards. Values are exactly those that ran the final.

Measured empirical tables (StatsBomb conditionals such as brace rates, goal
methods or substitute-scorer shares) live next to the model code that uses
them; this file holds the levels that were fitted, validated and promoted.
"""
from __future__ import annotations

# --------------------------------------------------------------------------- #
# Count models (probcup.models.counts)

OFFSIDE_TEMPO_EXPONENT = 0.18  # measured offside-vs-attack elasticity (StatsBomb)

# Count regressions are fit on CLUB data; international tournaments have
# systematically different event rates. These are tournament_mean / club_mean
# measured on 116k club + 314 StatsBomb tournament team-matches. Applied only
# when pricing tournament fixtures (CountPricer.tournament=True), never in the
# club backtest. Fouls > 1: tournament play is more cagey/physical.
COUNT_INTL_DISCOUNT = {"cards": 0.849, "corners": 0.872, "sot": 0.839, "fouls": 1.104}

# Per-stat WC-2026 calibration multipliers on count MEANS, applied only when
# pricing tournament fixtures (CountPricer.tournament=True), never in the club
# backtest. REPLACES a former UNIFORM 0.90 "low-event" multiplier (2026-06-19)
# that was a blunt instrument: it suppressed every stat equally, but WC-2026
# needs OPPOSITE corrections per stat. Re-fit 2026-06-24 on 383 settled markets
# (objective: minimise our Brier vs realized outcomes = calibration, the
# RBP-optimal target) with chronological OOS validation — each value below
# improves RBP on BOTH halves of the tournament — and cross-checked against
# observed WC-2026 rates (goals 3.12/game vs 2.56 in 2022; fouls 24.4 vs 27.7;
# yellows ~2.17/game):
#   cards   0.78  WC-2026 is a low-card tournament; the StatsBomb-based card model
#                 over-predicted by ~30pts (we 0.62 vs 0.31 realized). The former
#                 uniform 0.90 was nowhere near enough.  (settled cards -57 -> -5 RBP)
#   sot     1.00  removes the erroneous suppression — the early period (pre-0.90)
#                 had SOT well-calibrated; the 0.90 broke it, esp. total-SOT
#                 (-54 RBP).  (settled sot +153 -> +186 RBP)
#   corners 1.05  corners run high in WC-2026 (we under-predicted); modest boost.
#                 (settled corners +29 -> +54 RBP)
#   offsides 0.90 UNCHANGED — 2nd-biggest edge via fading and the realized rate is
#                 too unstable across the split to re-fit without overfitting.
#   fouls   0.90  unchanged; only appears in head-to-head markets where the level
#                 cancels (so this value is inert).
# Each value is uniform WITHIN a stat, so it moves only THRESHOLD markets; head-to-
# head "more-than X" comparisons are unchanged (asymmetry cancels). Anything not
# listed falls back to DEFAULT_INTENSITY. Re-estimate via `probcup ledger` + the
# calibration as KO-stage data settles (KO event rates may differ from the group
# stage). A between-team SOT correlation term for TOTAL-SOT is a flagged follow-up.
# sot stays 1.00 here. TEAM-level SOT (team_count_ge, both_teams, head-to-head, and player ">=1"
# via the normaliser) is well-calibrated (settled gaps ~-0.05). The under-prediction is TOTAL-
# SPECIFIC, so it is corrected by TOTAL_SOT_SCALE below — NOT by lifting the per-team mean.
# (2026-06-29: a flat sot=1.40 was tried and REVERTED — it fixed total SOT but OVER-corrected team
# SOT, leave-out Brier 0.203->0.230 on 31 settled team-SOT markets. The decoupled form keeps team
# SOT at calibrated v4 and boosts only the total.)
TOURNAMENT_INTENSITY = {"cards": 0.78, "sot": 1.00, "corners": 1.05,
                        "offsides": 0.90, "fouls": 0.90}
DEFAULT_INTENSITY = 0.90

# TOTAL-SOT level correction — applied ONLY to the convolved total (p_total_count_ge for sot) and
# the total-derived squad "2+ SOT" (total_sot_pmf), never to team/player SOT. The platform realises
# ~1.4x StatsBomb's total SOT (settled total_count_ge/sot pred 0.54 vs hit 0.83, n=18; implied scale
# 1.49, CI 1.24-1.78 — 1.40 is the conservative end). Validated live (BRA/JPN "8+ total SOT" HIT:
# v4 0.31->Brier 0.476 vs boosted 0.65->0.122) and by leave-out sim (total-SOT Brier 0.245->0.142).
TOTAL_SOT_SCALE = 1.50  # 2026-07-02 PROMOTED 1.40->1.50: the bot-2 1.60 probe went 4/5 vs 1.40
                        # (net dBrier -0.39); implied scale 1.49 (CI 1.24-1.78) -> point estimate.

# TOTAL-only offsides/shots level corrections — same total-specific pattern as SOT.
# The R32 our-data backtest showed total offsides (-28 RBP, pred 0.41 vs crowd 0.49,
# ~1.20x under) and total shots (-14 RBP, ~1.22x under) under-predicted, on THIN samples
# (n=4 / n=1) that cannot be checked against StatsBomb (platform counting convention), so
# both started as bot-2 experiments (bot2_experiment.yaml: total_offsides_scale /
# total_shots_scale); bot 1 moves only on an OOS-validated win.
TOTAL_OFFSIDES_SCALE = 1.10  # 2026-07-02 PROMOTED 1.0->1.10: total offsides -52 RBP cumulative
                             # (bias -0.32, n=7, direction consistent across ENG/COD + ESP/AUT);
                             # probe won at both notches on 2/3 settles. Mild level lift only.
TOTAL_SHOTS_SCALE = 1.0      # blanket shots probe is NET NEGATIVE (USA/BIH 22+ shots 91% vs NO,
                             # Brier 0.83) — dominance-concentrated, NOT promoted; bot-2 only.

# TOTAL-only card level correction: cards are over-predicted even after the 0.78 per-stat
# intensity — direct calibration (2026-07-01, 724 settled) shows mean card pred 0.481 vs
# realized 0.431 (+0.050), corroborated by the separate manual-branch policy backtest (+116 RBP
# from a card adjustment) and agent/memory/edge-analysis-and-fixes.md (total_count_ge/cards
# +0.14). A total-only reduction targets that without touching the calibrated card COMPARISONS
# (card head-to-heads are winning/scale-invariant — never lift the per-team mean).
TOTAL_CARDS_SCALE = 0.85  # 2026-07-02 PROMOTED 1.0->0.85: cards over-predicted, corroborated 5
                          # ways — direct calib +0.05 (724 mkts), manual-branch backtest +116,
                          # edge memory +0.14, and 8/13 current-era live A/B wins with no real
                          # losses (ENG/COD, USA/BIH x2, BEL/SEN x3, ESP/AUT). Total-only.

# FAVOURITE-SOT boost. The club-fitted SOT elasticity (log_lam_team 0.797) is calibrated for
# EVEN matches (model 3.36 vs realized 3.33) but too FLAT for heavy favourites: settled team-SOT
# predictions in the mid-high buckets under-predict by -0.12 to -0.18 (2026-07-01, n=32), and
# dominant teams rack up SOT (StatsBomb level 3.33 -> blowout 6.93, though that margin curve is
# ENDOGENOUS so we stay conservative). Supremacy-gated multiplier on the FAVOURITE's team-SOT mean
# (cascades to its players' SOT via tempo and to comparisons) — leaves even matches untouched.
# mu *= 1 + BOOST*min(lam_team/lam_opp-1, 2).
FAVORITE_SOT_BOOST = 0.06  # 2026-07-02 PROMOTED 0.0->0.06: the favourite-SOT under-prediction is
                           # bot 1's dominant recent leak — the 3 biggest losses in the last 4 games
                           # are ALL it (Bellingham 2+SOT -64, ENG 7+SOT -61, Spain 8+SOT -58).
                           # Validated: settled mid-high team-SOT bias -0.12..-0.18 (n=32), StatsBomb
                           # elasticity too flat for heavy favourites, and the live A/B won 4/4
                           # ESP/AUT divergents incl. the direct target (17->25 on a YES) and the
                           # player cascade (Yamal 39->44 YES). Supremacy-gated: even games untouched.

# Timing-window event scale. 2026-07-10 PROMOTED 1.0->0.85 (approved): timing_event
# over-predicts — realized 0.214 vs shipped 0.432 on n=28 settled. Three independent
# signals: live A/B probe 5/6, family calibration +0.146, and a walk-forward backtest
# (research/validations/timing_scale.py, n=22 OOS) where 0.85 beat 1.0 by -1.83 Brier pts.
# 0.85 is a validated PARTIAL correction (backtest optimum is lower ~0.45-0.70; the 0.70
# bot-2 probe then lost 5/5 live and was retired). Scales the windowed mean in
# p_total_in_window only.
TIMING_SCALE = 0.85

# --------------------------------------------------------------------------- #
# Player props (probcup.models.players)

# Scorer-family priors (unchanged — this family is well-calibrated)
PRIOR_GOAL_SHARE = 0.12      # share of own team's goals
PRIOR_INVOLVEMENT = 0.20     # share of goals scored-or-assisted
SHRINK_APPS = 6.0            # appearances of prior weight (goal shares)

# SOT-family constants (all measured; see the players module docstring)
PRIOR_SOT_RATE = 0.72        # tournament forward mean SOT/match
SOT_SHRINK_APPS = 1.0        # validated shrink strength toward the prior
INTL_DISCOUNT = 0.839        # club SOT/match -> tournament SOT/match
SOT_ALPHA = 0.05             # near-Poisson (within-player overdispersion ~0)
TOURNAMENT_TEAM_SOT = 4.11   # player-SOT tempo normaliser = team_sot_mean / this.
# Must track TOURNAMENT_INTENSITY['sot'] = 4.11 x sot_intensity. sot intensity is back to
# 1.00 (the 06-29 flat 1.40 was REVERTED — it over-corrected TEAM/player SOT; the total-SOT fix now
# lives in TOTAL_SOT_SCALE, total-only), so this is 4.11 = 4.11 x 1.00. player ">=1 SOT" is
# thus exactly v4 again (the total-SOT correction does not touch it).
MAX_TEMPO = 1.6              # cap on the per-match tempo multiplier
# For the near-binary ">=1 SOT" threshold, team tempo barely moves whether a genuine
# shooter gets a shot ON TARGET (a starter shoots regardless of team strength). The
# linear tempo curve is too steep for this case: StatsBomb (314 matches, 234 shooter-
# matches) shows the model UNDER-rates underdog shooters (pred 0.23-0.44 vs realized
# 0.38-0.54) and OVER-rates favourites (0.71 vs 0.50). Compressing the tempo swing
# toward 1.0 lowers held-out Brier 0.275->0.255 at beta 0.5. Applies to >=1 SOT for a
# real shooter (rate>=SHOOTER_RATE) ONLY; 2+ SOT (volume) keeps the full tempo model,
# and calibrated low-rate defenders (realized 0.10) are left untouched. See
# research/validations/sot1_tempo.py.
SOT1_TEMPO_BETA = 0.5        # 0=tempo-independent, 1=full tempo (old). 0.5 = conservative
SHOOTER_RATE = 0.5           # only compress for genuine shooters

# --------------------------------------------------------------------------- #
# Goal model (probcup.models.goals)

ET_FRACTION = 1.0 / 3.0  # 30' extra time vs 90' regulation, at the same Poisson rate
PENS_TILT = 0.25         # a penalty shootout retains only ~this share of the regulation
                         # strength edge — empirically shootouts are near coin-flips

# --------------------------------------------------------------------------- #
# Pricer blending (probcup.models.pricer)

# Backtest-derived weight on the model vs the structural baseline, keyed by
# (family, stat). Families absent here use the model fully (w=1). Rounded from the
# per-family `w_model` values fitted on the walk-forward club backtest and kept in
# data/models/family_blend.json.
BLEND_WEIGHTS = {
    ("total_count_ge", "cards"): 0.40,
    ("team_count_ge", "cards"): 0.40,     # borrows the total-cards evidence
    ("total_count_ge", "corners"): 0.94,
    ("team_more_count_than", "fouls"): 0.82,
    ("team_more_count_than", "cards"): 0.95,
    ("team_more_count_than", "corners"): 0.97,
    ("h2_more_goals_than_h1", "goals"): 0.67,
}

# NOTE (2026-06-14): a broad TOURNAMENT_WEIGHT_CAP shrinkage was tried and
# REVERTED. It was validated only on ~69 live markets, which were confounded
# with an unlucky favourite-underperformance run. The unbiased 314-match
# StatsBomb test showed shrinking the head-to-head / SOT families HURTS (they're
# calibrated), and cards already carry the right weight (0.40). Lesson: validate
# calibration changes on the large sample, not the recent live run.

# --------------------------------------------------------------------------- #
# Pricer assembly (probcup.pipeline.build)

TOURNAMENT_AVG_LAM = 1.30          # structural baseline goal rate per side
TOURNAMENT_CARDS_PER_MATCH = 3.46  # StatsBomb mean total cards/match (referee baseline)

# Referee strictness factor = (ref cards per game) / TOURNAMENT_CARDS_PER_MATCH,
# clipped to [FLOOR, CAP]. ASYMMETRIC CAP at 1.0 (2026-06-19, RBP-validated): a
# multiplier ABOVE 1.0 (strict ref) consistently OVER-predicted cards and was our
# single worst RBP leak — card markets flipped +15.5 -> -12.4/market once
# aggressive referee factors went in; the backtest shows capping at 1.0 recovers
# +37.7 RBP. A LENIENT ref may still reduce cards (we over-predict, so that's
# safe); a strict ref never inflates them. The remaining low-event level is
# handled by TOURNAMENT_INTENSITY.
REFEREE_FACTOR_CAP = 1.0
REFEREE_FACTOR_FLOOR = 0.6

# NOTE (2026-06-14): a favorite-longshot extremizing of the odds was tried and
# REMOVED. The bias is real but its benefit in modern de-margined odds is
# negligible (+0.00005/market out-of-sample), and it hurt on the live sample.
# We anchor on the de-margined odds as-is — accurate, lean, no overfitting.
