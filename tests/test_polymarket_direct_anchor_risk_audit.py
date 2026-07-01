import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from polymarket_direct_anchor_risk_audit import (  # noqa: E402
    build_risk_rows,
    read_current_anchors,
    read_status_evidence,
    render,
)


def _write(path: Path, text: str) -> None:
    path.write_text(text.strip() + "\n", encoding="utf-8")


def test_anchor_risk_audit_flags_large_player_and_corner_shifts(tmp_path):
    coverage = tmp_path / "coverage.md"
    score = tmp_path / "score.md"
    _write(
        coverage,
        """
# Polymarket Direct Coverage Audit

## Matched Markets

| status | match | kickoff_utc | model | anchor | delta_pp | volume | question | polymarket_question |
| --- | --- | --- | ---: | ---: | ---: | ---: | --- | --- |
| promoted_count_corner | BRA vs JPN | 2026-06-29T17:00+00:00 | 50% | 57% | +7 | $11,330 | Will there be 9 or more total corner kicks? | Brazil vs. Japan: O/U 8.5 Total Corners |
| promoted_player_prop | BRA vs JPN | 2026-06-29T17:00+00:00 | 41% | 30% | -11 | $3,907 | Will Matheus Cunha score a goal? | Matheus Cunha: 1+ goals |
| promoted_non_player | GER vs PAR | 2026-06-29T20:30+00:00 | 56% | 56% | +0 | $44,536 | Will the match have 3 or more total goals? | Germany vs. Paraguay: O/U 2.5 |
""",
    )
    _write(
        score,
        """
# Polymarket Direct Snapshot Score

- scored_latest_markets: 4
- unscored_latest_markets: 16
- historical_promotion_min_latest_markets: 30
- historical_promotion_decision: not_promoted_insufficient_sample

## Latest Market Coverage By Anchor Type

| status | latest_markets | scored | unscored | scored_delta | anchor_wins |
| --- | ---: | ---: | ---: | ---: | ---: |
| promoted_count_corner | 2 | 1 | 1 | -0.0941 | 0 |
| promoted_non_player | 16 | 2 | 14 | -0.0043 | 1 |
| promoted_player_prop | 2 | 1 | 1 | -0.0129 | 0 |
""",
    )

    anchors = read_current_anchors(coverage)
    status_evidence, keys = read_status_evidence(score)
    rows = build_risk_rows(anchors, status_evidence, keys, large_shift_pp=5)

    high = [row for row in rows if row.risk_level == "HIGH"]
    assert len(high) == 2
    assert all("LARGE_SHIFT_RECHECK" in row.tags for row in high)
    assert any("PLAYER_PROP_LINEUP_RECHECK" in row.tags for row in high)
    assert any("CORNER_COUNT_RECHECK" in row.tags for row in high)
    assert all("LOW_REPLAY_SAMPLE" in row.tags for row in rows)


def test_anchor_risk_render_is_read_only_and_reconciles_counts(tmp_path):
    coverage = tmp_path / "coverage.md"
    score = tmp_path / "score.md"
    _write(
        coverage,
        """
# Polymarket Direct Coverage Audit

## Matched Markets

| status | match | kickoff_utc | model | anchor | delta_pp | volume | question | polymarket_question |
| --- | --- | --- | ---: | ---: | ---: | ---: | --- | --- |
| promoted_player_prop | BRA vs JPN | 2026-06-29T17:00+00:00 | 41% | 30% | -11 | $3,907 | Will Matheus Cunha score a goal? | Matheus Cunha: 1+ goals |
""",
    )
    _write(
        score,
        """
# Polymarket Direct Snapshot Score

- scored_latest_markets: 4
- unscored_latest_markets: 16
- historical_promotion_min_latest_markets: 30
- historical_promotion_decision: not_promoted_insufficient_sample

## Latest Market Coverage By Anchor Type

| status | latest_markets | scored | unscored | scored_delta | anchor_wins |
| --- | ---: | ---: | ---: | ---: | ---: |
| promoted_player_prop | 2 | 1 | 1 | -0.0129 | 0 |
""",
    )
    anchors = read_current_anchors(coverage)
    status_evidence, keys = read_status_evidence(score)
    rows = build_risk_rows(anchors, status_evidence, keys, large_shift_pp=5)

    text = render(
        rows,
        status_evidence,
        keys,
        large_shift_pp=5,
        now=datetime(2026, 6, 29, 1, tzinfo=timezone.utc),
    )

    assert "Read-only analysis. `/predictions` was not read and no submissions were made." in text
    assert "decision: guardrail_only_no_probability_transform" in text
    assert "current_anchor_rows: 1" in text
    assert "large_shift_rows: 1" in text
    assert "high_risk_rows: 1" in text
    assert "not_promoted_insufficient_sample" in text
    assert "Matheus Cunha: 1+ goals" in text
