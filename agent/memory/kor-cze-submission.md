---
name: kor-cze-submission
description: "First live submission — 10 KOR vs CZE predictions placed 2026-06-12 ~01:56Z, minutes before kickoff"
metadata: 
  node_type: memory
  type: project
  originSessionId: edc0be82-f7ce-4493-be8b-e55f21f51524
---

First live predictions submitted with bot 1 on 2026-06-12 ~01:56Z (4 min before the 02:00Z kickoff), via scripts/kor_cze_quick_model.py: KOR win 36, BTTS&3+ 40, KOR 2H goal 50, Son scores 30, Schick 1+ SOT 66, CZE>KOR HT corners 40, KOR 2+ offsides 57, 2+ cards 2H 62, penalty 30, KOR>CZE 2H SOT 44. Anchors: bookmaker consensus KOR 36.5/draw 31/CZE 33, totals line 2.5 (fitted Poisson rates 1.29/1.31); Son priced off +195/+205 props; Schick off 1.21 SOT/match. Check results via pc.results_df() after the match settles to start tracking calibration ([[prediction-methodology]]).

Still TODO from the user's request: the full system — proper research, clean reusable model package (src/models/), and backtesting (e.g. Elo+Poisson fitted on historical internationals, validated on past World Cups) for the ~70 remaining matches. Next deadlines: CAN vs BIH 2026-06-12T19:00Z, USA vs PAR 2026-06-13T01:00Z. The crowd-edge thesis: tie-inclusive head-to-head markets (~40%) and disciplined market-anchoring beat casual 50%-ish crowd guesses.
