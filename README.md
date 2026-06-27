# Jump Trading Probability Cup — Prediction System

Competing on SportsPredict's **Probability Cup** (2026 World Cup, Jun 11 – Jul 19 2026)
via the Model API. Full API reference: `docs/API-Documentation.pdf`. Canonical strategy
write-up: `docs/strategy_v4.md`. Operating memory (decisions, fixes, current state) lives
in the agent memory index (decisions, fixes, current state).

---

## 0. The three entries

We hold **three independent leaderboard entries** on the same markets. Each is scored on its
own (RBP, §1); the standing is driven by whichever ranks best.

| Entry | Run by | What it is | Key |
|---|---|---|---|
| **bot 1** | the automated pipeline | **stable v4** — the calibrated production model; the current leader. Unvalidated changes are kept off it. | `SPORTSPREDICT_KEY_1` |
| **bot 2** | the automated pipeline | **live A/B experiment** — prices the same odds as bot 1 with one candidate tweak (`data/bot2_experiment.yaml`), to test changes before they reach bot 1. | `SPORTSPREDICT_KEY_2` |
| **manual** | a human/agent, by hand on the app | the third slot. There is no third API key — it is hand‑entered on the SportsPredict app, and currently carries bot 1's values. | none |

The pipeline (§5) produces and submits bot 1 and bot 2 and prints bot 1's daily chart; the
manual entry is filled in by hand. Predictions can be revised until a match's kickoff (the
deadline); the last value counts.

**Observations relevant to running several entries** (facts, not instructions):
- Scoring is a **proper scoring rule** (§2): calibrated probabilities minimize expected Brier,
  with no shading. bot 1 is built to exactly that.
- Three entries carrying the *same* numbers rank near‑identically (fully correlated).
  Decorrelating them is the only thing that turns three entries into three distinct shots — and
  every step away from the calibrated value lowers expected score in exchange for variance
  (proper scoring again).
- Our edge analysis (`docs/strategy_v4.md`, agent memory) finds we **beat the crowd on soft
  count markets** (offsides, corners, head‑to‑head "ties‑lose" comparisons) and **lose to it on
  goals / 1X2 / totals**, where the betting market + Polymarket are sharper — so the bots anchor
  to the market there.
- The largest self‑inflicted losses on record came from pushing **low‑base‑rate props above the
  crowd** on a hunch (benched players, longshot "X scores").

Everything below describes the system that produces bot 1 / bot 2, so any number can be
reproduced or reasoned about.

---

## 1. The game in one paragraph

~72 matches, each with ~10 binary yes/no markets (~720+ total). You submit a probability as an
integer **1–99** per market before kickoff. Scoring is **Brier**: `(p − o)²`, lower is better
(`o` = outcome 0/1). What ranks you is **Relative Brier Points**:
`RBP = (crowd_brier − your_brier) × 100` — you win by beating the **crowd average**. Stage
weighting: **group 1×, knockout 2×, final 3×** (so accuracy matters more now that the
knockouts are here). Predictions can be revised until kickoff; the last value counts.

## 2. Why calibration, not boldness

Because RBP is **linear in your own Brier** (the crowd term is fixed and identical for every
competitor), maximizing expected RBP = minimizing your own expected Brier = **reporting your
true, calibrated probability.** Brier is *strictly proper*: any shading away from your honest
estimate raises expected Brier. So there is **no extremizing, no game‑theoretic shading** — we
researched and backtested this explicitly and retracted an "extremize" idea as invalid. Rank
across competitors is decided purely by Brier‑vs‑outcome (the crowd‑variance term cancels).
The whole system is built to produce the most calibrated number, then submit it unshaded.

## 3. Where our edge is (and isn't)

From ground‑truth RBP analysis across the tournament:
- **Edge (soft markets):** niche counts the crowd guesses at — **offsides** (best), **corners**,
  **head‑to‑head count comparisons** ("more corners/SOT/fouls than…", which **resolve "No" on a
  tie** → even matchups price ~40%, a systematic crowd error we collect on), and certain
  combos. This is where deviating from the crowd pays.
