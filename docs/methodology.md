# Pricing Methodology by Question Type

*Companion to [strategy.md](strategy.md). For every question family the platform asks,
this file states: **Method** — exactly how the probability is computed; **Evidence** — the
research, data, and live observations that justify it. Code references are the ground truth:
`src/probcup/models/goals.py`, `counts.py`, `players.py`, `pricer.py`, and every tuned constant in
`src/probcup/models/calibration.py`.*

**Notation.** λ_H, λ_A = the two teams' expected goals. "The grid" = the joint scoreline
distribution built from λ_H, λ_A. "NB" = negative binomial. "De-margined" = bookmaker implied
probabilities rescaled to sum to 1 (for 1X2) or divided by 1.10 (for player props).

---

## 0. The two shared engines

Everything below is priced from one of two fitted objects per match.

### 0.1 The goal grid (`goals.py`)
**Method.** Fit (λ_H, λ_A, ρ) so that an independent-Poisson scoreline grid with the
Dixon-Coles low-score correction reproduces the **live de-margined market**: the 1X2 odds and
the over/under 2.5 total (ESPN/DraftKings feed, refreshed every run). All goal questions are
then answered by summing cells of this grid, so every answer is consistent with every other.
Half-based questions split each λ into halves using the measured second-half share
(**0.60 at international tournaments**, StatsBomb; club data gives 0.54 — internationals are
more back-loaded).

**Evidence.**
- Published research: even strong ML models fail to beat bookmaker closing odds on match
  outcomes. We never try; we inherit the market's calibration.
- Live confirmation: on the ~zero-crowd-gap families this grid feeds, we accumulated
  +183 RBP (`team_win`, 63 settles) and +198 RBP (`total_goals`, 57 settles) — i.e., the
  grid matches a sharp crowd and wins on rounding/freshness, exactly as an odds anchor should.
- BTTS calibration test: model fitted to 6,000 historical club matches' closing odds
  predicted BTTS 0.514 vs realized 0.519 (z=0.8), calibrated in every decile
  ([`research/validations/btts_calibration.py`](../research/validations/btts_calibration.py)).
- Team-goals calibration test on 12,000 club team-matches: predicted 0.379 vs realized
  0.381 overall ([`research/validations/team_goals_calibration.py`](../research/validations/team_goals_calibration.py)). One documented flaw: the top decile
  (heavy favourites) under-predicts by +0.038 (z=2.9). Left unshipped — no remaining market
  was affected — but recorded for future use.

### 0.2 The count models (`counts.py`)
**Method.** For each stat (corners, cards, SOT, fouls; offsides separately), a per-team NB
with mean `exp(const + a·log λ_team + b·log λ_opp + c·home)` — fitted on ~41k club matches
with closing odds and match statistics (football-data.co.uk) — multiplied by tournament-specific corrections (§7). Totals are the
**exact convolution** of the two team NBs (a sum of unequal-mean NBs is not NB; a shortcut
misprices uneven matches).

**Evidence.**
- The club fit provides the elasticities (how counts scale with team strength); the
  tournament corrections provide the level (§7 lists each with its own validation).
- Live record: count families are the system's largest RBP source (SOT thresholds +642
  player / +337 team; cards +331; corners +223; offsides +163) against a crowd measured
  to sit 5–12 points too high on them (16 games of scraped crowd data, `docs/archive/crowd_history.md`).

---

## 1. Match outcome questions

### "Will X win?" (regulation)
**Method.** Sum of grid cells where X outscores the opponent.
**Evidence.** §0.1. This is the market's own number de-margined; deviation would be a claim
we beat the closing line, which we reject by design.

