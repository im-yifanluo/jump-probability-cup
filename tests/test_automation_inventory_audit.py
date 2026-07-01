import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from automation_inventory_audit import build_inventory, render  # noqa: E402


PROMPT = (
    "Read docs/manual_strategy.md, docs/strategies.md, docs/full_research_program.md. "
    "Do not submit. /predictions is not part of the manual workflow. "
    "Run full_manual_pricing.py, brier_decomposition_audit.py, current_player_stats_audit.py, current_player_stats_replay.py, "
    "current_tournament_stats_audit.py, lineup_source_audit.py, special_count_source_discovery.py, match_lock_readiness_audit.py, "
    "manual_submission_packet_audit.py, "
    "manual_workflow_readiness_audit.py, and write manual_predictions.md. "
    "Report player_prop_anchor_status and current_prop_anchor_status. "
    "Field values are unavailable before prediction lock; never wait for or fabricate them."
)
RRULE = "DTSTART;TZID=America/Chicago:20260629T080000\\nRRULE:FREQ=DAILY;BYHOUR=8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23;BYMINUTE=0,30;BYSECOND=0"


def _write_automation(
    root: Path,
    automation_id: str,
    *,
    name: str,
    kind: str = "heartbeat",
    status: str = "ACTIVE",
    prompt: str = PROMPT,
    rrule: str = RRULE,
    target_thread: bool = True,
) -> None:
    path = root / automation_id / "automation.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    target = 'target_thread_id = "thread"' if target_thread else ""
    path.write_text(
        textwrap.dedent(
            f'''
            version = 1
            id = "{automation_id}"
            kind = "{kind}"
            name = "{name}"
            status = "{status}"
            rrule = "{rrule}"
            prompt = "{prompt}"
            {target}
            '''
        ).strip()
        + "\n",
        encoding="utf-8",
    )


def test_inventory_accepts_primary_heartbeat_and_paused_legacy(tmp_path):
    _write_automation(
        tmp_path,
        "probability-cup-nightly-manual-predictions-in-chat",
        name="Probability Cup manual predictions in main chat",
    )
    _write_automation(
        tmp_path,
        "probability-cup-morning-adjustment-check",
        name="Probability Cup morning adjustment check",
        kind="cron",
        status="PAUSED",
        prompt="stale Probability Cup prompt",
    )

    decision, rows, meta = build_inventory(tmp_path)
    text = render(decision, rows, meta)

    assert decision == "PRIMARY_HEARTBEAT_READY"
    assert meta["active_stale_or_duplicate_count"] == "0"
    assert meta["paused_legacy_count"] == "1"
    assert any(row.automation_id == "probability-cup-nightly-manual-predictions-in-chat" and row.risk == "OK" for row in rows)
    assert any(row.automation_id == "probability-cup-morning-adjustment-check" and row.risk == "PAUSED_OK" for row in rows)
    assert "PRIMARY_HEARTBEAT_READY" in text


def test_inventory_flags_active_legacy_duplicate(tmp_path):
    _write_automation(
        tmp_path,
        "probability-cup-nightly-manual-predictions-in-chat",
        name="Probability Cup manual predictions in main chat",
    )
    _write_automation(
        tmp_path,
        "probability-cup-pre-match-change-checker",
        name="Probability Cup pre-match change checker",
        kind="cron",
        status="ACTIVE",
        prompt="stale Probability Cup prompt",
    )

    decision, rows, meta = build_inventory(tmp_path)

    assert decision == "ACTION_REQUIRED"
    assert meta["active_stale_or_duplicate_count"] == "1"
    assert any(row.automation_id == "probability-cup-pre-match-change-checker" and row.risk == "STALE_DUPLICATE_ACTIVE" for row in rows)


def test_inventory_flags_primary_missing_terms_or_schedule(tmp_path):
    _write_automation(
        tmp_path,
        "probability-cup-nightly-manual-predictions-in-chat",
        name="Probability Cup manual predictions in main chat",
        prompt="Read docs/manual_strategy.md only.",
        rrule="DTSTART;TZID=America/Chicago:20260629T090000\\nRRULE:FREQ=DAILY;BYHOUR=9;BYMINUTE=0;BYSECOND=0",
    )

    decision, rows, meta = build_inventory(tmp_path)

    assert decision == "ACTION_REQUIRED"
    assert meta["primary_prompt_status"] == "CHECK"
    assert meta["primary_schedule_status"] == "CHECK"
    assert any(row.automation_id == "probability-cup-nightly-manual-predictions-in-chat" and row.risk == "PRIMARY_NOT_READY" for row in rows)


def test_inventory_flags_primary_with_stale_field_prompt(tmp_path):
    _write_automation(
        tmp_path,
        "probability-cup-nightly-manual-predictions-in-chat",
        name="Probability Cup manual predictions in main chat",
        prompt=PROMPT + " Apply the manual field policy only if field is visible/captured.",
    )

    decision, rows, meta = build_inventory(tmp_path)

    assert decision == "ACTION_REQUIRED"
    assert meta["primary_prompt_status"] == "CHECK"
    assert any(row.automation_id == "probability-cup-nightly-manual-predictions-in-chat" and row.risk == "PRIMARY_NOT_READY" for row in rows)
