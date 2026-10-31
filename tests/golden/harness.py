"""Run one piece of the system in a sealed process: frozen clock, fake network,
no sleeping, no desktop notifications. Used to record goldens from the
original code (the `tournament-final` tag) and to check the current code
against them.

    python harness.py --code {old,new} --code-root DIR --spec SPEC.json
                      --record OUT.json --mode cli -- run --hours 48 --dry-run
    python harness.py ... --mode boards --boards BOARDS.json --probe PROBE.yaml

`old` runs the original scripts (scripts/*.py on a flat src/); `new` runs the
`probcup` package. Data paths come from the code itself: the original code
reads data/ next to its own scripts, the package reads PROBCUP_ROOT.
"""
from __future__ import annotations

import argparse
import contextlib
import dataclasses
import io
import json
import runpy
import subprocess
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# Import the heavy third-party stack before freezing the clock, so libraries
# keep the real datetime class; only the code under test sees the frozen one.
import numpy  # noqa: E402,F401
import pandas as pd  # noqa: E402
import requests  # noqa: E402,F401
import scipy.optimize  # noqa: E402,F401
import scipy.stats  # noqa: E402,F401
import statsmodels.api  # noqa: E402,F401
import yaml  # noqa: E402

import fakeweb  # noqa: E402

OLD_SCRIPTS = {
    "run": "daily_run.py", "price": "price_match.py", "lineups": "pull_lineups.py",
    "player-rates": "update_player_rates.py", "verify": "verify_submissions.py",
    "backlog": "scan_backlog.py", "schedule": "schedule_day.py",
    "watchdog": "run_watchdog.py", "ledger": "parse_history_rbp.py",
    "replay": "backtest_engine.py", "drift": "family_calibration.py",
}


def freeze_clock(now_iso: str) -> None:
    import datetime as dt
    real = dt.datetime
    frozen = real.fromisoformat(now_iso)

    class FrozenDateTime(real):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return cls.fromtimestamp(frozen.timestamp())
            return cls.fromtimestamp(frozen.timestamp(), tz)

        @classmethod
        def utcnow(cls):
            return cls.fromtimestamp(frozen.timestamp(), dt.timezone.utc).replace(tzinfo=None)

    dt.datetime = FrozenDateTime
    stamp = pd.Timestamp(frozen)

    def _ts_now(cls=None, tz=None):
        return stamp.tz_convert(tz) if tz is not None else stamp.tz_convert(None)

    pd.Timestamp.now = classmethod(lambda cls, tz=None: _ts_now(cls, tz))


class _Recorder:
    def __init__(self):
        self.subprocess_calls: list[list[str]] = []

    def run(self, cmd, *args, **kwargs):
        self.subprocess_calls.append([str(c) for c in cmd])
        return subprocess.CompletedProcess(cmd, 0, b"", b"")


def _exit_code(exc: SystemExit) -> int:
    if exc.code is None:
        return 0
    if isinstance(exc.code, int):
        return exc.code
    print(exc.code, file=sys.stderr)
    return 1


# --------------------------------------------------------------------------- #
# code under test

class OldCode:
    def __init__(self, root: Path):
        self.root = root
        sys.path[:0] = [str(root / "src"), str(root / "scripts")]

    def run_cli(self, argv: list[str]) -> int:
        script = self.root / "scripts" / OLD_SCRIPTS[argv[0]]
        sys.argv = [str(script), *argv[1:]]
        runpy.run_path(str(script), run_name="__main__")
        return 0

    def pricing(self):
        import daily_run
        import price_match
        from questions.parser import parse
        from questions.teams import ALL_TEAM_NAMES
        return parse, ALL_TEAM_NAMES, price_match.build_pricer, daily_run._experimental


class NewCode:
    def __init__(self, root: Path):
        sys.path.insert(0, str(root / "src"))

    def run_cli(self, argv: list[str]) -> int:
        from probcup.cli import main
        rc = main(argv)
        return 0 if rc is None else int(rc)

    def pricing(self):
        from probcup.pipeline.build import build_pricer
        from probcup.pipeline.experiment import experimental
        from probcup.questions.parser import parse
        from probcup.questions.teams import ALL_TEAM_NAMES
        return parse, ALL_TEAM_NAMES, build_pricer, experimental


