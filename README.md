# Jump Trading Probability Cup — Prediction Model

Competing on SportsPredict's Probability Cup (2026 World Cup, Jun 11 – Jul 19 2026)
via the Model API. Full API reference: `docs/API-Documentation.pdf`.

## The game in one paragraph

~72 matches, each with ~10 binary yes/no markets (~720 total). You submit a
probability as an integer 1–99 per market before kickoff (in the live system
`opening_time` is the kickoff — see API quirks below). Scoring is Brier:
`(p − o)²`, lower is better. What ranks you is **Relative Brier Points**:
`RBP = (crowd_brier − user_brier) × 100` — you win by beating the *crowd
average*. Stage weighting: group 1×, knockout 2×, final 3×. Predictions can be
revised (PATCH) until kickoff; the last value counts. Because RBP is linear in
our own Brier, the optimal strategy is simply honest, maximally calibrated
probabilities — no game-theoretic shading.

## The model

Three layers, in strict order of trust. The guiding result from the
literature (and our own backtest): **bookmaker closing odds are the
best-calibrated public forecast** — models that fight the market lose; models
that anchor on it and extend it to markets the bookmakers don't price, win.

### Layer 1 — market anchors (used directly)

- **1X2 odds** — the de-margined average of two independent sharp sources:
  the bookmaker (ESPN → DraftKings) and the **Polymarket prediction market**
  (`src/wcdata/polymarket.py`, Gamma API — WC matches trade $1–3M with ~0.3%
  overround). Blending two agreeing sources reduces single-source error; a
  >0.10 disagreement is flagged as possible stale odds. Totals come from the
  bookmaker (Polymarket has no WC totals market).
- **Anytime-scorer prop odds** price "Will [player] score?" outright when
  researched: the daily routine (`/matchday`) looks up each scorer market's
  prop odds, de-margins them, and writes `scorer_prop_prob` into
  `data/player_overrides.yaml`; `build_pricer` feeds those to the pricer,
  which prefers them over the model. No prop entry → model fallback.

### Layer 2 — odds-anchored Dixon-Coles goal engine (`src/models/goals.py`)

The joint scoreline distribution is independent Poisson per team with the
Dixon-Coles low-score correction τ. We do **not** estimate team strengths —
we solve for the parameters that exactly reproduce the market:

```
find (λ_home, λ_away, ρ)  s.t.  P(home win) = p_home(market)
                                P(draw)     = p_draw(market)
                                λ_home + λ_away = T(totals market)
```

where T inverts the over/under price: P(Poisson(T) ≥ 2.5-line) = p_over.
Every goal-derived market — BTTS+totals combos, "team scores in 2nd half",
halftime tied/leading, "first goal of the game/half", "2nd half more goals
than 1st" — is then priced by enumerating the scoreline grid, so all answers
are mutually consistent. Half-based questions use per-half Poisson rates
λ·share; live pricing uses the **tournament-measured** second-half goal share
(~0.60 from 314 StatsBomb matches — international tournaments skew later than
the ~0.54 club figure, which remains the backtest default).

### Layer 3 — count models for markets without odds (`src/models/counts.py`)

Corners, cards, shots on target, fouls: **negative-binomial regressions
fitted on ~116k club matches**, with the *odds-implied* goal rates as
features — so the model transfers to any World Cup fixture without team
history:

```
log E[count] = b0 + b1·log(λ_team) + b2·log(λ_opp) + b3·is_home
Var[count]   = mean·(1 + α·mean)        (α = moment-estimated dispersion)
```

Card markets additionally take a **referee strictness** multiplier
(`data/referee_overrides.yaml`, researched per match): referee identity is the
dominant driver of card counts (a strict vs lenient official swings P(4+ cards)
from ~0.24 to ~0.66), so the card mean scales by the referee's cards/game ÷ the
3.46 tournament average. Defaults to neutral when the referee is unannounced.

Offsides, penalty rate (0.293/match), red-card rate (0.096), and every
stat's second-half share come from **314 international tournament matches**
(StatsBomb event data: WC 2018/2022, Euro 2020/2024, Copa 2024, AFCON 2023).
Head-to-head markets ("more corners than...") enumerate both teams' NB
distributions — **ties resolve to "No"**, which is why evenly-matched
comparisons price at ~40%, not 50% (a recurring crowd error we collect on).

Player props (`src/models/players.py`): scorer probability
`1 − exp(−goal_share · λ_team)` with shares from StatsBomb history shrunk
toward positional priors (this family scored Brier ~0.10 live and is unchanged).

