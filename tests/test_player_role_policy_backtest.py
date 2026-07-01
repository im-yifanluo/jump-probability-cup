from dataclasses import replace
from datetime import datetime, timezone
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from backtest.history import HistoryMarket
from player_role_policy_backtest import (
    RoleContext,
    RolePlayerRow,
    classify_recent_role,
    policy_probability,
)


def _row(role_class="bench_or_rotation_risk"):
    market = HistoryMarket(
        version=2,
        order=1,
        match="RSA vs CAN",
        question="Will Cyle Larin score?",
        you=0.15,
        crowd=0.28,
        outcome=0,
        official_rbp=0.0,
        family="player_scores",
    )
    return RolePlayerRow(
        row=market,
        player="Cyle Larin",
        actual_status="starter",
        role=RoleContext(role_class, ("06-12:bench",), "Canada"),
    )


def test_classify_recent_role_thresholds():
    assert classify_recent_role([]) == "unknown"
    assert classify_recent_role(["starter", "starter", "starter"]) == "likely_starter"
    assert classify_recent_role(["bench", "starter"]) == "rotation_risk"
    assert classify_recent_role(["starter", "bench"]) == "bench_or_rotation_risk"


def test_role_risk_policy_uses_field_only_for_risk_classes():
    risk = _row("bench_or_rotation_risk")
    starter = replace(risk, role=RoleContext("likely_starter", ("06-12:starter",), "Canada"))

    assert policy_probability("role_risk_field_else_asym", risk) == risk.row.crowd
    assert policy_probability("benchrisk_field_else_asym", risk) == risk.row.crowd
    assert policy_probability("role_risk_field_else_asym", starter) != starter.row.crowd
