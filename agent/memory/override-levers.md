---
name: override-levers
description: "Which player_overrides.yaml field moves which market; expected_minutes now scales ALL player props; scorer_prop_prob must be tagged with scorer_prop_match"
metadata:
  node_type: memory
  type: reference
  originSessionId: edc0be82-f7ce-4493-be8b-e55f21f51524
---

player_overrides.yaml levers and what they move in-model (re-verified against players.py 2026-06-29):

- **expected_minutes**: scales ALL three player families now — `player_sot_ge`, `player_scores`, AND `player_scores_or_assists` (each = rate/share × … × expected_minutes/90). (CORRECTION: the old note that it was INERT for scorer/S-or-A was from 2026-06-21; the 06-27 batch made scorer/S-or-A scale by minutes too — see [[lineup-puller]].) The confirmed-XI pull sets this per-match and OVERRIDES the global value, so prefer letting the pull own minutes; only hand-set it for a ruled-OUT player the pull can't catch (absent = unmatched = left at default).
- **scorer_prop_prob**: used outright for `player_scores` — the de-margined anytime-scorer market price (markets beat models). **Must be paired with `scorer_prop_match: "<match name>"`** (2026-06-29): a prop is OPPONENT-SPECIFIC and otherwise rides into the team's next game stale (the Gyökeres/Balogun cleanup). Untagged → used but printed as a loud warning; tagged for a different match → auto-expires to the model.
- **involvement_share**: base lever for `player_scores_or_assists` (intrinsic rate). **goal_share**: base lever for `player_scores` when no prop. Use these for INTRINSIC rate corrections; use expected_minutes for minutes/rotation.
- **sot_per_match**: base rate for `player_sot_ge`. **team**: REQUIRED for players whose squad is absent from StatsBomb, else priced at match-average strength.

GOAL_SHARE ARTIFACTS: a goal_share above ~0.45 means "scores >45% of the team's goals" — implausible; it's usually a bad sample (Gyökeres was 0.732). FIX IT AT SOURCE (back out from a market anytime price: goal_share ≈ −ln(1−p_anytime)/team_lam) rather than masking it with a static prop — then the model self-corrects by opponent. `PlayerModel.unrepresentative_scorer()` flags the opposite failure (enough apps but 0 goals → floored, the Larin class).

RULE: a ruled-OUT player → expected_minutes 0 now zeroes SOT, scorer AND S/A together (no need to separately zero goal_share/involvement, unlike the old note). A rotation/bench RISK that still likely starts → trim expected_minutes (the pull confirms near kickoff). See [[r32-family-hardening]], [[player-prop-prior-fallback]], [[edge-analysis-and-fixes]].