### "Will X advance / win the tie / win the World Cup / win the third-place match?"
**Method.** `P(win regulation) + P(draw) × P(win the tie-break)`, with the tie-break modeled
explicitly: 30 minutes of extra time as Poissons at ⅓ of each side's 90-minute rate; if still
level, a shootout priced as a near-coinflip — the regulation strength share shrunk toward 0.5
by 75% (`PENS_TILT = 0.25`). Conserves `P(A advances) + P(B advances) = 1` exactly.
"Win the World Cup" and "win the third-place match" are this same computation (those matches
have tie-breaks; a plain regulation-win read would under-price the favourite).
**Evidence.**
- Shootouts are empirically near 50/50 regardless of team strength (public shootout datasets);
  the old proportional-to-odds tie-break over-favoured strong teams, and the explicit version
  lifted underdogs — deployed 06-28 (`research/derivations/knockout_families.py` era) and validated on
  R32 settles.
- Live: `advance` family +78 RBP over 16 settles with a sharp crowd (gap −0.6 pts).

### "Will the match go to extra time?" / "…be tied at the end of regulation?"
**Method.** `P(draw)` off the grid — a regulation draw *is* what sends a knockout tie to ET.
**Evidence.** §0.1; the two phrasings are routed to the same family (settles identically).

### "Will the match be decided by a penalty shootout?"
**Method.** `P(draw) × P(extra time also level)`, using the same ⅓-rate ET Poissons as the
advance model, so the two families are mutually consistent.
**Evidence.** Consistency-by-construction with the advance family; debuted and won on its
first live settles (07-07 QF wave, part of the 8–2 new-family debut).

---

## 2. Total-goal and goal-structure questions

### "Will the match have N+ / exactly N / an odd number of total goals?"
**Method.** The total-goals distribution is the grid's diagonal sums. N+ = tail sum; exact
= single term; odd = sum of odd terms.
**Evidence.** §0.1. Odd-goals is a near-coinflip by construction (settled 0.49 → loss 0.260
on debut — within noise, retained).

### "Will both teams score?" (BTTS)
**Method.** Sum of grid cells with both scores ≥1.
**Evidence.** The 6,000-match calibration test above (gap +0.005). A live "drift" flag
(n=16–18, realized above predicted) was tested against that large sample and ruled
small-sample knockout variance — the model was deliberately left unchanged.

### "Will both teams score AND the match have N+ goals?"
**Method.** Joint sum over qualifying grid cells (never a product of marginals — the events
are correlated through the scoreline).
**Evidence.** §0.1; correlation handled exactly by cell enumeration.

### "Will X win by N+ goals?" / "…be decided by exactly one goal?"
**Method.** Grid cells with the required (signed / absolute) margin. Margins sum to 1 over
all N (verified in tests).
**Evidence.** Debut settle won (one-goal margin 0.38 → br 0.144, 07-07).

### "Will either team score 2+ goals in the same half?"
**Method.** Four (team × half) cells as independent Poissons at the half split;
`1 − Π P(cell ≤ 1)`.
**Evidence.** Built for the final from the same half machinery as every validated half
market (§3); closed-form, unit-tested.

### "Will X score N+ goals?" (team goals)
**Method.** Row/column tail of the grid.
**Evidence.** The 12,000-team-match calibration test (§0.1): calibrated overall; the
documented favourite-tail exception (+0.038 at p≥0.6) is recorded but was never live-relevant.

---

## 3. Half-structure questions

All use per-half Poissons: `λ_half = λ × share`, share = 1−0.60 / 0.60 (1st/2nd half).

| Question | Method |
|---|---|
| "Tied at halftime?" | 1H diagonal sum |
| "X leading at halftime?" | 1H off-diagonal sum |
| "0-0 at halftime?" | `exp(−(λ_H+λ_A)(1−h2))` |
| "Goal in each half?" | `P(≥1 in 1H) × P(≥1 in 2H)` |
| "Both halves same number of goals?" | `Σ_k P(1H=k)·P(2H=k)` |
| "2H more goals than 1H?" | half-Poisson comparison, shrunk 0.67 toward baseline (see Blending, §10) |
| "X scores in both halves / in the 2nd half?" | per-team half Poissons |
| "First goal of the match in the 2nd half?" | `exp(−λ_1H_total) × (1−exp(−λ_2H_total))` |
| "Either team wins both halves?" | per-half Poisson race, summed over the two (mutually exclusive) teams |

