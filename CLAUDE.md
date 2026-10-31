# Working in this repository

Forecasting system for the Jump Trading Probability Cup (2026 FIFA World Cup), operated live
by an agent from three playbooks in `.claude/commands/`. The tournament is over; this is the
archived, runnable system. Start with `README.md`; how it ran is in `agent/README.md`.

## Commands

```bash
make setup       # .venv with the exact tournament dependencies (requirements.lock)
make test        # offline unit + golden tests (~30 s); must stay green
make demo        # price the World Cup final offline, compare with what was submitted
.venv/bin/probcup --help
```

Run Python with `PYTHONPATH` unset (the Makefile does): this machine's shell exports a ROS
`PYTHONPATH` that otherwise leaks packages and pytest plugins in.

## Layout

- `src/probcup/` — the package. `models/` (goal grid, counts, players, pricer),
  `pipeline/` (pricer assembly, overrides, bot-2 experiment, the production run),
  `sources/`, `sportspredict/`, `ops/`, `evaluation/`. Every path is in `config.py`.
- `research/` — the studies behind each constant; `research/rejected/` is not production.
- `data/models/`, `data/inputs/` are tracked state as of the final; `cache/ raw/ logs/` are not.
- `tests/golden/` — recordings of what the tournament-final code did; see its README.

## Rules

- **Behaviour is pinned.** `tests/test_golden.py` compares every price, request, printout and
  file write against recordings from the `tournament-final` tag. A refactor must keep it green
  without re-recording. An intentional change of behaviour is re-recorded with
  `tests/golden/make_goldens.py record --current`, and the commit explains the diff.
- **Tuned constants live only in `models/calibration.py`** and are read as `cal.NAME` at call
  time. Never `from probcup.models.calibration import NAME` inside model code: bot 2's
  experiment swaps them by attribute, and an import-time copy would silently ignore the swap.
- **Probabilities come only from the pipeline.** Research enters through
  `data/inputs/*.yaml`, scoped to one match name; nothing writes a raw probability.
- **Never touch the live platform from tests or by accident.** Tests run offline (enforced in
  `tests/conftest.py`). `probcup run --submit`, `verify`, `lineups` and `player-rates` call real
  APIs with the keys in `.env`; only run them when asked.
- **Submission order is bot 2, then bot 1**, so the stable values are always the last write.
- Data files are referenced through `probcup.config`, never by a literal path.
