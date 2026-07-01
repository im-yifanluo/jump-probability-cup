import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from card_field_component_audit import (  # noqa: E402
    build_comparisons,
    load_count_rows,
    open_card_context,
    render,
)


def _write(path: Path, text: str) -> None:
    path.write_text(textwrap.dedent(text).strip() + "\n", encoding="utf-8")


def test_open_card_context_reads_category_table_with_shared_summary_heading(tmp_path):
    _write(
        tmp_path / "manual_execution_leverage.md",
        """
        # Manual Execution Leverage Audit

        ## Summary

        | priority | markets |
        | --- | ---: |
        | HIGH | 2 |

        | coverage_state | markets |
        | --- | ---: |
        | FIELD_REQUIRED | 2 |

        | category | markets |
        | --- | ---: |
        | count_cards | 2 |
        | penalty_red | 1 |
        """,
    )
    _write(
        tmp_path / "referee_research_audit.md",
        """
        # Referee Research Audit

        ## Open Match Referee Availability

        | match | card_markets | local_override | espn_officials |
        | --- | ---: | --- | --- |
        | A vs B | 2 | no_override | - |
        | C vs D | 1 | override | Ref Name |

        ## Open Card Markets

        | match | market_id |
        | --- | --- |
        | A vs B | m1 |
        | A vs B | m2 |
        | C vs D | m3 |
        """,
    )

    context = open_card_context(tmp_path)

    assert context.open_card_like_markets == 3
    assert context.field_required_card_count_rows == 2
    assert context.card_like_non_count_rows == 1
    assert context.open_matches_with_card_markets == 2
    assert context.verified_referee_matches == 1
    assert context.local_referee_override_matches == 1


def test_card_component_render_includes_read_only_guardrails():
    rows = load_count_rows(sports_predict=False)
    comparisons, meta = build_comparisons(rows, min_train=30, trials=20, seed=3)
    text = render(
        rows,
        context=open_card_context(ROOT / "data" / "logs"),
        comparisons=comparisons,
        meta=meta,
        sports_predict=False,
        trials=20,
        seed=3,
        now=datetime(2026, 6, 29, 3, tzinfo=timezone.utc),
    )

    assert "# Card Field Component Audit" in text
    assert "`/predictions` was not read" in text
    assert "card_rows_field_minus_raw_model" in text
    assert "promoted_count_policy_minus_no_card_field_component" in text
    assert "no_field_policy: card_threshold_stat_base_else_raw" in text
    assert "field_visible_required_no_referee_override_no_new_transform" in text