Shots-on-target props were rebuilt 2026-06-13 after scoring worse than a coin
flip live. Three data-driven corrections, each measured rather than assumed:
(1) an **international discount** of 0.839 on club-sourced rates (tournament
team SOT 3.70 vs club 4.41); (2) **Poisson** for P(≥k), which StatsBomb shows
is calibrated *conditional on the right rate* — the population overdispersion
is entirely between players, not within; (3) **shrinkage** of each rate toward
the 0.72 tournament-forward prior. Validated leave-one-tournament-out
(`scripts/validate_player_sot.py`): Brier 0.176 vs the old 0.212 and 0.25
coin-flip, on 4,982 held-out player-matches. Rates prefer current club form
(API-Football via `scripts/update_player_rates.py`, discounted) over thin/stale
StatsBomb history; `data/player_overrides.yaml` (`sot_per_match`,
`expected_minutes`, scorer `scorer_prop_prob`) takes precedence, and the daily
routine flags players on sparse history for current-form research.

### One match model, many readouts — how composite questions are priced

Many markets cannot be answered by any single-purpose model: conjunctions
("both teams score AND 3+ goals"), cross-period combos ("X scores the game's
first goal and Y scores in the 2nd half"), disjunctions ("penalty OR red
card"), head-to-head comparisons ("more corners than..."), and player props
that depend on team context. The system handles these by construction, in
five steps:

**1. Decompose, don't pattern-match.** The parser
(`src/questions/parser.py`) turns every question into a structured
`MarketSpec(family, team, opponent, player, stat, threshold, period)` —
19 families cover all 710 markets. Anything unrecognized **raises** rather
than guessing: a new question template fails loudly and gets a parsing rule
plus a pricing rule, never a silent default.

**2. One shared match context.** Each fixture gets exactly one
`MatchPricer` holding one ScorelineModel, one CountPricer, and one
PlayerModel — all driven by the *same* market-fitted (λ_home, λ_away).
Every market is a readout of that single joint model, which guarantees
cross-question coherence: P(win)+P(draw)+P(loss)=1, P(BTTS ∧ 3+ goals) can
never exceed P(3+ goals), a team's scorer props are consistent with its
expected goals, and the count models share the tempo implied by the goal
market.

**3. Conjunctions = exact joint enumeration.** AND-questions within one
distribution are *not* products of marginals. "Both teams score AND 3+
total goals" is computed by summing P(a, b) over the scoreline-grid region
{a ≥ 1, b ≥ 1, a + b ≥ 3} — the (negative) dependence between the two
clauses is captured exactly because both live on the same joint grid. The
same applies to halftime-state questions (joint over first-half scorelines)
and comparisons ("more goals in the 2nd half than X").

**4. Cross-distribution composites = explicit, documented approximations.**
Where a question spans genuinely different random objects, the components
still come from the shared rates, and any independence assumption is
deliberate and one-directional:
- *"A scores first goal of the game AND B scores in 2H"* — first-scorer is a
  Poisson race (λ_A / λ_total × P(any goal)), multiplied by B's 2nd-half
  scoring probability. Residual correlation is mild because both components
  already condition on the same match tempo (the λs).
- *"Penalty OR red card"* — priced from the **empirical union frequency**
  (0.369 across 314 tournament matches), not 1−(1−p_pen)(1−p_red):
  penalties and reds are positively correlated (chippy matches produce
  both), so the independence formula would overprice the union.
- *Head-to-head counts* ("more corners than...") — joint enumeration of the
  two teams' negative-binomial distributions with **ties resolving to
  "No"**; that tie mass is why even-matchup comparisons price near 40%, a
  systematic crowd error we collect on.

**5. Per-family trust weighting.** After pricing, each family is shrunk
toward the structural baseline by its backtest-derived weight (next
section) — so a composite family whose *model* signal proved weak in 41k
historical matches automatically leans on measured base rates instead.

The routing table itself is `MatchPricer._price_raw` in
`src/models/pricer.py` — one `match spec.family` block, one line per family,
so the question-to-model mapping is auditable at a glance.

### Blending (backtest-derived)

Each priced market is shrunk toward a *structural baseline* (the same
machinery at tournament-average λ = 1.30) by a per-family weight
`w* = E[(p−r)(o−r)] / E[(p−r)²]` estimated from the backtest
(`data/family_blend.json`, applied via `BLEND_WEIGHTS` in
`src/models/pricer.py`). In practice: cards markets keep only 40% of the
model's deviation (card counts are referee-driven, not quality-driven);
nearly everything else passes through at w = 1.

### Validation

Walk-forward backtest, seasons 2017/18 → 2025/26 (~41k matches per family,
count models always fitted on strictly earlier seasons):
**19/19 families beat the always-50% baseline; 16/19 beat the informed
base-rate baseline; pooled calibration within ±1%.** Report:
`notebooks/02_backtest.ipynb`, raw results in `data/backtest_results.parquet`.

### Monte Carlo match simulator (`src/models/simulator.py`)

A game-state-aware alternative pricer for derived markets, addressing the one
structural blind spot of the analytic model: it can't represent that a leading
favourite sits back and cedes second-half shots/corners to the chaser. The
simulator plays each match minute-by-minute (~40k sims), tracks the score, and
generates every derived stat as a *correlated* outcome — so all markets are
mutually consistent and game state is captured natively. It stays anchored:
goal rates come from the odds-fitted lambdas, per-stat means and measured
half-shares from `CountPricer`, and the game-state multiplier is normalized to
be **mean-preserving** (it only redistributes events, leaving full-match
marginals matched). Validated to reproduce the analytic model within Monte
Carlo noise (`tests/test_simulator.py`) plus the game-state corrections. Use
via `build_sim_pricer()` / `SimMatchPricer`; most valuable for knockout matches
(2–3× weight) where game state matters most. Honest note: because the analytic
model was already well-calibrated (per the 314-match StatsBomb validation), the
simulator's benefit is a modest, correct refinement, not a large accuracy jump.

### Deep model (optional H200 track, `src/models/deep.py`)

GRU encoder over each team's last-10-match stat lines + odds features →
negative-binomial heads for {home,away} × {goals, corners, cards, SOT,
fouls}. Train with `scripts/train_deep.py --device cuda` on the H200
(CPU smoke-tested). Per `src/models/ensemble.py` it only earns ensemble
weight where it beats the statistical stack on held-out Brier; with missing
national-team form it degrades gracefully to the odds baseline.

## Usage

### One-time setup

1. SportsPredict app → Probability Cup event → **Profile & History → My Bots
   → Generate New Bot** → copy the `sp_live_...` key (shown once; 2 bots max).
2. `cp .env.example .env`, paste keys as `SPORTSPREDICT_KEY_1` / `_2`.
3. `source .venv/bin/activate` (or `python3 -m venv .venv && pip install -r` deps:
   requests pandas scipy statsmodels pyarrow pyyaml tqdm pytest matplotlib jupyterlab torch).
4. `python3 scripts/initialize.py` — joins the lobby, snapshots all markets.
5. `python3 scripts/download_data.py` — fetches club CSVs, internationals,
   StatsBomb events (~300 MB, idempotent).
6. `python3 scripts/run_backtest.py` — fits count models, validates, writes
   `data/family_blend.json` inputs. Re-run after data refreshes.

### Per match-day

```bash
# 1. Price a fixture (pulls live ESPN/DraftKings odds automatically)
python3 scripts/price_match.py "CAN vs BIH" --save
#    -> review the printed table; players flagged [prior] need lineup research
#       (add researched rates to data/player_overrides.yaml and re-run)

# 2. Submit (dry-run first; real submission needs the CSV you inspected)
python3 scripts/submit.py "data/predictions_CAN_vs_BIH.csv" --dry-run
python3 scripts/submit.py "data/predictions_CAN_vs_BIH.csv"

# 3. Revise any time before kickoff — submit.py PATCHes existing predictions
#    automatically when the CSV values change.

# 4. After matches settle: track realized Brier vs expectation
python3 - <<'PY'
import sys; sys.path.insert(0, "src")
from probability_cup import ProbabilityCup
print(ProbabilityCup(bot=1).results_df()[["question", "brier_score"]])
PY
```

If ESPN lacks odds for a fixture, add a manual entry to
`data/manual_odds.yaml` (`CAN vs BIH: {p_home: .52, p_draw: .27, total_goals: 2.4}`,
first-listed team = p_home) and re-run `price_match.py`.

### Automation architecture (agent-primary, deterministic fallback)

Two layers run the daily routine, coordinated so they never conflict:

1. **Agentic orchestrator (primary)** — a Claude session runs `/matchday` on a
   schedule: it researches lineups/props/odds, writes findings to the override
   YAMLs, then runs `daily_run.py --submit --agent`. The `--agent` flag drops a
   heartbeat at `data/logs/agent_last_run`.
2. **OS crontab (fallback)** — runs `daily_run.py --submit --defer-to-agent 45`.
   If the agent's heartbeat is younger than 45 min, **the cron run defers and
   does nothing** — so the deterministic pipeline never overwrites the agent's
   research-informed values. It only fires when the agent *didn't* run (session
   closed, app crashed), guaranteeing a backtested submission either way.