**Evidence.**
- The 0.60 second-half share is measured on StatsBomb international tournaments (the club
  0.54 under-prices late goals at internationals).
- Family record: half markets settled +67 (`ht_tied`, 24), +78 (`ht_team_winning`, 15),
  +93 (`team_scores_both_halves`, 6). `h2_more_goals_than_h1` carries a 0.67 blend weight
  from the 314-match StatsBomb backtest (§10).
- goal-each-half / same-goals-halves / either-wins-both-halves all won their live debuts
  (07-07 wave).

---

## 4. First-event questions

### "Will X score the first goal of the match?"
**Method.** Poisson race: `(λ_X / λ_total) × P(≥1 goal)`.
**Evidence.** A persistent-looking live drift (favourites scored first "too often", n=10,
gap −0.19) was tested on all 314 StatsBomb matches: conditional on final score, the
higher-scoring team scored first 88.6% vs the exchangeable-model 87.8% (z=0.35) —
**goals are time-exchangeable; the formula is exact; the live drift was noise**
([`research/validations/first_goal_drift.py`](../research/validations/first_goal_drift.py)). No change was made — this is the reference example
of the "large sample before touching the model" rule.

### "Will the first goal be scored by a player other than A and B?"
**Method.** Convert each named player's anytime probability (market-anchored where quoted)
to a scoring intensity `λ_p = −ln(1−p)`; first-scorer share = intensity share;
answer = `P(≥1 goal) × (1 − Σλ_p/λ_total)`. Own goals fall outside any named set.
**Evidence.** Competing-Poisson identity; debut settle won (0.59 → br 0.168, ARG/EGY).

### "Will the first goal be scored by a single-digit shirt (1–9)?" (incl. ET)
**Method.** `P(≥1 goal in regulation+ET) × 0.383`.
**Evidence.** Jersey share measured on StatsBomb: 269 of 703 tournament goals (38.3%) were
scored by shirts 1–9 (modern attackers wear 10/11/17+). Debut settle won (0.37 → NO).

### "Will the first goal be credited with an assist?"
**Method.** `P(≥1 goal) × 0.582`.
**Evidence.** 58.2% of matches' first goals carried a StatsBomb assist (n=285 first goals;
penalties and own goals carry none).

### "Will a card be shown before the first goal?"
**Method.** Race of two Poisson processes over the match:
`λ_c/(λ_c+λ_g) × (1 − exp(−(λ_c+λ_g)))`, cards at the referee- and scale-corrected total.
The trailing clause "resolves No if neither occurs" is priced by the same formula (the
`1−e^{−Σλ}` factor is exactly that).
**Evidence.** Closed-form race identity; won its live settles (51–52% priced, hit twice).

---

## 5. Timing-window questions ("before the first hydration break", "in stoppage time", …)

**Method.** Each window has an empirical share of match events measured from StatsBomb
timestamps: goals **before30 = 0.228, after75 = 0.243, 1H-stoppage = 0.030, 2H-stoppage =
0.095** (cards/corners/offsides have their own shares). Windowed mean = match mean × share ×
**TIMING_SCALE 0.85**; probability = Poisson tail. Composite windows are arithmetic:
mid-window = 1 − before30 − after75; "either stoppage" = h1stop + h2stop; "1H after the
break" = (1 − h2share) − before30.
**Evidence.**
- Shares: StatsBomb event timestamps across 6 tournaments (314 matches).
- The 0.85 level is the tournament's only knockout-era constant promotion, validated three
  independent ways: family calibration (shipped 0.432 vs realized 0.214, n=28), the live
  bot-2 A/B (0.85 beat 1.0 on 5 of 6 clean settles), and a walk-forward backtest that
  re-prices every settled timing market at candidate scales from its recovered window mean
  (0.85 −1.83 Brier OOS vs 1.0; [`research/validations/timing_scale.py`](../research/validations/timing_scale.py)).
- The deeper 0.70 probe then lost 5 of 5 live settles → 0.85 confirmed as the level, 0.70
  retired. Family net +161 RBP (27 settles).

---

## 6. Count-threshold questions (SOT, corners, cards, offsides, fouls, shots)

