---
name: parser-ko-normalization
description: "Knockout rounds re-phrase markets ('in regulation (90 min + stoppage time)'); parser normalizes them; backlog of new market families still unpriced"
metadata: 
  node_type: memory
  type: project
  originSessionId: edc0be82-f7ce-4493-be8b-e55f21f51524
---

Discovered 2026-06-27: the KNOCKOUT rounds opened with RE-PHRASED markets — group-stage templates get a "... in regulation (90 minutes + stoppage time)" / ", excluding extra time" qualifier (KO ties can go to ET), plus brand-new market types. This silently broke ~HALF our coverage: 58/120 open markets (48%) were UNPARSEABLE -> skipped, not submitted. Our models price 90-min regulation, so the qualifier is a semantic no-op.

FIX (`src/questions/parser.py` `_normalize`, runs before rule matching): strip the regulation/extra-time qualifier to the base group-stage question; "Will regulation (...) end in a tie?" -> "Will the match end in a tie?". Plus flexed rules (team_win optional "the match", penalty optional "in the match", h2 "have|produce", team_goals optional "total") and new variants (first-half total goals, "X ahead at halftime", total offsides, "card shown in the first/second half"). RESULT: 76% parse overall AND 100% of the imminent R32 slate (60/60) — verified via a live `daily_run --hours 24 --dry-run`. Unparseable markets stay LOUD (parse() raises -> daily_run prints CANNOT PARSE + skips), never mis-submitted.

TIER A — DONE 2026-06-27 (exact derivations from the goal/card models; verified prices + identities, end-to-end through MatchPricer.price): BTTS `p_btts`, draw `match_drawn`=`p_draw`, team scores first goal `p_first_goal_game`, red card `p_red`, win by N `p_win_by_ge`, score in both halves `p_team_scores_both_halves`, both teams >=1 card (reuses `both_teams_count_ge`). Parser 52% -> 84% on live markets (draw reproduces p_draw, win-by-1==win, first-goal home+away == P(>=1 goal)).

TIER B — still unpriced (~19 markets, niche/later games); EACH needs genuine data/model work, NOT a quick rule — do properly, never hand-wave:
- TIMING (~10, biggest + cleanest, DO NEXT): goal/card/offside/corner before the 1st hydration break (<30'), card/goal after the 2nd (>75'), goal in 1H/2H stoppage. StatsBomb events HAVE `minute` (statsbomb.py `_team_half_rows` currently buckets only by half) -> add minute-window counters, rebuild the parquet cache (refresh=True), add window-shares to tournament_aggregates, price lam_match * window_share. Data-driven.
- advance to Round of 16 (2): GROUP qualification -> needs a standings/remaining-fixtures simulator OR external qualify-odds.
- any player 2+ goals / 2+ SOT (3): squad-level distribution, not a single named player.
- substitute scores (2): measure the sub-goal-share from StatsBomb (Substitution events + goals).
- total shots on+off (1): a shots model (StatsBomb has Shot events; the club regression doesn't carry shots).
See [[lineup-puller]], [[prediction-system]].
