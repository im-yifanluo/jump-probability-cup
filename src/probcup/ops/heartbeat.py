"""The agent heartbeat: proof that a researched run happened.

Every `probcup run --submit --agent` touches data/logs/agent_last_run; the
watchdog reads its age to decide whether a scheduled run was missed.
"""
from __future__ import annotations

from datetime import datetime, timezone

from probcup import config

HEARTBEAT = config.HEARTBEAT


def touch() -> None:
    """Record that an agent run happened now."""
    HEARTBEAT.parent.mkdir(parents=True, exist_ok=True)
    HEARTBEAT.write_text(datetime.now(timezone.utc).isoformat())


def age_minutes() -> float | None:
    """Minutes since the last agent run, or None if there never was one."""
    if not HEARTBEAT.exists():
        return None
    return (datetime.now(timezone.utc).timestamp() - HEARTBEAT.stat().st_mtime) / 60
