"""Probability Cup RUN WATCHDOG — alerts LOUDLY when a scheduled *agent* run was
missed. It NEVER submits anything (a low-quality mechanical submit would bleed RBP;
a skipped market is zero, a bad one is negative). Its only job is to make sure the
operator KNOWS a researched run needs triggering, so they can run it themselves.

Run frequently from the durable OS crontab (`probcup watchdog`, every 20 min). Checks
two things against the agent heartbeat (data/logs/agent_last_run, written by any
`probcup run --submit --agent`):

  1. MORNING run missed  — it's past MORNING_DEADLINE local and no agent run today.
  2. FINAL PASS missed    — a game locks within LOOKAHEAD_H and no agent run happened
                            in its pre-game window (heartbeat older than PREGAME_STALE_MIN).

On a miss it (a) writes a sticky data/logs/RUN_MISSED.txt, (b) appends to
data/logs/watchdog.log, (c) best-effort desktop notify-send. When all-clear it
clears the sticky file. Extra alert channels (email/push) plug into `_alert()`.
"""
from __future__ import annotations
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

from probcup import config
from probcup.ops import heartbeat

HEARTBEAT = heartbeat.HEARTBEAT
SNAPSHOT = config.SNAPSHOT
STICKY = config.RUN_MISSED
WLOG = config.WATCHDOG_LOG

LOOKAHEAD_H = 2.0        # start warning this far ahead of a kickoff (final pass is due ~50min before)
PREGAME_STALE_MIN = 95   # a final pass should have run within this of kickoff
MORNING_DEADLINE_H = 9   # local hour by which the morning run should have happened


def _upcoming(now: datetime):
    if not SNAPSHOT.exists():
        return []
    s = json.loads(SNAPSHOT.read_text())
    out = []
    for m in s.get("matches", []):
        try:
            ko = datetime.fromisoformat(m["opening_time"].replace("Z", "+00:00"))
        except Exception:
            continue
        hrs = (ko - now).total_seconds() / 3600
        if 0 < hrs < LOOKAHEAD_H:
            out.append((m.get("name", "?"), ko, hrs))
    return sorted(out, key=lambda z: z[1])


def _alert(lines: list[str]) -> None:
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    body = f"⚠️  PROBABILITY CUP — RUN MISSED ({stamp})\n" + "\n".join(f"  • {ln}" for ln in lines) \
        + "\n\n  ACTION: open the Claude session and trigger the run manually. NOTHING was auto-submitted.\n"
    STICKY.parent.mkdir(parents=True, exist_ok=True)
    STICKY.write_text(body)
    with WLOG.open("a") as fh:
        fh.write(body + "\n")
    # best-effort desktop notification (cron lacks DISPLAY/DBUS — inject them)
    env = dict(os.environ)
    env.setdefault("DISPLAY", ":0")
    env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path=/run/user/{os.getuid()}/bus")
    with open(os.devnull, "w") as dn:
        for cmd in (["notify-send", "-u", "critical", "⚠️ Probability Cup: RUN MISSED",
                     "; ".join(lines)],
                    ["wall", body]):
            try:
                subprocess.run(cmd, env=env, stdout=dn, stderr=dn, timeout=10)
            except Exception:
                pass
    print(body)


def _all_clear() -> None:
    if STICKY.exists():
        STICKY.unlink()


def main(argv: list[str] | None = None) -> int:
    now = datetime.now(timezone.utc)
    local = datetime.now()                       # machine local time
    age = heartbeat.age_minutes()
    misses: list[str] = []

    # 1. morning run — past the deadline and no agent run since local midnight
    if local.hour >= MORNING_DEADLINE_H:
        last_local = (datetime.fromtimestamp(HEARTBEAT.stat().st_mtime)
                      if HEARTBEAT.exists() else None)
        ran_today = last_local is not None and last_local.date() == local.date()
        if not ran_today:
            misses.append(f"MORNING run has not run today (it is {local:%H:%M} local; "
                          f"last agent run {('never' if last_local is None else last_local.strftime('%b %d %H:%M'))}).")

    # 2. final pass — a game locks soon and no agent run in its pre-game window
    for name, ko, hrs in _upcoming(now):
        mins_to_lock = hrs * 60
        if age is None or age > PREGAME_STALE_MIN:
            misses.append(f"FINAL PASS for {name} not done — locks in {mins_to_lock:.0f} min, "
                          f"last agent run {('never' if age is None else f'{age:.0f} min ago')}.")

    if misses:
        _alert(misses)
        return 1
    _all_clear()
    print(f"[watchdog] all clear — last agent run "
          f"{('n/a' if age is None else f'{age:.0f} min ago')}, "
          f"{len(_upcoming(now))} game(s) within {LOOKAHEAD_H:g}h.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
