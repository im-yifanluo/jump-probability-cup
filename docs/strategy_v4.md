# Probability Cup — v4 Strategy (reference)

*Last updated 2026-06-25. Supersedes v3. Lives at `docs/strategy_v4.md`.*

This is the complete description of the live prediction system (v4), why it beats v3,
and the operating discipline around it. Written as a standing reference.

---

## 0. The one thing to remember

**Our score is a proper scoring rule, so the entire game is calibration — reporting
probabilities as close to the true frequency as possible.**

RBP = (crowd's Brier − our Brier), and the crowd term is fixed, so maximizing RBP is
*identical* to minimizing our own Brier. Minimizing Brier ⇒ report your honest
probability. There is **no** reward for being bold, contrarian, or extreme. Every edge
we have is just "be better calibrated than the field," and rank is decided purely by
whose probabilities are closer to the outcomes (the crowd's variance term cancels across
all competitors).

Three corollaries we live by:
1. **Anchor to markets where they exist** (1X2, totals, scorer props) — a de-margined
   betting line is the sharpest calibration available; markets beat our models.
2. **Fade the crowd where it systematically over-prices** — the field over-predicts
   "will this event happen" props (star names, recency, low-event games). Being *lower*
   there is just being better calibrated, not contrarian for its own sake.
3. **Don't manufacture confidence.** Going *above* the crowd is only right when the event
   is genuinely high-probability or there's a confirmed fact the market missed — not
   because one input (a shot rate) looks big.

---

## 1. Pipeline (data → probability)

```
odds feeds ──► ScorelineModel (Dixon-Coles)            ──► goal-derived markets
   │                 │ (λ_home, λ_away, ρ from odds)
   │                 ▼
   └──────────► CountPricer (negative-binomial)          ──► count/threshold + h2h markets
                     │ regress log E[count] on odds-implied λ's
                     ▼
                PlayerModel (rates × minutes × tempo)     ──► player SOT / scorer / S-or-A
```

- **Odds anchor:** ESPN/DraftKings 1X2 + total, **blended 50/50 with Polymarket**
  (independent, sharp). De-margined. This sets λ_home, λ_away.
- **Goals:** odds-anchored Dixon-Coles; the scoreline grid prices win/draw, totals, BTTS,
  halves, "scores in 2H", etc. Goals are essentially market-efficient — we don't beat the
  book here, we match it.
- **Counts (SOT, corners, cards, fouls):** negative-binomial GLMs fit on ~116k club
  matches, with the count regressed on the *odds-implied* goal rates (so the model
  transfers to any fixture once we have its odds). Offsides come from StatsBomb tournament
  rates (no club data), scaled by a weak tempo elasticity (0.18).
- **Club → tournament discount** (`COUNT_INTL_DISCOUNT`, from past tournaments):
  cards 0.849, corners 0.872, sot 0.839, fouls 1.104.
- **WC-2026 per-stat calibration** (`TOURNAMENT_INTENSITY`, the v4 core change — see §3).
- **Referee card factor:** `(ref cards/game)/3.46`, **asymmetrically capped at 1.0** — a
  lenient ref can lower cards, a strict ref can't inflate them (strict-ref inflation was
  our single worst leak).
- **Players:** SOT = player's club SOT/match × (match team-SOT / `TOURNAMENT_TEAM_SOT`)
  × (expected_minutes/90); scorer markets use the **de-margined anytime-scorer prop**
  when available (markets beat models), else a goal-share model. (`src/models/players.py`)

Code: `src/models/{goals,counts,players}.py`, `scripts/price_match.py`,
`scripts/daily_run.py`.

---

## 2. Where the edge actually is (per-family RBP, 383 settled markets)