- **No edge (sharp markets):** **goals / 1X2 / totals** — the betting market + Polymarket are
  sharper than us; fighting them loses. We **anchor** to them.
- **Calibration corrections (WC‑2026):** the count means carry per‑stat tournament multipliers
  (`TOURNAMENT_INTENSITY` in `counts.py`: cards 0.78 — WC‑2026 is low‑card; sot 1.00; corners
  1.05; offsides 0.90), OOS‑validated. See `docs/strategy_v4.md`.

---

## 4. Methodology — how every probability is produced

Three layers, in strict order of trust. Guiding result (literature + our backtest):
**bookmaker closing odds are the best‑calibrated public forecast** — anchor on them and extend
to the markets bookmakers don't price.

### Layer 1 — market anchors (used directly)
- **1X2 odds** — de‑margined average of two independent sharp sources: bookmaker (ESPN →
  DraftKings, `src/wcdata/odds_feeds.py`) and **Polymarket** (`src/wcdata/polymarket.py`). A
  >0.10 disagreement is flagged as possible stale odds. Totals from the bookmaker.
- **Anytime‑scorer prop odds** price "Will [player] score?" outright when researched: the daily
  routine looks up the prop, de‑margins it (÷ ~1.10 margin), writes `scorer_prop_prob` into
  `data/player_overrides.yaml`; the pricer prefers it over the model. **These are
  per‑OPPONENT** — re‑research them each match (a stale one was a real miss; see §7).

### Layer 2 — odds‑anchored Dixon‑Coles goal engine (`src/models/goals.py`)
We do **not** estimate team strength; we solve for `(λ_home, λ_away, ρ)` that reproduce the
market's `p_home`, `p_draw`, and total. Every goal‑derived market is then priced by enumerating
the scoreline grid, so all answers are mutually consistent (P(win)+P(draw)+P(loss)=1, etc.).
Half markets use per‑half Poisson `λ·share` with the **tournament‑measured** 2nd‑half goal share
(~0.60). Families: win, draw, BTTS, total goals, team scores in half, win‑by‑N, score‑in‑both‑
halves, first goal of game/half, halftime tied/leading, 2nd‑half‑more‑than‑1st, and combos.

### Layer 3 — count models for markets without odds (`src/models/counts.py`)
Corners, cards, SOT, fouls: **negative‑binomial regressions on ~116k club matches** with the
*odds‑implied* goal rates as features, so they transfer to any fixture:
`log E[count] = b0 + b1·log(λ_team) + b2·log(λ_opp) + b3·is_home`. Plus per‑stat WC‑2026
calibration (§3) and a **referee strictness** multiplier for cards
(`data/referee_overrides.yaml` — a strict vs lenient ref swings P(4+ cards) ~0.24→0.66).
Offsides, penalty (0.293/match), red‑card (0.096), and every stat's 2nd‑half share come from
**314 StatsBomb tournament matches**. Head‑to‑head comparisons enumerate both teams' NB
distributions with **ties → "No"** (the ~40% even‑matchup edge).

### Player props (`src/models/players.py`)
- **Scorer / score‑or‑assist:** `1 − exp(−share · λ_team · expected_minutes/90)`. The
  `expected_minutes/90` factor (added 2026‑06‑27) means a benched player is reduced and a
  ruled‑out player → 0 **automatically from the pulled lineup** — no manual cut. A researched
  `scorer_prop_prob` still wins outright when present.
- **Shots on target:** per‑match rate × team tempo × `expected_minutes/90`, P(≥k) Poisson,
  rates shrunk to the 0.72 tournament‑forward prior, club rates × 0.839 international discount.
  Validated leave‑one‑tournament‑out (Brier 0.176).

