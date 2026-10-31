# Strategy (v5): the system that ran the knockouts and the final

*Written 2026-07-19, after the final. This documents the **exact strategy as it ran** for the
knockout rounds and the final — the evolved successor to [v4](archive/strategy_v4.md).
Final standing (leaderboard after the final settled): **bot 1 #17 of 4,013 overall (#8 in the
knockout stage); bot 2 #31 overall and #5 in the knockout stage.** Overall Brier 0.2197 (bot 1)
and 0.2174 (bot 2). When this was written, the night of the final, bot 1 stood 31st.*

> **Per-question-type reference:** [methodology.md](methodology.md) states, for every question
> family, the exact pricing method and the research/observations behind it.

---

## 1. Objective and the scoring math that drives everything

The platform scores each settled market by Brier: `(p − outcome)²`. Our leaderboard metric is

```
RBP = Σ over markets  (crowd_brier − our_brier) × 100 × stage_weight
      stage_weight: group 1× / knockout 2× / final 3×
```

Two mathematical facts dictate the whole strategy:

1. **RBP is linear in our own Brier** → the optimal report is our *honest, calibrated*
   probability. There is no game-theoretic shading against the crowd.
2. **Brier is a strictly proper scoring rule** → reporting p when our belief is t costs
   `(p−t)²` in expectation, *quadratically*. Deviation amplification ("boldness") trades a
   bounded gain for an unbounded-ish loss: pushing 0.60→0.90 wins +45 RBP (at 3×) when right
   and −135 when wrong, EV −27. We measured this twice (backtest engine OOS: every global
   boldness dial lost; final-week binomial analysis: 2–3× amplification bought ~1pt of
   tail probability at −40..−80 EV) and therefore **never amplified a ranked entry**.
   Variance plays are for throwaway entries only (§10).

**Edge therefore comes from exactly three places:** being calibrated where the crowd is
biased (§6), covering markets the crowd/other models forfeit (§7), and sharper inputs —
odds, lineups, referee, player form (§4–5).

## 2. Architecture

```
snapshot (platform API)                       data/cache/snapshot.json
  └─ questions/parser.py    455+ templates → ~40 structured MarketSpec families (100% parse)
       └─ models/pricer.py  one MatchPricer per fixture routes every family:
            ├─ models/goals.py    ScorelineModel — Dixon-Coles grid FITTED TO LIVE ODDS
            ├─ models/counts.py   NB count models (corners/cards/SOT/fouls/offsides) + derived families
            └─ models/players.py  scorer / SOT / involvement props (market-anchored, §4)
  └─ probcup run            prices + submits BOTH bots (bot 2 first, bot 1 last), pulls
                            confirmed XIs, expires stale props, logs everything
```

- **Goals**: independent Poisson + Dixon-Coles tau, `(λ_home, λ_away, ρ)` fitted to the
  **de-margined live 1X2 + O/U line** (ESPN/DraftKings). Decades of research + our own live
  record say the closing market is the best-calibrated public forecast — every goal-derived
  market (BTTS, halves, margins, timing splits, advance/ET/pens) is enumerated from this one
  grid, so all answers are mutually consistent.
- **Counts**: negative-binomial per stat, per-team means from club-fitted elasticities
  (`log λ_team`, `log λ_opp`), tournament-adjusted (§5). Totals are exact convolutions of the
  two team NBs — never an alpha shortcut.
- **Players**: per-90 club rates × expected minutes × match tempo, overridden by market
  anchors wherever a fresh line exists (§4).
- **Two live entries**: **bot 1 = stable leaderboard entry** (changes only via §8's
  promotion rule), **bot 2 = live A/B bench** (`data/bot2_experiment.yaml` overrides
  constants per-run). Retired to `bot 2 ≡ bot 1` for the final once no future game remained
  for a probe to inform.

## 3. Pricing hierarchy (which source wins)

1. **Market odds for this exact fixture** — moneyline/total anchor the goal model; a
   de-margined anytime-scorer line anchors that player's scorer market outright.
2. **Odds-fitted model output** — everything derivable from the anchored grid/NB models.
3. **StatsBomb-derived conditional tables** — for families with no odds and no closed form
   (hold-lead, sub-scores, goal-methods, distinct-shooters, jersey shares, HT-sub rates…).
4. **Base rates + researched priors** — only when nothing above exists; always flagged.

## 4. The player doctrine (the most-litigated part of the system)

**Scorers → market. Shots → model. Everything scoped to one match.**

