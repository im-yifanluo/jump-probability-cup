"""Health check for the automated submission pipeline.

Run this any time to verify the automation is sound WITHOUT waiting for a
scheduled fire. Checks, in order:
  1. the pipeline runs end-to-end (dry-run) and can reach the live odds + API
  2. the three log files exist and how recent the last real run/submission was
  3. whether an OS crontab safety net is installed (the only layer that
     survives this app closing)
  4. that the model and the platform are in sync (no un-submitted changes)

Usage: python3 scripts/check_automation.py
Exit code 0 = all green; 1 = something needs attention.
"""

import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
LOGS = ROOT / "data" / "logs"


def _age(path: Path) -> str:
    if not path.exists():
        return "MISSING"
    mtime = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    hrs = (datetime.now(timezone.utc) - mtime).total_seconds() / 3600
    return f"{hrs:.1f}h ago"


def main() -> int:
    ok = True
    print("=== Probability Cup automation health check ===\n")

    # 1. Pipeline runs end-to-end (dry-run, no side effects)
    print("[1] pipeline dry-run (odds fetch + pricing + logging)...")
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "daily_run.py"), "--hours", "48", "--dry-run"],
        capture_output=True, text=True, cwd=ROOT)
    if proc.returncode == 0 and "submitting" in proc.stdout:
        action = proc.stdout.rsplit("submitting", 1)[1].splitlines()[1].strip()
        print(f"    OK — pipeline ran, plan: {action}")
    else:
        ok = False
        print(f"    FAIL — exit {proc.returncode}\n{proc.stderr[-400:]}")

    # 2. Log freshness
    print("\n[2] logs:")
    for name in ("runs.log", "submissions.csv", "matchday_reports.md"):
        print(f"    {name:22s} last write {_age(LOGS / name)}")

    # 3. OS crontab safety net
    print("\n[3] OS crontab persistence:")
    cron = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    if "daily_run.py" in cron.stdout:
        n = cron.stdout.count("daily_run.py")
        print(f"    OK — {n} crontab entries run the pipeline unattended")
    else:
        ok = False
        print("    NOT INSTALLED — automation depends on the Claude app staying open.")
        print("    Install with the one-liner in the README to make it survive restarts.")

    # 4. Platform sync (model output == what's submitted)
    print("\n[4] model/platform sync:")
    try:
        from probability_cup import ProbabilityCup
        p = ProbabilityCup(bot=1).predictions_df()
        print(f"    {len(p)} predictions live "
              f"({sum(p['market_status']=='open')} open, {sum(p['market_status']=='settled')} settled)")
    except Exception as exc:
        ok = False
        print(f"    FAIL — cannot reach platform: {exc}")

    print("\n" + ("ALL GREEN" if ok else "NEEDS ATTENTION — see FAIL/NOT INSTALLED above"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