### "Will there be N+ total X?" / "Will TEAM have N+ X?" / "Will both teams have N+ X?"
**Method.** Team means from the club-fitted NB (§0.2) × tournament corrections (§7);
team question = NB tail; total = convolution tail × the stat's **total-only scale** (§7);
both-teams = product of the two independent NB tails.
**Evidence.** §0.2 live record. Two observed failure modes are now doctrine:
- Thresholds **near the distribution mean** are fragile to small level errors (lost −22 and
  −31 on 8+/4+ SOT questions); thresholds far from the mean are robust (the 92% "20+ shots"
  call — mean 31.9, threshold 1.5σ below — won +23.4 even in a losing game).
- The crowd's bias is asymmetric by stat (§ strategy v5 edge map): +9..+12 points high on
  SOT/cards, −9 low on total shots. We deviate exactly where the bias is measured.

### "Will TEAM have more X than OPPONENT?" (strictly more; tie → No)
**Method.** Joint enumeration of the two team NB pmfs, `P(A > B)` — ties resolved to No
explicitly.
**Evidence.** Comparison families settled +93 (fouls), +188 (SOT), plus corners; per-team
comparisons are scale-invariant, so total-level corrections never touch them (a deliberate
design invariant, validated when a flat 1.40 SOT scale over-corrected team SOT and was
decoupled to total-only).

### "Will there be N+ total shots (on and off target)?"
**Method.** Shots are not separately fitted: total-shots mean = tournament shots mean ×
(this match's SOT mean / average SOT) — i.e., SOT-tempo-scaled — NB tail with its own alpha.
**Evidence.** `TOTAL_SHOTS_SCALE` was A/B-probed at 1.10/1.20 and **rejected** (net
negative) — the derivation needs no level correction. The crowd runs ~9 points low on these
(measured), making our "high" calls a repeat winner.

### "Will N+ different TEAM players attempt a shot?"
**Method.** Empirical curve `P(≥5 distinct shooters | team shots = s)` (0 below 5 shots,
0.30 at 5, 0.71 at 7, ~1.0 from 13), **integrated over the team's NB shots pmf** — the curve
is steep, so pricing at the mean would bias.
**Evidence.** Curve measured on 627 StatsBomb team-matches (every point n≥21).

### Offsides
**Method.** Offsides have no club coverage and are driven by defensive line tactics, not
attacking strength: mean = StatsBomb team-match mean × (λ_team/1.30)^**0.18**.
**Evidence.** The 0.18 elasticity is measured on StatsBomb (raw correlation with attacking
output ≈ 0.08); an earlier 0.5 assumption over-priced favourites' offsides and was replaced.
Family +163 RBP against a crowd measured +7.4 too high.

---

## 7. The tournament corrections (levels applied to §6)

Each is multiplicative, validated separately, and applied at the narrowest scope that fixes
the observed error:

| Correction | Value | Scope | Validation |
|---|---|---|---|
| `TOURNAMENT_INTENSITY` | cards 0.78 · sot 1.00 · corners 1.05 · offsides 0.90 · fouls 0.90 | per-team means | per-stat replacement of v3's uniform 0.90 after the uniform dial cost rank 50→94; OOS-validated per stat |
| `TOTAL_SOT_SCALE` | 1.50 | totals only | platform counts ~1.4–1.5× StatsBomb SOT: settled totals implied scale 1.49 (CI 1.24–1.78, n=18); promoted 07-02 after live A/B + five corroborations; the 1.60 probe lost its clean settles |
| `TOTAL_CARDS_SCALE` | 0.85 | totals + card-derived families | cards over-predicted at WC; promoted 07-02; the deeper 0.80 probe **failed** its clean A/B (n=11, net +0.107) — 0.85 is the level |
| `TOTAL_OFFSIDES_SCALE` | 1.10 | totals only | same platform-counting logic; promoted 07-02 |
| Referee `card_factor` | (ref cards/g) ÷ 3.46, **capped at 1.0** | all card markets | strictness swings P(4+ cards) 0.24→0.66 (StatsBomb), but WC card counts run below refs' club averages, so only *leniency* is priced: the cap is RBP-validated, and the lenient-ref fades (Barton 2.8) produced the best single card results (4+ cards at 13%, Brier 0.017) |
| `FAVORITE_SOT_BOOST` | 0.06, only when λ_team>λ_opp, capped at 2× supremacy | favourite team SOT | the club-fitted SOT elasticity (0.797) is too flat for WC blowouts; gated so even matches are untouched; the 0.10 probe stayed net-negative and never promoted |
| KO-stage tempo | **no correction** | — | hypothesis "knockout games are lower-tempo" tested on 314 StatsBomb matches: World Cups show *no* KO compression (SOT ratio 1.02–1.07); the dip exists only at AFCON/Copa/Euro24 ([`research/validations/knockout_tempo.py`](../research/validations/knockout_tempo.py)). Two low-event live KO games were variance |

---

## 8. Player questions

### "Will P score a goal?" (anytime, excluding own goals)
**Method — hierarchy.**
1. **A fresh de-margined line for this fixture**: `implied(odds)/1.10`, stored with a
   match tag and auto-expiring after it. Used outright.
2. Otherwise the model: P's share of team goals (`goal_share`, `involvement_share` from
   club data/API, web-grounded where the API is stale) applied to the team's λ.
