# Manual Predictions Log - 2026-06-27

Generated: 2026-06-27 16:12 CDT.

Command:

```bash
python3 scripts/manual_odds_anchor.py --date 20260627 --sports-predict
```

Scope:

- Independent manual pipeline only.
- Bot stored predictions were intentionally not read or used.
- SportsPredict API use was limited to read-only market discovery.
- Full count/player/referee model artifacts were unavailable in this checkout because `data/` is absent, so unsupported rows are logged as `NEEDS_RESEARCH` rather than filled with bot values.
- The 16:00 CDT matches were already locked at generation time.

## Match-Level Odds Anchors

| Match | Kickoff UTC | W/D/W | Lambda | Home Scores | Away Scores | BTTS | 3+ Goals | 4+ Goals |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Croatia vs Ghana | 2026-06-27 21:00 | 53/30/17 | 1.52/0.79 | 78% | 54% | 44% | 41% | 20% |
| Panama vs England | 2026-06-27 21:00 | 5/11/84 | 0.60/2.95 | 45% | 95% | 43% | 69% | 47% |
| Colombia vs Portugal | 2026-06-27 23:30 | 28/25/48 | 1.23/1.68 | 71% | 81% | 58% | 56% | 33% |
| DR Congo vs Uzbekistan | 2026-06-27 23:30 | 57/25/18 | 1.60/0.77 | 80% | 54% | 43% | 42% | 21% |
| Algeria vs Austria | 2026-06-28 02:00 | 27/48/26 | 0.92/0.91 | 60% | 60% | 44% | 28% | 11% |
| Jordan vs Argentina | 2026-06-28 02:00 | 5/10/85 | 0.49/2.78 | 38% | 94% | 36% | 63% | 41% |

## Open SportsPredict Markets

### COL vs POR - 18:30 CDT

| Question | Manual Anchor | Source |
|---|---:|---|
| Will both teams score AND the match have 3 or more total goals? | 46% | btts_and_total_ge |
| Will Colombia win the match? | 28% | team_win |
| Will Colombia be caught offside 2 or more times? | NEEDS_RESEARCH | team_count_ge |
| In the second half, will Colombia have more shots on target than Portugal? | NEEDS_RESEARCH | team_more_count_than |
| Will a penalty kick be awarded OR a red card be shown? | NEEDS_RESEARCH | penalty_or_red |
| Will Goncalo Ramos have at least 1 shot on target? | NEEDS_RESEARCH | player_sot_ge |
| Will Luis Diaz score a goal (excluding own goals)? | NEEDS_RESEARCH | player_scores |
| Will there be 5 or more total corner kicks in the second half? | NEEDS_RESEARCH | total_count_ge |
| Will Portugal have 4 or more shots on target? | NEEDS_RESEARCH | team_count_ge |
| Will Colombia have 4 or more shots on target? | NEEDS_RESEARCH | team_count_ge |

### COD vs UZB - 18:30 CDT

| Question | Manual Anchor | Source |
|---|---:|---|
| Will both teams score AND the match have 3 or more total goals? | 31% | btts_and_total_ge |
| Will DR Congo win the match? | 57% | team_win |
| Will Uzbekistan commit more fouls than DR Congo? | NEEDS_RESEARCH | team_more_count_than |
| Will Uzbekistan be caught offside 2 or more times? | NEEDS_RESEARCH | team_count_ge |
| Will both teams have at least 1 shot on target in the second half? | NEEDS_RESEARCH | both_teams_count_ge |
| Will a penalty kick be awarded OR a red card be shown? | NEEDS_RESEARCH | penalty_or_red |
| Will Uzbekistan have more shots on target than DR Congo in the second half? | NEEDS_RESEARCH | team_more_count_than |
| Will Eldor Shomurodov have at least 1 shot on target in the second half? | NEEDS_RESEARCH | player_sot_ge |
| Will Uzbekistan have 5 or more corner kicks? | NEEDS_RESEARCH | team_count_ge |
| Will Cedric Bakambu score or assist a goal (excluding own goals)? | NEEDS_RESEARCH | player_scores_or_assists |

### ALG vs AUT - 21:00 CDT

| Question | Manual Anchor | Source |
|---|---:|---|
| Will Austria score more goals than Algeria in the second half? | 28% | team_more_goals_2h |
| Will Austria score the first goal of the second half? | 33% | team_first_goal_2h |
| Will Algeria win the match? | 27% | team_win |
| Will Austria have more shots on target than Algeria in the second half? | NEEDS_RESEARCH | team_more_count_than |
| Will Algeria commit more fouls than Austria? | NEEDS_RESEARCH | team_more_count_than |
| Will Austria receive more cards than Algeria? | NEEDS_RESEARCH | team_more_count_than |
| Will Algeria have 3 or more shots on target? | NEEDS_RESEARCH | team_count_ge |
| Will Marcel Sabitzer have at least 1 shot on target? | NEEDS_RESEARCH | player_sot_ge |
| Will Austria have 5 or more shots on target? | NEEDS_RESEARCH | team_count_ge |
| Will there be 9 or more total corner kicks? | NEEDS_RESEARCH | total_count_ge |

### JOR vs ARG - 21:00 CDT

| Question | Manual Anchor | Source |
|---|---:|---|
| Will Jordan score in the second half? | 25% | team_scores_in_half |
| Will the match have 2 or fewer total goals? | 37% | total_goals |
| Will Jordan score at least 1 goal? | 38% | team_goals_ge |
| Will a penalty kick be awarded OR a red card be shown in the match? | NEEDS_RESEARCH | penalty_or_red |
| Will Argentina have more shots on target than Jordan in the second half? | NEEDS_RESEARCH | team_more_count_than |
| Will Argentina have 6 or more shots on target? | NEEDS_RESEARCH | team_count_ge |
| Will Jordan commit more fouls than Argentina? | NEEDS_RESEARCH | team_more_count_than |
| Will Jordan be caught offside 2 or more times? | NEEDS_RESEARCH | team_count_ge |
| Will Argentina have at least 1 corner kick in the first half? | NEEDS_RESEARCH | team_count_ge |
| Will Mousa Al-Taamari score or assist a goal (excluding own goals)? | NEEDS_RESEARCH | player_scores_or_assists |