WINNERS (we fade the crowd's over-prediction and the lower number is closer to reality):

| Family | RBP | Pattern |
|---|---|---|
| team SOT thresholds | +170 | we ~0.34 vs crowd 0.44, realized 0.33 |
| offsides 2+ | +143 | we ~0.43 vs crowd 0.48, realized 0.32 |
| player ≥1 SOT | +125 | we ~0.35 vs crowd 0.45, realized 0.32 |
| more-SOT (h2h) | +117 | territorial favourite |
| team win | +91 | odds-anchored |
| more-fouls (h2h) | +74 | |
| penalty-or-red | +46 | base-rate |

The thread: **the crowd sits ~10–15 pts above reality on low-probability event props; we
sit just above the realized rate.** That gap is the edge. It is a *calibration* edge, not
a boldness edge.

HISTORICAL LEAKS (the v4 fix targets these — see §3): total cards (over-predicted), total
SOT (under-predicted), corners (under-predicted).

---

## 3. What makes v4 better than v3

**v3** corrected for WC-2026 being a low-event-feeling tournament with a single **uniform
0.90 multiplier** on all count means. It was a blunt instrument, and it cost us — our edge
**halved** over the back half of the group stage (+3.13 → +1.96 RBP/market) and we slid
from ~#50 toward ~#100.

The diagnosis (383 settled markets + web research) showed WC-2026 is **high-attacking,
low-card** — the *opposite* of the cagey-tournament assumption:
- goals **3.12/game** (+22% vs 2022's 2.56), fouls **24.4** (down from 27.7), yellows
  **~2.17/game**.
- So the count stats needed **opposite** corrections: cards *down* (we were +30 pts over),
  SOT/corners *up* (we were under). One scalar can't do both.

**v4 change #1 — replace the uniform 0.90 with per-stat multipliers** (`src/models/counts.py`):

```
TOURNAMENT_INTENSITY = {cards 0.78, sot 1.40, corners 1.05, offsides 0.90, fouls 0.90}
```

- **cards 0.78** — low-card tournament; the StatsBomb card model was way too high.
- **sot 1.00 (team/player), TOTAL_SOT_SCALE 1.40 (total only)** — the under-prediction is
  TOTAL-SPECIFIC, so the fix is DECOUPLED (2026-06-29). TEAM SOT (team_count_ge/both_teams/h2h)
  and player ">=1 SOT" are well-calibrated (settled gaps ~-0.05) and stay at v4 (intensity 1.00).
  Only the convolved TOTAL (`counts.TOTAL_SOT_SCALE`, also feeding the squad "2+ SOT") is boosted
  1.40x: settled total_count_ge/sot pred 0.54 vs hit 0.83 (n=18; StatsBomb total ~7.4/match but the
  platform realises ~1.4x). A flat sot=1.40 was tried first and REVERTED — it over-corrected TEAM
  SOT (leave-out Brier 0.203->0.230 on 31 team-SOT settles) while only the total needed it.
  Confirmed live: BRA/JPN "8+ total SOT" HIT, v4 Brier 0.476 vs the boost 0.122.
- **corners 1.05** — corners run high; mild boost.
- **offsides 0.90** — UNCHANGED. It's our 2nd-biggest edge and too unstable to re-fit.

Fit by **calibration objective** (minimize our Brier vs realized = the RBP-optimal target),
validated **out-of-sample**: each multiplier improves RBP on *both* chronological halves;
leave-one-match-out gain stays +91…+129 (not driven by any single match). Net on
count-threshold markets: **+266.9 → +379.1 RBP (+112), ≈ +0.29 RBP/prediction (~+11% on
our edge)** — and it compounds in the 2–3× knockout rounds. Club backtest untouched
(tournament-only change); tests pass.

**v4 change #2 — couple the player-SOT normalizer to the SOT multiplier**
(`TOURNAMENT_TEAM_SOT 3.70 → 4.11` in `src/models/players.py`). Raising the SOT level
(change #1) would otherwise have silently inflated every player-SOT prop ~11% and dented
our #1 family. 4.11 = 3.70/0.90 holds player-SOT props *invariant*. (The v3.5/manual entry
lacked this and ran ~11% hot on player SOT — a bug, not a feature; it "beat" v4 on a tiny
settled sample by luck, but v4 is the calibrated choice.)

**v4 change #3 — strategic clarity (the discipline, not code):**
- **Calibration, not extremizing.** We *tested* extremizing (pushing predictions away from
  0.5) and **rejected it**: Brier is strictly proper, our predictions already fit b≈0.72–0.79
  on logit (slightly *over*-confident, not under), and an OOS recalibration buys ~0. The
  crowd is at most weakly under-confident (not significant). Extremizing only helps
  *aggregated* forecasts, which we are not.
- **Override discipline.** The bots underperformed a frozen slate mainly because two live
  lineup overrides backfired (raised Džeko above the crowd → no SOT; cut a "doubtful"
  Nazon → he played and shot). Rule: deviate only for a **confirmed material fact the
  market missed**, keep it **small**, and **never take an above-crowd position on a
  low-base-rate prop** because one input looks high.

---

## 4. Tested and REJECTED (don't re-litigate)

- **Game-state Monte-Carlo simulator** (for "favourite cedes/chases" dynamics): it's
  mean-preserving, so it can't fix a level bias, and a 1,400-match paired backtest gave
  +0.0003 Brier (~noise). The residual SOT correlation it would exploit is ≈0 (47k club
  matches); the convolution is already calibrated. **Do not ship.**
- **Between-team SOT correlation term:** residual home/away SOT correlation is −0.018
  (independent); total-SOT convolution is calibrated on club data (gaps ≤0.007).
  **Not justified.**
- **Extremizing / "go above the crowd on high-prob events":** see §3 change #3.
- **Favorite-longshot odds extremizing:** negligible benefit in de-margined modern odds.

---

## 5. Operating procedure (matchday)

Run `scripts/daily_run.py --hours <h> --submit --agent`. Daily cadence:
- **Morning (48h):** price everything in window, submit, verify.
- **Final passes (5h / 14h):** odds refresh + **confirmed-lineup gate** + scorer-odds for
  today's goal markets.

Per pass:
1. **Lineups:** ruled-out player → `expected_minutes 0` (+ zero `goal_share`/
   `scorer_prop_prob` if they have a scorer market); rotation risk → modest minutes cut.
   **Revert `expected_minutes` when a benched-then-restored player starts again** (this bit
   us once). Confirmed starter → leave the model alone.
2. **Override levers** (which field moves which market): `expected_minutes` scales *only*
   SOT; `scorer_prop_prob` drives "score a goal"; `involvement_share` drives "score or
   assist"; `goal_share` feeds scorer when no prop price. `team` is REQUIRED for minnow
   squads absent from StatsBomb.
3. **Referee:** only matters for *total/team card-threshold* markets (h2h cancels); with
   the 1.0 cap, only a *lenient* ref changes anything. Otherwise moot.
4. **Verify BOTH bots** (`market_status=='open'` counts must match; 0 value mismatches).
   A silent bug once left bot 2 empty — never trust the submit summary, query directly.
5. **Calibration check** (`results_df`): mean Brier per family vs the ~0.21–0.24 band.
   Watch `total_count_ge` — it's trending **down** post-v4 (0.30 → 0.284), the live sign
   the recalibration is working.

---

## 6. Honest limitations / what to watch

- The per-stat multipliers were **group-stage-fit and deliberately conservative**, but KO data
  proved SOT needed a real boost: total/team SOT was under-predicted at sot=1.00, so it was
  re-fit to **1.40** (06-29) on settled + live evidence. bot 2 now probes 1.60 (the realized 0.83
  hit rate hints even 1.40 may slightly under-correct). Other stats still conservative; re-fit
  via `scripts/parse_history_rbp.py` + `scripts/family_calibration.py` as more KO data settles.
- **We don't beat the market on goals/1X2** — that's efficient. Our edge is the ~8/10
  thin markets per match the crowd guesses at. Most of our headline RBP is the crowd's
  shared variance term; our *rank* depends on the thinner directional edge, which the v4
  recalibration is what makes reliably positive.
- Catching #1 is not guaranteed — the gap is large and KO weighting amplifies whoever's
  ahead. v4 is a real, consistent per-prediction improvement, not a silver bullet.
- **Tinker less.** The model is calibrated; respect the crowd/market as a strong baseline.