3. Confirmed bench → **cameo decomposition**: `P(comes on) × P(scores | expected cameo
   minutes at ~1.2× late-game intensity, incl. penalty duty)` ≈ 0.10–0.18 for an elite
   attacker. Never the raw model bench-crash (0.02–0.05), never the stale pre-lineup quote.
**Evidence.**
- Markets beat models on scorers (literature + our own field-RBP analysis: the goals family
  *loses* to the sharp crowd when we model it ourselves).
- The margin strip is itself the edge: across 16 games the crowd sat +4.5 points above us on
  star scorers (it follows the raw quote); anchored calls beat the crowd in every knockout
  round (Messi 53/48/39 vs crowds 59/55/45; family +264 RBP).
- Cameo decomposition: validated on Kane (benched, priced 15% vs crowd 36% → +31.6) and its
  inverse lesson Dembélé (bench-crashed to 5%, hit at crowd 38 → −94, the tournament's worst
  market — see the consistency sweep below).
- Bench catches overall: Marmoush 14 (crowd 37), Lukaku 20 (crowd 45), Kane 15 (crowd 36) —
  the crowd lags posted lineups by design; the confirmed-XI pull (≤100 min pre-kickoff) is
  the decisive input, and preview/press lineups are explicitly *not* trusted (they were
  wrong about Kane and Álvarez in the same week).

### "Will P score or assist?" (S/A)
**Method.** Modeled from `involvement_share` (share of team goal events P participates in):
`S/A = 1 − exp(−involvement_share × λ_team)`. **Mandatory consistency sweep on every board**:
S/A must be ≥ the player's de-margined anytime line plus an assist uplift, and ≤ any ceiling
the line implies. Tune `involvement_share` until it holds (props do not feed this family
directly).
**Evidence.** The sweep exists because both failure directions occurred live:
- Floor violations: Bellingham 0.21 vs an implied ~0.35 anytime (6 tournament goals — a
  form-lagged share) → corrected to 0.43, beat the crowd; Álvarez, Arias same pattern.
- Ceiling violation: Yamal S/A 0.61 against a +550 anytime (~0.14) → cut to 0.41, won +24.
- The one un-swept board (Dembélé, 0.19 stale starter share → 0.05 as sub) cost −94 and
  converted this from a habit into a rule.

### "Will P have N+ shots on target?"
**Method.** Per-90 club SOT rate (`sot_per_match`, web-grounded when the API feed is stale or
mis-matched) × international discount 0.839 × match tempo `min(team_SOT/4.11, cap)` ×
confirmed minutes/90; NB tail. For the N=1 threshold with a genuine shooter (rate ≥0.5),
tempo swing is halved (`SOT1_TEMPO_BETA = 0.5`) — ≥1 SOT saturates.
**Evidence.**
- **Never market-anchored** — this is the model's best family: +642 RBP over 107 settles
  against a crowd measured +11.5 points too high.