# --------------------------------------------------------------------------- #
# modes

def _price(pricer, spec) -> str:
    try:
        return repr(float(pricer.price(spec)))
    except Exception as exc:  # recorded, not raised: failures are behaviour too
        return f"error {type(exc).__name__}: {exc}"


def _spec_dict(spec) -> dict:
    return json.loads(json.dumps(dataclasses.asdict(spec), sort_keys=True, default=repr))


def price_boards(code, boards: list[dict], questions: list[str], probe: dict) -> dict:
    parse, teams, build_pricer, experimental = code.pricing()
    out = {"boards": [], "parse": {}}
    for b in boards:
        kickoff = pd.Timestamp(b["kickoff"] or "2026-07-01T00:00:00+00:00")
        buf = io.StringIO()
        entry = {"match_name": b["match_name"], "bot1": {}, "bot2": {}, "bot1_after": {}}
        with contextlib.redirect_stdout(buf):
            try:
                pricer = build_pricer(b["match_name"], kickoff)
                entry["error"] = None
            except Exception as exc:
                pricer, entry["error"] = None, f"{type(exc).__name__}: {exc}"
        entry["build_stdout"] = buf.getvalue()
        if pricer is not None:
            specs = {}
            for m in b["markets"]:
                try:
                    specs[m["market_id"]] = parse(m["question"], teams=teams)
                except Exception as exc:
                    entry["bot1"][m["market_id"]] = f"parse-error {type(exc).__name__}"
            for mid, spec in specs.items():
                entry["bot1"][mid] = _price(pricer, spec)
            with experimental(probe):
                for mid, spec in specs.items():
                    entry["bot2"][mid] = _price(pricer, spec)
            for mid, spec in specs.items():
                entry["bot1_after"][mid] = _price(pricer, spec)
        out["boards"].append(entry)
    for q in questions:
        try:
            out["parse"][q] = _spec_dict(parse(q, teams=teams))
        except Exception as exc:
            out["parse"][q] = {"error": type(exc).__name__, "message": str(exc)}
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", choices=["old", "new"], required=True)
    ap.add_argument("--code-root", type=Path, required=True)
    ap.add_argument("--spec", type=Path, required=True)
    ap.add_argument("--record", type=Path, required=True)
    ap.add_argument("--mode", choices=["cli", "boards"], default="cli")
    ap.add_argument("--boards", type=Path)
    ap.add_argument("--probe", type=Path)
    ap.add_argument("argv", nargs=argparse.REMAINDER)
    args = ap.parse_args()
    argv = args.argv[1:] if args.argv[:1] == ["--"] else args.argv

    spec = json.loads(args.spec.read_text())
    freeze_clock(spec["now"])
    time.sleep = lambda *_: None
    recorder = _Recorder()
    subprocess.run = recorder.run
    web = fakeweb.FakeWeb(spec)
    fakeweb.install(web)

    code = (OldCode if args.code == "old" else NewCode)(args.code_root)
    record: dict = {"rc": 0, "exception": None}
    if args.mode == "boards":
        payload = json.loads(args.boards.read_text())
        probe = yaml.safe_load(args.probe.read_text())
        record["result"] = price_boards(code, payload["boards"], payload["questions"], probe)
    else:
        try:
            record["rc"] = code.run_cli(argv)
        except SystemExit as exc:
            record["rc"] = _exit_code(exc)
        except BaseException as exc:  # noqa: BLE001 - a crash is an outcome to compare
            traceback.print_exc()
            record["rc"] = 1
            record["exception"] = f"{type(exc).__name__}: {exc}"
    sys.stdout.flush()
    record["calls"] = web.calls
    record["subprocess"] = recorder.subprocess_calls
    record["predictions_state"] = web.predictions
    record["seq"] = web._seq
    args.record.write_text(json.dumps(record, indent=1, sort_keys=True, default=repr))
    return 0


if __name__ == "__main__":
    sys.exit(main())
