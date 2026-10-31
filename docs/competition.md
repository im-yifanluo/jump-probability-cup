# The competition

The [Jump Trading Probability Cup](https://www.jumptrading.com/signals/introducing-the-jump-trading-probability-cup)
was a free forecasting contest sponsored by Jump Trading and hosted on
[SportsPredict](https://sportspredict.com/probabilitycup) during the 2026 FIFA World Cup
(June 11 – July 19, 2026), run as a talent search for Jump's prediction-markets team. First
prize was a paid ten-week fellowship in Chicago ([official rules](https://sportspredict.com/probability-cup-terms)).
4,013 forecasters were ranked on the final leaderboard. The contest is not affiliated with FIFA.

## What you forecast

About ten yes/no questions per match across all 104 matches: player and team shots on target,
corners, cards, offsides, scorers, halftime states, timing windows ("a goal before the first
hydration break"), combinations ("both teams score AND 3+ goals"), and in the knockouts,
advancement, extra time and penalties. Each forecast is a whole-number probability from 1 to 99,
editable until kickoff, settled on Opta data.

Bots were explicitly allowed ("Use any tool, model, or bot. We score forecasts, not methods"):
each account could connect up to two through the platform's
[Model API](https://sportspredict.com/probability/api), each ranked as its own leaderboard
entry, separately from the account's hand-entered forecasts. The platform also entered Claude,
GPT and Gemini as benchmark bots, each prompted 30 minutes before kickoff.

## Scoring

Each settled forecast gets a Brier score, `(p − outcome)²`, lower is better. The leaderboard
ranks the cumulative **Relative Brier Points** ([scoring FAQ](https://sportspredict.com/probabilitycup/scoring)):

```
RBP = (field-average Brier − your Brier) × 100 × stage weight
      stage weight: group stage 1×, knockout rounds 2×, final 3×
```

Three consequences shaped the whole system:

1. **Honesty is optimal.** The field's term is the same for everyone, so expected RBP is
   maximized by minimizing your own expected Brier, and Brier is a *strictly proper* scoring
   rule: the best report is your true probability. Shading toward or away from the crowd, or
   "extremizing", lowers the expected score. (Tested and rejected twice; see
   [strategy.md §1](strategy.md).)
2. **You are scored against the average participant, not the average forecast.** Because Brier
   is convex, the field's average Brier is worse than the Brier of the field's average
   probability. A calibrated forecast that sits right on the crowd's mean still earns points:
   bot 1 averaged +3.5 points on markets where it was within 5 points of the crowd.
3. **Late rounds dominate.** Knockout forecasts count double and the final triple. Bot 1 earned
   +501.6 of its 4,732 points on the final's 20 markets.

## Our entries

| Entry | What it ran | Overall | Knockout stage |
|---|---|---|---|
| Bot 1 | the stable model | **#17** of 4,013 | #8 |
| Bot 2 | the live A/B bench (stable model + one candidate change at a time) | #31 | **#5** |
| Manual | hand-entered on the app; not produced by this system | — | — |

Bot 2 missed its first ~15 matches to a silent submission bug ([agent/README.md](../agent/README.md#what-went-wrong-and-what-changed-because-of-it)),
which is most of the overall gap between the bots; over the markets it did play, its Brier
score was slightly better (0.2174 vs 0.2197).