- The tempo compression is backtested ([`research/validations/sot1_tempo.py`](../research/validations/sot1_tempo.py), StatsBomb).
- Stale-rate discipline: Bellingham's API rate (0.65) burned three settles before web
  grounding (1.3); Balogun carries a "do not auto-update" guard (API name mis-match to a
  defender). Rates are inputs to be researched, not trusted blindly.

### "Will P record more SOT than Q?" (player vs player)
**Method.** Independent Poissons at each player's tempo/minutes-scaled SOT mean (no
threshold-1 compression — that is a ≥1-family calibration); `P(A > B)`, tie → No.
**Evidence.** Same machinery as the validated SOT family; built for the final.

### "Will any TEAM player have 2+ SOT?"
**Method.** Lookup `P(some player ≥2 | team SOT total)` (0.12 at 2 team SOT … 0.99 at 9),
weighted over the team's SOT pmf.
**Evidence.** Table derived from StatsBomb player-level data (628 team-matches).

### "Will P play the entire match?"
**Method.** Confirmed starter → positional full-90 rate {GK 0.95, DEF 0.79, MID 0.575,
FWD 0.282}; expected minutes <89 → 0.02.
**Evidence.** Starters' full-90 shares measured on the **5-substitute era only** (199
matches — 3-sub-era data under-counts substitutions). Star attackers get subbed: the FWD
0.282 priced Pulisic correctly on debut.

