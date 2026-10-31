---
name: lineup-puller
description: "Confirmed lineups auto-pulled from ESPN -> expected_minutes; how it works, what it fixes and what it doesn't"
metadata: 
  node_type: memory
  type: project
  originSessionId: edc0be82-f7ce-4493-be8b-e55f21f51524
---

Built 2026-06-27 after the 06-26 Trezeguet miss: a stale benched override (exp_min 15, set 06-22 vs NZ) rode into the Egypt game because a garbled web-lineup scrape "confirmed" the benching; the market LOCKED at 5% SOT when he actually started (~30%). ROOT CAUSE = overrides are global/persistent/UNSCOPED (zero staleness logic in code) + the free API-Football plan can't serve 2026 lineups, so benchings were hand-typed from web scrapes and never expired.

THE FIX (`scripts/pull_lineups.py`): ESPN's match `summary` endpoint — the SAME one `odds_feeds.py` already uses for odds — carries a `rosters` block with the posted XI (a `starter` bool per player) ~60 min before kickoff. FREE, no new dependency/key. `daily_run` auto-pulls it within 100 min of kickoff (`LINEUP_PULL_WINDOW`) and writes `data/match_lineups.yaml` = `{match: {minutes: {player: 90|20}}}`. `PlayerModel(match_minutes=...)` gives the PULLED value precedence: pulled > global override expected_minutes > 90. `build_pricer` reads only the entry for the match it prices (scoped by match name -> a benching can NEVER leak into a later fixture). So a stale benching is auto-corrected at the final pass.

ROBUST BY CONSTRUCTION: writes NOTHING unless the ESPN teams orient to our match AND a real XI is posted (exactly 2 rosters, >=11 starters each) — early/missing lineup keeps the all-90 default. Name-matching is conservative: exact order-insensitive (`_normalize_name`) then an UNAMBIGUOUS surname; an ambiguous or unmatched player is FLAGGED into NEEDS ATTENTION, NEVER force-benched (a spelling miss can't manufacture a false benching). Verified LIVE (EGY/IRN -> Trezeguet 90; NZ/BEL -> Tielemans 90) + 5 regression tests in `tests/test_pull_lineups.py` (all green; the 3 failing `test_parser.py` tests are pre-existing, unrelated — an unparseable "card after hydration break" template).

WHAT IT FIXES: `expected_minutes` -> the SOT / `player_sot_ge` family (the Trezeguet bug). Automatic, every game.

EXTENDED 2026-06-27 (robustness batch):
1. MODEL FIX — `p_scores` & `p_scores_or_assists` now scale by `expected_minutes/90` (like SOT). So the pull ALSO fixes the SCORER family (benched -> reduced, ruled-out 0min -> 0) and the manual goal_share benched-cut is no longer needed. Verified: Mané p_scores 90min 0.343 / 20min 0.089 / 0min 0.000; starter price UNCHANGED (no regression); a scorer prop still wins.
2. STALENESS GUARD — `daily_run._flag_stale_overrides` flags any player drawing `expected_minutes!=90` or `scorer_prop_prob` from the GLOBAL file not confirmed by today's per-match pull (verified firing on Sučić/Gonçalo Ramos for R32). Safety net, not the primary mechanism.
3. `scorer_prop_prob` is per-OPPONENT (Al-Dawsari +3000-vs-SPAIN class) — now FLAGGED by the guard; moving it into the per-match file (`match_lineups.yaml`) is the remaining STRUCTURAL step. Also deferred: clearing the stale global benchings (the pull+guard handle them at game time; bulk YAML edit skipped for formatting safety). See [[override-levers]], [[parser-ko-normalization]], [[edge-analysis-and-fixes]], [[daily-automation]].
