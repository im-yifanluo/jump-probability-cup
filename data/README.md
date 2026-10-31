# data/

| Folder | Tracked | Contents |
|---|---|---|
| `models/` | yes | Fitted model files the pricer loads: count-model coefficients (`count_models.json`), the club backtest's per-family blend weights (`family_blend.json`), and StatsBomb tournament aggregates (two parquet files, derived from [StatsBomb open data](https://github.com/statsbomb/open-data)). |
| `inputs/` | yes | The researched per-match inputs, exactly as they stood for the final: player rates and scorer prices (`player_overrides.yaml`), referee strictness (`referee_overrides.yaml`), confirmed lineups (`match_lineups.yaml`), and bot 2's configuration (`bot2_experiment.yaml`). `manual_odds.yaml` is created on demand. |
| `cache/` | no | The platform snapshot, the club-match cache, backtest outputs, the crowd ledger. All regenerable. |
| `raw/` | no | Downloaded datasets (~950 MB): `probcup download-data`. |
| `logs/` | no | Live run log, submission audit trail, heartbeat, watchdog log. The tournament's logs are archived in [agent/logs/](../agent/logs/). |

Every path is defined once, in [`src/probcup/config.py`](../src/probcup/config.py).

**Note on `count_models.json`.** This is the file that priced the tournament (last written on
June 20). `probcup backtest` refits it as a side effect, and a refit today does not reproduce
the June 20 file, so don't commit one (see the known issues in the main README).