### "Will GK make N+ saves?"
**Method.** Saves ≈ opponent SOT − opponent goals; NB tail on that mean (floor 0.3).
**Evidence.** Identity-based; one live miss (a dominated keeper's workload under-read) is a
recorded watch item — the family is priced but flagged as the model's weakest.

---

## 9. Squad / substitution / special questions

| Question | Method | Source |
|---|---|---|
| "Substitution before halftime?" | flat 0.197 | StatsBomb match scan |
| "Substitution at halftime?" | flat 0.497 | 5-sub era, n=199 (HT subs are logged at period 2, minute 45) |
| "Will TEAM make the first substitution?" | 0.50 | P(first-listed team subs first) = 0.506, n=314 — symmetric; who trails is unknowable pre-match |
| "N+ total substitutions?" | tail lookup {6: .97, 7: .92, 8: .84, 9: .72, 10: .41, 11: .01} | 5-sub-era distribution |
| "A substitute scores / scores-or-assists?" | `Σ_G P(G) × P(sub goal | G)` table (0.07 at G=1 … 0.90 at G=6) | derived 5-sub era (`research/derivations/substitute_scorers.py`). The parser routes "a substitute **scores**" here (it once fell to a player-name prior and cost −47). **Known issue:** the score-*or-assist* phrasing still misparses as a player named "a substitute" and is priced at the generic player prior (~0.21), including in the final |
| "Any player scores 2+ (brace)?" | `Σ_G P(G) × q_brace(G)` | StatsBomb brace share by total goals |
| "Own goal / header goal / outside-box goal?" | `Σ_G P(G) × P(≥1 of type | G)` per-type tables | StatsBomb, leave-one-tournament-out validated (`derive_goal_methods.py`); these markets were forfeited before 06-30 |
| "TEAM holds a lead at any point?" | exact decomposition: P(led\|won)=1, P(led\|drew, scored first)=1, P(led\|drew, didn't)=0.073, P(led\|lost)=0.132, composed with the grid's result probabilities and first-goal share | 314 matches / 628 team-matches (`derive_r16_families.py`); won on debut |
| "Penalty awarded?" / "Red card?" / "Penalty or red?" | tournament base rates from StatsBomb match flags | family +94 RBP (37 settles), crowd gap ≈ 0 — priced honestly, no deviation |
| "Penalty **scored**?" | `P(pen awarded) × 0.725` | conversion = 79/109 regulation penalties, StatsBomb |
| "More cards than goals?" | joint sum over the (referee-corrected) card pmf × goal pmf | exact convolution; card side carries §7 corrections |
| "≥1 card in each half?" | per-half convolved two-team NB tails × card corrections, product of halves | card sibling of goal-each-half; won debut (0.37 vs crowd, Barton game) |
| "VAR on-field review?" | flat 0.30 — **flagged estimate** | no clean event source; the one number in the system without data behind it (won its only settle) |

---

## 10. Cross-cutting rules

### Blending toward a structural baseline
Families whose model showed weak OOS skill are shrunk toward the same machinery run at
tournament-average strengths: price = baseline + w × (model − baseline), with backtested
weights (cards 0.40, corner totals 0.94, fouls comparison 0.82, 2H>1H 0.67, …).
**Evidence.** 314-match StatsBomb backtest set the weights; a later attempt to shrink
*everything* (a global cap) was tested and **reverted** — head-to-head and SOT families are
calibrated and shrinking them hurts. Shrink only what the backtest says to shrink.

### The change-control pipeline (what it takes to touch a constant)
Candidate → bot-2 live A/B probe (one notch) → promote only on ~8–10 **clean** own-family
settles in one direction, where "clean" means settled *after* bot 1's last level change for
that constant (an era-confounded tally once showed 10/13 for a probe whose clean sample was
n=0). Calibration drift flags (|gap|>0.15, n≥8) are candidates for **large-sample
validation**, never direct fixes: three of four knockout-era flags (first-goal, BTTS,
KO-tempo) were killed by StatsBomb/club-data tests; the one that survived every test
(timing) was promoted. Full history: `data/logs/experiment_log.md`.

### Question parsing
Every question must parse to a family (100% enforced by test) — an unparsed question is a
forfeited market. Knockout re-phrasings are normalized by stripping the regulation qualifier
("in regulation (90 minutes + stoppage time[, …])"), which let ~50 group-stage rules serve
every knockout board; genuinely new templates get families built same-day (19 during the
knockouts). Subject checks prevent team-as-player and "a substitute"-as-player mis-parses,
both of which produced real losses before the checks existed.

---

*Last updated 2026-07-19, post-final (paths updated for the restructure). Numbers cited from:
`probcup ledger` (per-family RBP and crowd gaps over 16 games), `probcup drift` (1,163 settled
markets), the validations in `research/validations/`, and the StatsBomb derivations in
`research/derivations/`.*

---

## References

1. **Walsh, C. & Joshi, A. (2024).** "Machine learning for sports betting: should model
   selection be based on accuracy or calibration?" *Machine Learning with Applications.*
   [doi:10.1016/j.mlwa.2024.100539](https://www.sciencedirect.com/science/article/pii/S266682702400015X)
   · [arXiv:2303.06021](https://arxiv.org/abs/2303.06021). — Calibration-selected models
   flipped NBA betting returns from −35.2% to +34.7% vs accuracy-selected ones. Basis for
   the honest-calibration doctrine (§ strategy v5 §1).
2. **Hubáček, O., Šourek, G. & Železný, F. (2019).** "Exploiting sports-betting market using
   machine learning." *International Journal of Forecasting* 35(2).
   [ida.fel.cvut.cz/papers/hubacek2019learning](https://ida.fel.cvut.cz/papers/hubacek2019learning.html).
   — Best XGBoost model RPS 0.2063 vs bookmaker 0.2020 on soccer outcomes: strong ML loses
   to the closing line. Basis for anchoring the goal model to the market (§0.1).
3. **Dixon, M.J. & Coles, S.G. (1997).** "Modelling Association Football Scores and
   Inefficiencies in the Football Betting Market." *Applied Statistics (JRSS-C)* 46(2),
   265–280. — The Poisson + low-score-correction scoreline model implemented in `goals.py`.
4. *Softer empirical grounds (no single canonical paper):* shootout near-coinflips (win-rate
   data; `PENS_TILT 0.25` is a modeling choice), NB overdispersion for corners/cards
   (confirmed by our own α>0 fits on ~41k club matches), second-half goal share
   (our measurement: 0.54 club, 0.60 international).
