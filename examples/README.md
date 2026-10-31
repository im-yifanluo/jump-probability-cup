# examples/

- **[boards.json](boards.json)** — 101 real match boards from the tournament (1,153 markets):
  every question with its platform market id, rebuilt from the settled-results export
  ([results/data](../results/data/)). For nine boards the home/away order is inferred.
- **[odds.yaml](odds.yaml)** — the odds anchors for the World Cup final, recovered from what
  bot 1 submitted by [recover_odds.py](recover_odds.py): fit the three anchors on the four
  goal-only markets, and the other 16 markets on the board come out matching bot 1's live
  submissions.

```
probcup demo                   # price the final offline; compare with what was submitted
probcup demo --list            # every board
probcup demo --match "FRA vs ESP" --odds 0.37 0.32 2.64
```

The researched inputs in `data/inputs/` are the versions from the final, so other boards are
priced with later player rates and props than they were live.