The agent is the orchestrator; the model does all the math; the crontab is a
pure safety net. Install the fallback (survives the app closing — the only
layer that does):

```bash
cd "/home/yifan/World Cup Predictions/jump-probability-cup"
(crontab -l 2>/dev/null
 echo "10 8  * * * cd \"$(pwd)\" && .venv/bin/python scripts/daily_run.py --hours 48 --submit --defer-to-agent 45 >> data/logs/daily.log 2>&1"
 echo "35 11 * * * cd \"$(pwd)\" && .venv/bin/python scripts/daily_run.py --hours 6  --submit --defer-to-agent 45 >> data/logs/daily.log 2>&1"
 echo "5 16  * * * cd \"$(pwd)\" && .venv/bin/python scripts/daily_run.py --hours 14 --submit --defer-to-agent 45 >> data/logs/daily.log 2>&1"
) | crontab -
```

### Tests

`python3 -m pytest tests/` — includes the guarantee that all 710 snapshot
questions parse into priceable families.

### Verifying the automation

`python3 scripts/check_automation.py` — one-shot health check: runs the pipeline
dry-run, reports log freshness, whether the OS crontab is installed, and the live
prediction count. Exit 0 = all green.

**Verifying submissions landed** — the authoritative source is the platform itself:

```bash
# Raw API call: everything stored for your account (independent of our code)
curl -s "https://api.sportspredict.com/api/v1/predictions" \
  -H "Authorization: Bearer $SPORTSPREDICT_KEY_1" | python3 -m json.tool

# Settled predictions with Brier scores
curl -s "https://api.sportspredict.com/api/v1/results" \
  -H "Authorization: Bearer $SPORTSPREDICT_KEY_1" | python3 -m json.tool
```