### Composite questions, blending, validation
One `MatchPricer` per fixture holds one ScorelineModel + CountPricer + PlayerModel off the
**same** market‑fitted λs; `MatchPricer._price_raw` (`src/models/pricer.py`) is the one
auditable `family → method` routing table. Conjunctions are **exact joint enumeration** on the
shared grid (never products of marginals). Each family is then shrunk toward a structural
baseline by a **backtest‑derived weight** (`BLEND_WEIGHTS`; e.g. cards keep only 40% of the
model deviation). Walk‑forward backtest (2017/18→2025/26, ~41k matches/family):
**19/19 families beat always‑50%, 16/19 beat the base‑rate baseline, calibration within ±1%.**

---

## 5. The daily process — what actually runs

The pipeline auto‑prices and submits **bot 1 and bot 2**; it also **prints bot 1's chart for the
manual entry**. Predictions are built fresh and revised through the day as information arrives.

### The runs (dynamic, self‑scheduling)
- **~07:48 — MORNING run** (`.claude/commands/morning_run.md`, the anchor): evaluate last
  night's results + the bot1‑vs‑bot2 A/B; set bot 2's experiment; **flag** any bot‑1 promotion
  (never auto‑apply); submit both bots at a 48 h horizon; **print today's bot‑1 chart for the
  manual entry**; then **schedule that day's final passes** by running `scripts/schedule_day.py`,
  which reads the day's real kickoffs and creates a one‑shot job ~**50 min before each wave**.
- **Final passes** (`.claude/commands/matchday.md`, 5 h horizon), one per kickoff wave, ~50 min
  out: this is where the edge is added — **confirmed lineups** (auto‑pulled, see below),
  scorer odds, referees → update the override files → re‑price → submit both bots.
- **~last wave — NIGHT run** (`.claude/commands/night_run.md`, 26 h horizon): the final pass for
  the night's games **plus** prices all of **tomorrow** and **prints tomorrow's bot‑1 chart** so
  the manual entry can be pre‑filled.

Why dynamic: kickoff times move day to day, so fixed times mistime them. The morning run lays
the day's passes against the *actual* schedule, ~50 min before each wave (after lineups post,
with time to reprice). **Caveat:** these are in‑session crons — they do **not** survive closing the app; they fire only
while the session is open, and otherwise each run is invoked by sending its prompt.

### Confirmed lineups — pulled, not hand‑typed (`scripts/pull_lineups.py`)
Within 100 min of kickoff, `daily_run` pulls the **posted starting XI from ESPN's free
`summary` feed** (the same call we use for odds) and writes `expected_minutes` to
`data/match_lineups.yaml` (starter→90, bench→20). The pricer reads it, **scoped to that match**,
so a benching can't leak into a later fixture. Robust by construction: it writes **nothing**
unless a real XI is posted (2 teams, ≥11 starters) and the teams orient to our match, and a
player it can't confidently name‑match is **flagged, never force‑benched**. This replaced
hand‑parsed web lineups (which caused a stale‑benched miss). `expected_minutes/90` then flows
into **both** the SOT and scorer families.

### The override files (per‑match research inputs — never raw probabilities)
- `data/player_overrides.yaml` — **stable** player rates (`sot_per_match`, `goal_share`,
  `involvement_share`, `team`) **and** researched `scorer_prop_prob`. `expected_minutes` is now
  set by the pull; only hand‑set it for an injury known *before* lineups post.
- `data/referee_overrides.yaml` — `{match: {referee, cards_per_game}}` for the card markets.
- `data/manual_odds.yaml` — fallback 1X2/total when ESPN lacks a fixture (created on demand).
- `data/bot2_experiment.yaml` — bot 2's active A/B tweak.
- `data/match_lineups.yaml` — per‑match confirmed minutes (written by the pull; created on demand).

### Staleness guard
`daily_run` flags (in NEEDS ATTENTION) any player drawing a volatile value
(`expected_minutes≠90` or `scorer_prop_prob`) from the **global** file that wasn't confirmed for
*this* match — the safety net for the per‑opponent/per‑game staleness class.

