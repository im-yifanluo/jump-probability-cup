# Golden tests: "does it still behave exactly as it did in the final?"

The restructure moved every file in the repository, so before moving anything the code that
ran the World Cup final (git tag `tournament-final`) was run against a fake outside world and
everything it did was recorded. The current code must reproduce those recordings exactly.

**What's recorded** ([`data/`](data/)):

- `boards.json` — all 101 real boards (1,153 markets) priced three times: bot 1, bot 2 under an
  experiment that moves every swappable constant, and bot 1 again afterwards (the experiment
  must restore everything); plus the parse of every real question template (748).
- `cli/<scenario>.json` — 13 command scenarios: production runs (submit, dry run, nothing to
  do, a repeat run that must change nothing), single-match pricing, lineup pulls, scheduling,
  the watchdog (clear and alarm), backlog scan, submission verification, player-rate updates,
  and the evaluation tools. Each step records stdout, the exit code, every HTTP request
  (method, URL, parameters, body) and every file written.

**How** ([`harness.py`](harness.py)): each command runs in its own process with the clock frozen at
2026-06-27 22:40 UTC, `requests` patched to a stateful fake of SportsPredict, ESPN, Polymarket
and API-Football ([`fakeweb.py`](fakeweb.py), [`scenarios.py`](scenarios.py)), sleeping and
desktop notifications disabled, and the hash seed fixed. Recording the old code twice with
different hash seeds gives identical results.

The only differences allowed between old and new are the documented renames in
`scenarios.PATH_MAP` / `TEXT_RENAMES`: data files moved to `data/models|inputs|cache/`, and four
printed hints now name `probcup` commands instead of deleted scripts.

```
pytest tests/test_golden.py                       # what CI runs
python tests/golden/make_goldens.py check         # same, with a readable diff
git archive tournament-final | tar -x -C /tmp/old && \
  python tests/golden/make_goldens.py record --old-tree /tmp/old    # re-record from the tag
```

If you change behaviour on purpose, re-record with `record --current` and explain the
golden diff in the commit. Stored prices are compared exactly; on another CPU, set
`GOLDEN_TOL=1e-9` to absorb last-digit floating-point noise (CI does).

The same before/after check was run once, outside the test suite, for the ten research
scripts (byte-identical output) and the full club backtest (identical report, coefficients
and 782,157-row results).