- **Anchor rule**: `scorer_prop_prob = implied(anytime line) / 1.10` (margin strip), tagged
  `scorer_prop_match: "<fixture>"`. A prop for another fixture **auto-expires loudly** back
  to the model — a prop is an opponent-specific price. The margin strip alone is a
  systematic edge: across 16 games the crowd sat ~4.5 pts above us on star scorer markets
  (they follow the raw quote; we strip the vig) and those anchors netted +RBP in every
  knockout round.
- **The S/A consistency sweep** (the Dembélé rule, learned at −94): for **every**
  score-or-assist market on a board — not just researched stars — S/A must satisfy
  `S/A ≥ de-margined anytime + assist uplift`. Tune via `involvement_share` (S/A does not
  read the prop field). This caught Bellingham 0.21→0.43, Álvarez 0.37→0.49, Arias, and in
  reverse capped Yamal 0.61→0.41 against a +550 line. Its one missed application (Dembélé,
  stale 0.19 starter share for a 5-goal player, bench-crashed to 0.05, hit at crowd 38) was
  the single worst market of our tournament.
- **The benching protocol**: the ESPN confirmed XI (auto-pulled ≤100 min pre-kickoff)
  outranks every prediction, preview, and pre-lineup quote. Minutes only rescale **SOT**;
  a benched scorer's **score/anytime must be hand-priced by cameo decomposition**:
  `P(comes on) × P(scores | expected minutes at ~1.2× late-game intensity, incl. pen duty)`
  ≈ 0.10–0.18 for an elite attacker — never the raw model bench-crash (0.02–0.05) and never
  the stale start-quote. Bench catches were the tournament's biggest single-market wins:
  Marmoush 14 (crowd 37), Lukaku 20 (crowd 45), Kane 15 (crowd 36) = +32/+41/+32.
- **The public-superstar exception** (Salah rule): if an independent model corroborates our
  low number and the player is a heavy public bet, lift only *mildly* toward the market —
  public-shortened lines are not sharp lines.
- **SOT props are never market-anchored**: they are the model's calibrated home turf
  (`player_sot_ge` = +642 RBP, our best family) — club `sot_per_match` grounded by research
  when StatsBomb is sparse, tempo-compressed for the ≥1 threshold (`SOT1_TEMPO_BETA = 0.5`),
  minutes-scaled by the confirmed XI. A 0.12-prior price on a covered team's player is
  treated as a name-resolution bug, never accepted.

## 5. Calibration constants (exact values + provenance)

All corrections are multiplicative and **level-specific**: per-team and comparison markets
are scale-invariant, so totals carry their own corrections and player/team levels are never
touched by total-level fixes.