### Submission order (contamination safety)
Both bots are priced off identical odds; **bot 2 (experiment) is submitted first, bot 1 (stable)
last**, so if anything ever cross‑contaminated, the stable version is the final write on both.

---

## 6. Making & submitting predictions (commands)

```bash
cd "/home/yifan/World Cup Predictions/jump-probability-cup"
source .venv/bin/activate   # interpreter: .venv/bin/python

# Price + submit both bots for everything kicking off within N hours
.venv/bin/python scripts/daily_run.py --hours 48 --submit --agent     # add --dry-run to preview
#   --agent drops a heartbeat so the crontab fallback defers to this run
#   within 100 min of a kickoff it auto-pulls the confirmed XI first

# Price ONE fixture and see the full table (no submit)
.venv/bin/python scripts/price_match.py "URU vs ESP"

# Pull/inspect a confirmed lineup on demand
.venv/bin/python scripts/pull_lineups.py "URU vs ESP" --dry-run

# Plan today's final-pass schedule (what the morning run lays down)
.venv/bin/python scripts/schedule_day.py

# Read settled Brier (per bot)
.venv/bin/python -c "import sys; sys.path.insert(0,'src'); from probability_cup import ProbabilityCup; print(ProbabilityCup(bot=1).results_df()[['question','brier_score']])"
```

**Manual entry:** there is no third API key — it is hand‑entered on the SportsPredict app before
each kickoff (kickoff = the deadline). The day's model values are available from the bot‑1 chart
the morning/night runs print, from `daily_run --dry-run` / `price_match.py`, and each bot's live
stored predictions can be read via the API (§9).

---

## 7. Project structure

```
src/
  sp_api.py            transport: REST endpoint wrappers (auth, retry/backoff)
  probability_cup.py   domain: snapshot caching, DataFrames, upsert submit() — import ONLY this
  questions/
    parser.py          question text -> MarketSpec(family, team, player, stat, threshold, period)
                       + _normalize() strips the KO "in regulation (90 min + stoppage time)" suffix
    teams.py           FIFA code <-> full name bridge (match names use codes, questions use names)
  wcdata/
    odds_feeds.py      ESPN/DraftKings 1X2 + totals + (the same summary feed carries lineups)
    polymarket.py      Polymarket prediction-market 1X2 (second sharp source)
    statsbomb.py       314 tournament matches -> half shares, offsides, penalty/red, player rates
    football_data_uk.py / internationals.py / api_football.py   club data, Elo, current-form rates
  models/
    goals.py           odds-fitted Dixon-Coles scoreline grid (all goal markets)
    counts.py          NB count regressions + TOURNAMENT_INTENSITY + referee factor
    players.py         scorer / score-or-assist / SOT (all scale by expected_minutes/90)
    pricer.py          MatchPricer._price_raw — the family -> model routing table + BLEND_WEIGHTS
    simulator.py / sim_pricer.py   game-state Monte Carlo pricer (knockout refinement)
    deep.py / ensemble.py          optional H200 GRU track (earns weight only if it beats stats)
scripts/
  daily_run.py         the production loop: price + (auto lineup pull) + guard + submit BOTH bots
  pull_lineups.py      ESPN confirmed XI -> expected_minutes (data/match_lineups.yaml)
  schedule_day.py      compute the day's final-pass times (~50 min before each wave)
  price_match.py       price one fixture; build_pricer() is the shared entry point
  submit.py            CSV -> upsert predictions (manual path)
  initialize.py / download_data.py / run_backtest.py    one-time setup + model fitting
  update_player_rates.py  current-season SOT/goal rates from API-Football
  verify_submissions.py / check_automation.py            health checks
  analyze_rbp.py / parse_history_rbp.py / calibration_report.py   performance analysis
.claude/commands/      morning_run.md, matchday.md, night_run.md  (the agent routines)
data/
  player_overrides.yaml, referee_overrides.yaml, manual_odds.yaml, bot2_experiment.yaml,
  match_lineups.yaml   the per-match research/knob files (some created on demand)
  count_models.json, family_blend.json, snapshot.json, *.parquet   fitted models + datasets
  logs/                runs.log, submissions.csv, matchday_reports.md, experiment_log.md, agent_last_run
docs/                  strategy_v4.md (canonical strategy), history.md (RBP history), API PDF
tests/                 parser coverage, simulator, pull_lineups regression
```

