import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from player_prop_boundary_audit import (  # noqa: E402
    build_historical_comparisons,
    current_player_context,
    load_player_rows,
    render,
)


def _write(path: Path, text: str) -> None:
    path.write_text(textwrap.dedent(text).strip() + "\n", encoding="utf-8")


def test_current_player_context_counts_flags_and_threshold_support(tmp_path):
    _write(
        tmp_path / "player_prop_diagnostics.md",
        """
        # Player Prop Diagnostics

        ## Current Open Player Props

        | priority | action | family_skill/row | match | kickoff_local | market_id | raw_model | current_anchor | model | manual | manual_policy | player | family | source | minutes | resolved | team_hint | side | lineup | flags | question |
        | --- | --- | ---: | --- | --- | --- | ---: | ---: | ---: | ---: | --- | --- | --- | --- | ---: | --- | --- | --- | --- | --- | --- |
        | HIGH | find exact prop odds | -1.65 | A vs B | 2026-06-29 12:00 CDT | m1 | 17% |  | 17% | 17% | player_no_field_raw_model_unvalidated_threshold_and_source | Player One | player_sot_ge | prior | 90 |  | A | home | not_posted | PRIOR_ONLY,LINEUP_NOT_POSTED | Will Player One have 2 or more shots on target in regulation (90 minutes + stoppage time)? |
        | MEDIUM | wait for lineup | 0.86 | A vs B | 2026-06-29 12:00 CDT | m2 | 35% |  | 35% | 28% | player_no_field_raw_scores_else_global_base | Player Two | player_sot_ge | override | 90 | Player Two | A | home | not_posted | GLOBAL_MINUTES_UNCONFIRMED,SCORER_PROP_RECHECK,LINEUP_NOT_POSTED | Will Player Two have at least 1 shot on target in regulation (90 minutes + stoppage time)? |
        | MEDIUM | recheck anchor | 0.86 | A vs B | 2026-06-29 12:00 CDT | m3 | 42% | 30% | 30% | 30% | player_no_field_researched_model | Player Three | player_scores | override | 90 |  | A | home | not_posted | CURRENT_PROP_MARKET_ANCHOR,LINEUP_NOT_POSTED | Will Player Three score a goal (excluding own goals) in regulation (90 minutes + stoppage time)? |
        """,
    )

    context, policy_counts = current_player_context(tmp_path)

    assert context.current_player_props == 3
    assert context.high_priority_rows == 1
    assert context.medium_priority_rows == 2
    assert context.current_prop_anchor_rows == 1
    assert context.source_unvalidated_rows == 1
    assert context.global_minutes_unconfirmed_rows == 1
    assert context.scorer_prop_recheck_rows == 1
    assert context.lineup_not_posted_rows == 3
    assert context.high_threshold_sot_rows == 1
    assert context.no_field_base_supported_rows == 2
    assert context.no_field_base_applied_rows == 1
    assert context.raw_unvalidated_threshold_rows == 1
    assert policy_counts["player_no_field_researched_model"] == 1


def test_player_boundary_render_includes_read_only_guardrails():
    rows = load_player_rows(sports_predict=False)
    comparisons, meta, bucket_rows = build_historical_comparisons(rows, min_train=20, trials=20, seed=5)
    context, manual_policy_counts = current_player_context(ROOT / "data" / "logs")
    text = render(
        rows,
        comparisons=comparisons,
        meta=meta,
        bucket_rows=bucket_rows,
        context=context,
        manual_policy_counts=manual_policy_counts,
        sports_predict=False,
        trials=20,
        seed=5,
        now=datetime(2026, 6, 29, 3, tzinfo=timezone.utc),
    )

    assert "# Player Prop Boundary Audit" in text
    assert "`/predictions` was not read" in text
    assert "field_minus_raw_when_model_above_field" in text
    assert "raw_scores_else_global_base" in text
    assert "Current high-threshold player SOT rows have no settled no-field holdout support" in text
    assert "decision:" in text
