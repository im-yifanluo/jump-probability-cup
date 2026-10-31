"""Record the golden outputs from the original code, or check code against them.

    # once, from the code that ran the final (tag tournament-final):
    git archive tournament-final | tar -x -C /tmp/old_tree
    python tests/golden/make_goldens.py record --old-tree /tmp/old_tree

    # any time: does the current package reproduce them?
    python tests/golden/make_goldens.py check            # (also run by pytest)
    python tests/golden/make_goldens.py check --old-tree /tmp/old_tree   # re-run old code

Two kinds of golden:
  boards.json      every market of 101 real boards priced for bot 1, for bot 2
                   under an experiment that moves every knob, and for bot 1 again
                   afterwards (the swap must restore everything), plus the parse
                   of ~1,200 real questions.
  cli/<name>.json  each CLI scenario's stdout, exit code, every HTTP request it
                   made, and every file it wrote, under a frozen clock and a fake
                   network (see scenarios.py).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import scenarios as sc  # noqa: E402

GOLDEN = HERE / "data"
TEXT_SUFFIXES = {".json", ".yaml", ".yml", ".csv", ".md", ".log", ".txt", ""}
MAX_INLINE = 40_000   # larger outputs (the cached snapshot, the ledger CSV) are compared by hash


# --------------------------------------------------------------------------- #
# running the harness

def _env(code: str, data_root: Path, hashseed: str) -> dict:
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": os.environ.get("HOME", ""),
           "PYTHONHASHSEED": hashseed, "TZ": "America/Chicago", "LANG": "C.UTF-8",
           "PYTHONDONTWRITEBYTECODE": "1", **sc.ENV}
    if code == "new":
        env["PROBCUP_ROOT"] = str(data_root)
    return env


def _harness(code, code_root, data_root, spec, record, mode, argv=(), extra=(),
             hashseed="0") -> subprocess.CompletedProcess:
    cmd = [sys.executable, "-s", "-B", str(HERE / "harness.py"), "--code", code,
           "--code-root", str(code_root), "--spec", str(spec), "--record", str(record),
           "--mode", mode, *extra, "--", *argv]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=1800,
                          env=_env(code, data_root, hashseed), cwd=data_root)


def _files(root: Path) -> dict[str, bytes]:
    out = {}
    for top in ("data", "docs"):
        base = root / top
        if not base.exists():
            continue
        for p in base.rglob("*"):
            if p.is_file() and "__pycache__" not in p.parts:
                out[str(p.relative_to(root))] = p.read_bytes()
    return out


class Normalizer:
    def __init__(self, layout: str, root: Path, code_root: Path):
        self.layout, self.root, self.code_root = layout, str(root), str(code_root)

    def text(self, s: str) -> str:
        s = s.replace(self.root, "<ROOT>").replace(self.code_root, "<CODE>")
        return sc.translate_text(s) if self.layout == "old" else s

    def path(self, rel: str) -> str:
        return sc.new_path(rel) if self.layout == "old" else rel

    def obj(self, o):
        if isinstance(o, str):
            return self.text(o)
        if isinstance(o, list):
            return [self.obj(x) for x in o]
        if isinstance(o, dict):
            return {k: self.obj(v) for k, v in o.items()}
        return o


def _file_changes(before: dict, after: dict, norm: Normalizer) -> dict:
    changes = {}
    for rel in sorted(set(before) | set(after)):
        if before.get(rel) == after.get(rel):
            continue
        key = norm.path(rel)
        if rel not in after:
            changes[key] = "<deleted>"
            continue
        data = after[rel]
        if Path(rel).suffix in TEXT_SUFFIXES and len(data) <= MAX_INLINE:
            try:
                changes[key] = norm.text(data.decode("utf-8"))
                continue
            except UnicodeDecodeError:
                pass
        changes[key] = "sha256:" + hashlib.sha256(data).hexdigest()
    return changes


def _stderr_tail(text: str) -> str:
    lines = [ln for ln in text.strip().splitlines() if ln.strip()]
    return lines[-1] if lines else ""


def _setup(code: str, name: str, scenario: dict, work: Path, old_tree: Path | None):
    root = work / name
    if code == "old":
        sc.copy_tree(old_tree, root)
        code_root, layout = root, "old"
    else:
        root.mkdir(parents=True)
        code_root, layout = sc.REPO, "new"
    sc.materialize(layout, root, scenario, source_root=sc.REPO)
    if "heartbeat_age_min" in scenario:
        hb = root / "data" / "logs" / "agent_last_run"
        ts = datetime.fromisoformat(sc.NOW).timestamp() - 60 * scenario["heartbeat_age_min"]
        os.utime(hb, (ts, ts))
    return root, code_root, layout


def run_scenario(code: str, name: str, work: Path, old_tree: Path | None = None,
                 hashseed: str = "0") -> dict:
    scenario = sc.SCENARIOS[name]
    root, code_root, layout = _setup(code, name, scenario, work, old_tree)
    norm = Normalizer(layout, root, code_root)
    spec = sc.web_spec(scenario.get("predictions", "pipeline"))
    steps = []
    for i, argv in enumerate(scenario["steps"]):
        spec_path, record = work / f"{name}.{i}.spec.json", work / f"{name}.{i}.record.json"
        spec_path.write_text(json.dumps(spec))
        before = _files(root)
        proc = _harness(code, code_root, root, spec_path, record, "cli", argv, hashseed=hashseed)
        if not record.exists():
            raise RuntimeError(f"harness crashed on {name} {argv}:\n{proc.stderr[-3000:]}")
        rec = json.loads(record.read_text())
        steps.append({
            "argv": argv,
            "rc": rec["rc"],
            "exception": norm.text(rec["exception"]) if rec["exception"] else None,
            "stdout": norm.text(proc.stdout),
            "stderr_tail": norm.text(_stderr_tail(proc.stderr)) if rec["rc"] else "",
            "calls": norm.obj(rec["calls"]),
            "subprocess": norm.obj(rec["subprocess"]),
            "files": _file_changes(before, _files(root), norm),
        })
        # platform state carries over to the next step
        spec["sportspredict"]["predictions"] = {
            bot: list(preds.values()) for bot, preds in rec["predictions_state"].items()}
        spec["sportspredict"]["seq_start"] = rec["seq"]
    return {"scenario": name, "steps": steps}


def _questions() -> list[str]:
    import pandas as pd
    qs = set()
    for bot in (1, 2):
        df = pd.read_csv(sc.REPO / "results" / "data" / f"settled_markets_bot{bot}.csv")
        qs |= set(df["question"].str.strip())
    qs |= {m["question"] for m in sc._snapshot()["markets"]}
    return sorted(qs)


def run_boards(code: str, work: Path, old_tree: Path | None = None, hashseed: str = "0") -> dict:
    root, code_root, layout = _setup(code, "boards", {}, work, old_tree)
    odds_rel = "data/manual_odds.yaml" if layout == "old" else sc.new_path("data/manual_odds.yaml")
    (root / odds_rel).write_text((sc.FIX / "odds.yaml").read_text())
    payload = work / "boards_payload.json"
    payload.write_text(json.dumps({"boards": json.loads((sc.FIX / "boards.json").read_text()),
                                   "questions": _questions()}))
    spec, record = work / "boards.spec.json", work / "boards.record.json"
    spec.write_text(json.dumps(sc.web_spec()))
    proc = _harness(code, code_root, root, spec, record, "boards",
                    extra=["--boards", str(payload), "--probe", str(sc.FIX / "experiment_probe.yaml")],
                    hashseed=hashseed)
    if not record.exists():
        raise RuntimeError(f"harness crashed on boards:\n{proc.stderr[-3000:]}")
    rec = json.loads(record.read_text())
    if rec["calls"]:
        raise RuntimeError(f"boards mode made network calls: {rec['calls'][:3]}")
    return rec["result"]


# --------------------------------------------------------------------------- #
# comparing

def diff(golden, actual, path="", out=None, limit=40, tol=0.0):
    """Human-readable differences between two JSON-like objects. Floats stored
    as repr strings are compared numerically when `tol` > 0."""
    out = [] if out is None else out
    if len(out) >= limit:
        return out
    if isinstance(golden, dict) and isinstance(actual, dict):
        for k in sorted(set(golden) | set(actual), key=str):
            if k not in actual:
                out.append(f"{path}/{k}: missing in actual")
            elif k not in golden:
                out.append(f"{path}/{k}: unexpected in actual")
            else:
                diff(golden[k], actual[k], f"{path}/{k}", out, limit, tol)
    elif isinstance(golden, list) and isinstance(actual, list):
        if len(golden) != len(actual):
            out.append(f"{path}: length {len(golden)} != {len(actual)}")
        for i, (g, a) in enumerate(zip(golden, actual)):
            diff(g, a, f"{path}[{i}]", out, limit, tol)
    elif golden != actual:
        if tol and isinstance(golden, str) and isinstance(actual, str):
            try:
                if abs(float(golden) - float(actual)) <= tol:
                    return out
            except ValueError:
                pass
        g, a = repr(golden), repr(actual)
        if len(g) > 300 or len(a) > 300:
            import difflib
            lines = list(difflib.unified_diff(str(golden).splitlines(), str(actual).splitlines(),
                                              "golden", "actual", lineterm="", n=1))
            out.append(f"{path}:\n    " + "\n    ".join(lines[:40]))
        else:
            out.append(f"{path}: golden {g} != actual {a}")
    return out


def state_hashes(layout: str, root: Path) -> dict:
    return {rel: hashlib.sha256((root / (rel if layout == "old" else sc.new_path(rel))).read_bytes()).hexdigest()
            for rel in sc.STATE_FILES}


def _versions() -> dict:
    import numpy, pandas, scipy, statsmodels  # noqa: E401
    return {"python": sys.version.split()[0], "numpy": numpy.__version__,
            "pandas": pandas.__version__, "scipy": scipy.__version__,
            "statsmodels": statsmodels.__version__}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["record", "check"])
    ap.add_argument("--old-tree", type=Path, help="export of the tournament-final tag")
    ap.add_argument("--only", nargs="*", help="scenario names (default: all, plus boards)")
    ap.add_argument("--hashseed", default="0")
    ap.add_argument("--tol", type=float, default=0.0, help="float tolerance (default exact)")
    args = ap.parse_args()

    names = args.only if args.only else ["boards", *sc.SCENARIOS]
    code = "old" if args.old_tree else "new"
    if args.action == "record" and code != "old":
        ap.error("record needs --old-tree (goldens come from the original code)")

    failures = 0
    with tempfile.TemporaryDirectory(prefix="golden-") as tmp:
        work = Path(tmp)
        for name in names:
            result = (run_boards(code, work, args.old_tree, args.hashseed) if name == "boards"
                      else run_scenario(code, name, work, args.old_tree, args.hashseed))
            target = GOLDEN / ("boards.json" if name == "boards" else f"cli/{name}.json")
            if args.action == "record":
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(json.dumps(result, indent=1, sort_keys=True, ensure_ascii=False))
                print(f"recorded {target.relative_to(sc.REPO)}")
                continue
            problems = diff(json.loads(target.read_text()), result, tol=args.tol)
            failures += bool(problems)
            print(f"{'OK  ' if not problems else 'DIFF'} {name}")
            for p in problems:
                print("   ", p)
    if args.action == "record":
        meta = {"generated_from": "tag tournament-final",
                "state_file_sha256": state_hashes("old", args.old_tree),
                "versions": _versions()}
        (GOLDEN / "meta.json").write_text(json.dumps(meta, indent=1, sort_keys=True))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