| Constant | Value | Evidence |
|---|---|---|
| `TOURNAMENT_INTENSITY` (per-stat) | cards 0.78, sot 1.00, corners 1.05, offsides 0.90, fouls 0.90 | OOS-validated per-stat replacement of a uniform 0.90 (v3's uniform dial cost rank #50→#94) |
| `TOTAL_SOT_SCALE` | **1.50** | platform counts ~1.4–1.5× StatsBomb totals; implied 1.49 (CI 1.24–1.78) on settled totals; promoted 07-02 after live A/B + 5-way corroboration |
| `TOTAL_OFFSIDES_SCALE` | 1.10 | same total-only pattern; promoted 07-02 |
| `TOTAL_CARDS_SCALE` | 0.85 | cards over-predicted at WC; promoted 07-02; the deeper 0.80 probe **failed** (n=11 clean, net +0.107) and was retired |
| `FAVORITE_SOT_BOOST` | 0.06, supremacy-gated | club-fitted SOT elasticity (0.797) too flat for WC blowouts; boost only when `λ_team > λ_opp`, capped; the 0.10 probe stayed net-negative (5/13) |
| `TIMING_SCALE` | **0.85** | the only knockout-era promotion: timing windows over-predicted (realized 0.214 vs shipped 0.432, n=28); validated three ways — live A/B 5/6, family drift +0.146, walk-forward backtest (0.85 −1.83 Brier OOS). The deeper 0.70 probe then went **0/5 live** and was retired: 0.85 is the level |
| Referee `card_factor` | `(ref cards/game)/3.46`, **capped at 1.0** | strictness swings P(4+ cards) 0.24→0.66, but the cap is RBP-validated: only *lenient* refs move our cards (Barton 2.8 → the 13% four-cards call, br 0.017). Strict refs stay neutral |
| `SOT1_TEMPO_BETA` | 0.5 | ≥1-SOT saturates in tempo for genuine shooters |
| `PENS_TILT` / `ET_FRACTION` | 0.25 / ⅓ | shootouts are near-coinflips; ET is 30' at the regulation rate. Conserves `p_advance(A)+p_advance(B)=1` |

Derived-family tables (StatsBomb, n in parens): pen conversion 0.725 (109), first-goal
assisted 0.582 (285), single-digit-shirt scorer share 0.383 (703), HT-sub rate 0.497
(199, 5-sub era), distinct-shooters curve by team shots (627; integrated over the shots pmf
because the curve is steep), hold-lead conditionals, sub-scores by total goals, full-90
rates by position {GK .95, DEF .79, MID .575, FWD .282}.

## 6. The edge map — where RBP actually came from

Ground truth from 16 games of scraped crowd data (`docs/archive/crowd_history.md` → `probcup ledger`).
Mean crowd−us gap per family, and cumulative RBP:

| Family | Crowd sits | Cum. RBP | Play |
|---|---|--:|---|
| `player_sot_ge` | **+11.5 pts above us** | **+642** | deviate-low SOT fades — the crown jewel |
| `team_count_ge/sot` | (same bias) | +337 | |
| `total_count_ge/cards` | +9.6 above | +331 | card fades, deepened by lenient refs |
| `player_scores` | +4.5 above (name bias) | +264 | de-margined anchors |
| `team_count_ge/corners` | +5.1 above | +223 | corner fades |
| `total_goals`, `team_win` | ~0 (sharp crowd) | +198/+183 | ride the odds anchor, no deviation |
| `team_count_ge/offsides` | +7.4 above | +163 | offside fades |
| `timing_event` | ~0 after the 0.85 fix | +161 | |
| substitute S/A | +8.8 above | — | fade, but see Dembélé rule |
| `total_shots` | **−8.9 below us** | — | the crowd under-prices open games; our "high" calls (92 vs 71) print |

Reading: **the crowd systematically over-prices event occurrence on niche count markets and
follows raw quotes on stars; it is nearly unbeatable on outcomes and totals-of-goals.**
Deviate where the bias is measured; anchor where the crowd is sharp. "Deviate-low on soft
markets" was worth +479 RBP by the R32 analysis and kept printing through the final.

Structural jackpots (rarer, bigger): confirmed-XI bench catches (+30..+41 each), lenient-ref
card fades, exotic-family coverage the field forfeits (§7). Games with a jackpot ran +200
to +264; games without capped near +50–120 — that spread is environmental, not process.

## 7. Coverage doctrine

**A forfeited market is 0 expected RBP; a priced one on a calibrated model is positive.**
Every new template is built same-day, closed-form where possible, StatsBomb-derived
otherwise, unit-tested, and the full snapshot must parse 100% (enforced by test). 19
families were built during the knockout rounds alone (KO re-phrasings, goal-method,
advancement with explicit ET+pens, odd-goals, margins, first-goal splits, card-each-half,
stoppage/hydration windows, VAR, single-digit scorer, first-substitution, distinct-shooters,
player-vs-player SOT, …). New families debuted 6/7 and 5/6 on first live settles. The
regulation-suffix normalizer (strips "in regulation (90 minutes + stoppage time[, …])")
is what lets group-stage rules serve every KO re-phrasing.

## 8. The validation discipline (how changes reach bot 1)

**Bot 1 changes only through this pipeline. No exceptions mid-run.**

1. **Hypothesis** → runs as a bot-2 probe (one notch, disjoint families, bot 2 stays
   competitive) via `bot2_experiment.yaml`.
2. **Scoring** → `probcup replay ab` replays settled divergent markets. A probe
   promotes on **~8–10 clean own-family settles in a consistent direction**; it retires
   when clearly losing. **Confound filter**: only settles *after* bot 1's last level change
   for that constant count (the totalSOT "10/13" that was really n=0 clean nearly caused a
   bad promotion).
3. **Drift flags** (`probcup drift`, |gap|>0.15, n≥8) are *candidates*, never
   fixes: every flag gets a **large-sample validation** before any change — StatsBomb
   (n=219 cleared first-goal "drift" as noise), club data with the real model (n=6000
   cleared BTTS; n=12000 confirmed team_goals_ge overall but found a real +0.04
   favourite-tail miss, z=2.9 — documented, unshipped, as no remaining market was affected),
   walk-forward on recovered window-means (promoted timing 0.85).
4. **Scorecard**: of every tempting change, exactly **one** global promotion survived the
   full pipeline in the knockout era (timing 1.0→0.85); the rest were rejected or retired.
   The backtest engine's standing verdict held all tournament: *no global boldness dial wins
   OOS — edge is model-leak fixes + coverage + sharper inputs.*

Live-sample rules of thumb burned into the process: a sub-2σ drift at n<20 is noise until a
large sample says otherwise (3 of 4 flags died this way); a probe tally that ignores the
era-confound is not evidence; five correlated settles don't retire a backtest-validated
level — but 0/5 in a consistent direction does end a probe.

## 9. Operations

- **Cadence**: one **morning run** (evaluate settles → probe scoring → research today's
  fixtures → submit both bots → output tables for the manual entry → schedule the day), then
  one **final pass per game** firing ~50 min pre-kickoff (confirmed XI + final odds +
  referee + scorer lines → resubmit). The last game of the day is the "night" variant
  (also prices tomorrow).
- **Submit order is always bot 2 first, bot 1 last** (contamination safety), then verify
  both bots' open counts are **equal** by querying the platform — never trust the submit
  summary (a silent bug once left bot 2 empty for ~15 matches).
- **Scheduling is two-layer** (learned by losing a final pass to a session restart):
  session-level crons do the researched runs; a **durable OS-cron watchdog**
  (`probcup watchdog`, every 20 min, survives reboots) *never submits* — it alarms
  (desktop + sticky file) when a run was missed. **No mechanical auto-submit exists by
  decision**: a low-quality submission bleeds RBP (negative), a missed refresh only loses
  freshness (we are always already submitted from the prior pass). Quality > coverage.
- Every run leaves a trace: `runs.log`, `submissions.csv`, `matchday_reports.md`,
  `experiment_log.md`; settled crowd data is pasted into `docs/archive/crowd_history.md` (the only crowd
  source) and parsed into the per-family RBP ledger.

## 10. Risk policy

- **Ranked entries are never amplified** (§1 math). Tail-trimming applies instead: no
  price <8% unless structurally certain (the −94 lesson: a wrong 5% at 3× is −280), the
  S/A sweep before every board, cameo decomposition for benched stars.
- **Variance tickets live only on rank-irrelevant entries** (the hand-entered manual). The
  sanctioned construction: `p_manual = clip(2·p_honest − crowd_est, 3, 97)` per market,
  with `crowd_est = p_honest + measured family gap` (§6 table) — i.e., double only the
  *measured* biases, leave sharp-crowd markets at honest, mild scenario-shade on outcomes.
  Expected cost ≈ −40..−80 vs honest; meaningfully fatter right tail.
- Endgame objectives are stated *before* the last round and the play derived from them
  (final day: protect bot 1's top-1% + extend bot 2's record → both bots honest and
  identical; the lottery went manual-only).

## 11. The lessons ledger (post-mortems that became rules)

| Cost | Lesson → standing rule |
|---|---|
| −94 (Dembélé) | S/A sweep every board; stale shares scream (5-goal player at 19%); elite-sub cameos ≥0.10 |
| −47 (BEL/SEN sub) | "a substitute" is a family, not a player name — parser subject checks |
| 3× burns (Bellingham 2+SOT) | stale API rates get grounded by web research; overrides carry "DO NOT auto-update" guards where the API mis-matches (Balogun) |
| −22, −31 (near-mean totals) | total-count thresholds near the mean are level-error-fragile; far-from-mean calls (20+ shots at mean 31.9) are robust boldness |
| rank #50→#94 (v3) | never ship a uniform global dial; calibrate per-stat |
| missed final pass 07-11 | session-only scheduling is not scheduling; durable watchdog + loud failure, no silent auto-submit |
| Kane +31.6 / Álvarez flip | pre-lineup quotes and press reports are wrong often enough that only the posted XI decides; a stale start-quote on a benched player is the field's leak and our win |

## 12. What we would keep vs. change next time

**Keep**: odds-anchored goal grid; per-stat multiplicative corrections with total-only
levels; market-anchor + margin-strip on scorers; deviate-low on the measured-bias count
families; the bot-2 live A/B bench with the clean-settle promotion bar; large-sample
validation before touching the ranked bot; same-day coverage builds; the two-layer
ops with alert-only durability; post-mortems that end as executable rules.

**Change**: run the S/A sweep and the favourite-tail audit from day 1 (both were learned
mid-tournament); capture crowd values programmatically every settle (our biggest analysis
asset arrived by hand-paste); pre-derive the full KO/final template families before the
tournament (they are knowable from any prior World Cup's boards); and keep a standing
cameo-decomposition price for every elite bench player the moment lineups post.

---
*Provenance: constants in `src/probcup/models/calibration.py`; validations in `research/`
(index: [research/README.md](../research/README.md)), `probcup drift` and `probcup replay`;
decision log in [agent/logs/experiment_log.md](../agent/logs/experiment_log.md); per-run history in
[agent/logs/run_reports.md](../agent/logs/run_reports.md); crowd ledger in `docs/archive/crowd_history.md`.*