`python3 scripts/verify_submissions.py` reconciles our submission log against
`GET /predictions`, flagging any market that's MISSING (logged but not stored)
or MISMATCH (stored value ≠ last submitted). Exit 0 = every submission confirmed.

To prove cron itself executes on this machine (the persistent layer), add a
dry-run self-test that runs every minute, wait ~2 min, check the log, remove it:

```bash
(crontab -l 2>/dev/null; echo "* * * * * cd \"$(pwd)\" && .venv/bin/python scripts/daily_run.py --hours 48 --dry-run >> data/logs/cron_selftest.log 2>&1") | crontab -
# wait 2 minutes, then:
tail data/logs/cron_selftest.log         # should show dry-run output
crontab -l | grep -v cron_selftest | crontab -   # remove the self-test
```

## Layout

Two-layer API architecture: a transport layer mirroring the REST API 1:1, and
a domain layer that analysis code imports. **Notebooks and models only import
`probability_cup.ProbabilityCup`** — never raw HTTP.

- `src/sp_api.py` — transport: endpoint wrappers (auth, retry/backoff, batching)
- `src/probability_cup.py` — domain: snapshot caching, DataFrames, upsert `submit()`
- `src/questions/` — parser: question text → `MarketSpec` (19 families, 710/710 covered)
- `src/wcdata/` — data: club CSVs, StatsBomb events, internationals, ESPN odds feed
- `src/models/` — pricing stack (goals / counts / players / pricer / deep / ensemble)
- `src/backtest/` — odds-implied λ solver + walk-forward engine
- `scripts/` — `initialize`, `download_data`, `run_backtest`, `price_match`,
  `submit`, `train_deep`, `kor_cze_quick_model` (legacy v0)
- `notebooks/` — `01_onboarding` (API walkthrough), `02_backtest` (validation report)
- `tests/` — parser coverage; `data/` — datasets + predictions (gitignored)

## Live-API quirks (observed 2026-06-11/12, differ from the docs PDF)

- **`opening_time` is the kickoff = prediction deadline**; `closing_time` is
  ~2.5 h later (≈ match end), despite what the docs say.
- `GET /events` returns `type` as a UUID, not `"probability"` — match by title.
- `GET /predictions` returns `probability` as a percentage (40), not the
  documented 0–1 decimal — `ProbabilityCup.stored_prob_to_int` handles both.
- Match names use FIFA codes (`KOR vs CZE`); questions use full names
  (`South Korea`) — bridged by `src/questions/teams.py`.
- The 60 req/min rate limit is enforced; the client backs off on 429 automatically.

## API cheat sheet

Base `https://api.sportspredict.com/api/v1`, header `Authorization: Bearer sp_live_...`,
60 req/min. Flow: `GET /events` → `GET /lobbies?event_id=` → `POST /lobbies/{id}/join`
→ `GET /matches?event_id=` → `GET /markets?lobby_id=&match_id=` →
`POST /predictions/batch` (≤50) → `PATCH /predictions/{id}` →
`GET /results` (settled, with per-prediction `brier_score`).
409 on submit = already predicted on that market (PATCH instead).