Two‑layer API design: notebooks/models import **only** `probability_cup.ProbabilityCup`, never
raw HTTP.

---

## 8. Current state & recent hardening (2026‑06‑27)

- **bot 1** = stable v4 (the leader). **bot 2** = live A/B (`aggressive-count-multipliers`:
  cards 0.72 / sot 1.05 / corners 1.15 vs v4's conservative values) — **no settled verdict yet**.
  Settled Brier runs ~0.21–0.24 (healthy).
- **Lineup puller** (new): confirmed XIs auto‑pulled from ESPN → `expected_minutes`; fixed a
  stale‑benched miss class.
- **Model fix** (new): scorer/score‑or‑assist scale by `expected_minutes/90` (benched→reduced,
  ruled‑out→0); starter prices unchanged.
- **Staleness guard** (new): flags volatile global overrides not reconfirmed per match.
- **Parser KO‑normalization** (new): the knockouts re‑phrased markets as "… in regulation
  (90 minutes + stoppage time)". `_normalize()` strips it; live coverage **52% → 84%, 100% of
  the high‑frequency families.** Unparseable markets **raise loudly and are skipped, never
  mis‑submitted.**

### Known gap — the Tier‑B market backlog (≈16% of live markets, mostly later/Sunday games)
These genuinely‑new families are **not yet priced** (each needs a real model, not a parser rule);
they currently submit **nothing** (flagged in NEEDS ATTENTION; the bots skip them). They are,
with the approach each would need:
- **Timing markets** (goal/card/offside/corner before the 1st hydration break <30′, after the
  2nd >75′, stoppage‑time goals) — needs StatsBomb minute‑bucket extraction (the data has the
  timestamps). *Biggest chunk; the planned next build.*
- **Advance to Round of 16** — group qualification; needs a standings simulator or "to qualify" odds.
- **Any player 2+ goals / 2+ SOT** — squad‑level distribution.
- **Substitute scores** — sub‑goal‑share from StatsBomb.
- **Total shots (on+off target)** — a shots model (we only model SOT).

---

## 9. Live‑API quirks & cheat sheet

- **`opening_time` is the kickoff = prediction deadline**; `closing_time` is ~2.5 h later.
- `GET /predictions` returns `probability` as a **percentage** (40), not 0–1.
- Match names use FIFA codes (`URU vs ESP`); questions use full names (`Uruguay`) — bridged by
  `src/questions/teams.py`.
- 60 req/min; the client backs off on 429. `409` on submit = already predicted → PATCH instead.

```
Base https://api.sportspredict.com/api/v1   header: Authorization: Bearer sp_live_...
GET /events -> GET /lobbies?event_id= -> POST /lobbies/{id}/join -> GET /matches?event_id=
-> GET /markets?lobby_id=&match_id= -> POST /predictions/batch (<=50) -> PATCH /predictions/{id}
-> GET /results (settled, per-prediction brier_score)

# Inspect what a bot has stored (swap the key per entry):
curl -s "https://api.sportspredict.com/api/v1/predictions" -H "Authorization: Bearer $SPORTSPREDICT_KEY_1" | python3 -m json.tool
```

### Tests
`.venv/bin/python -m pytest tests/` — parser, simulator, and the `pull_lineups` regression guard.
(The snapshot "all‑parse" assertion is intentionally red on the Tier‑B backlog above.)
