"""Run the manual research refresh plan with read-only safety checks.

This is an orchestrator over the existing manual-entry evidence scripts. It
does not introduce a strategy, does not submit predictions, and does not read
stored bot predictions. Its purpose is to make the full research/pipeline
refresh reproducible and auditable from one command.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "data" / "logs"
OUT = LOG_DIR / "manual_research_refresh.md"

UV_BASE = [
    "uv",
    "run",
    "--with",
    "pandas",
    "--with",
    "numpy",
    "--with",
    "scipy",
    "--with",
    "pyarrow",
    "--with",
    "pyyaml",
    "--with",
    "requests",
    "--with",
    "statsmodels",
]
UV_REQUESTS = ["uv", "run", "--with", "requests"]
UV_NUMPY = ["uv", "run", "--with", "numpy"]
UV_NUMPY_SCIPY = ["uv", "run", "--with", "numpy", "--with", "scipy"]
UV_PANDAS_NUMPY = ["uv", "run", "--with", "pandas", "--with", "numpy", "--with", "scipy", "--with", "statsmodels", "--with", "pyarrow"]
UV_REQUESTS_NUMPY_SCIPY = ["uv", "run", "--with", "requests", "--with", "numpy", "--with", "scipy"]


@dataclass(frozen=True)
class CommandSpec:
    phase: str
    name: str
    argv: tuple[str, ...]
    live_api: bool = False

    def command_text(self) -> str:
        return " ".join(self.argv)


@dataclass(frozen=True)
class CommandResult:
    spec: CommandSpec
    status: str
    elapsed_s: float
    returncode: int | None
    output_tail: str


def _py(script: str, *args: str) -> tuple[str, ...]:
    return (sys.executable, script, *args)


def _uv(base: list[str], script: str, *args: str) -> tuple[str, ...]:
    return tuple(base + ["python", script, *args])


def _maybe_sports_predict(enabled: bool) -> tuple[str, ...]:
    return ("--sports-predict",) if enabled else ()


def build_plan(
    *,
    profile: str,
    local_date: str,
    espn_date: str,
    start_date: str,
    sports_predict: bool,
    include_polymarket_snapshot: bool,
) -> list[CommandSpec]:
    """Build a deterministic command plan for a refresh profile."""

    sp = _maybe_sports_predict(sports_predict)

    live: list[CommandSpec] = [
        CommandSpec("live", "manual_odds_anchor", _py("scripts/manual_odds_anchor.py", "--date", espn_date, *sp), live_api=sports_predict),
        CommandSpec("live", "live_market_risk_audit", _uv(UV_BASE, "scripts/live_market_risk_audit.py", "--all-open", "--write", "--field-template"), live_api=True),
        CommandSpec("live", "manual_field_capture_audit", _uv(UV_BASE, "scripts/manual_field_capture_audit.py", "--write")),
        CommandSpec(
            "live",
            "player_prop_diagnostics",
            _uv(
                UV_BASE,
                "scripts/player_prop_diagnostics.py",
                *sp,
                "--all-open",
                "--write",
                "--anchor-template",
                "--player-prop-csv",
                "data/player_prop_anchors_template.csv",
            ),
            live_api=sports_predict,
        ),
        CommandSpec(
            "live",
            "current_player_stats_audit",
            _uv(UV_REQUESTS, "scripts/current_player_stats_audit.py", "--start-date", start_date, "--end-date", local_date, *sp, "--write"),
            live_api=sports_predict,
        ),
        CommandSpec(
            "live",
            "current_player_stats_replay",
            _uv(UV_REQUESTS, "scripts/current_player_stats_replay.py", "--start-date", start_date, "--end-date", local_date, *sp, "--write"),
            live_api=sports_predict,
        ),
        CommandSpec("live", "lineup_source_audit", _uv(UV_BASE, "scripts/lineup_source_audit.py", *sp, "--all-open", "--write"), live_api=sports_predict),
        CommandSpec("live", "special_market_live_audit", _uv(UV_BASE, "scripts/special_market_live_audit.py", "--all-open", "--write", "--count-source-template"), live_api=True),
        CommandSpec(
            "live",
            "current_tournament_stats_audit",
            _uv(UV_REQUESTS, "scripts/current_tournament_stats_audit.py", "--start-date", start_date, "--end-date", local_date, *sp, "--write", "--update-special-count-csv"),
            live_api=sports_predict,
        ),
        CommandSpec("live", "special_count_source_discovery", _uv(UV_REQUESTS, "scripts/special_count_source_discovery.py", "--write"), live_api=True),
        CommandSpec("live", "special_count_source_audit", _py("scripts/special_count_source_audit.py", "--write")),
        CommandSpec("live", "full_manual_pricing", _uv(UV_BASE, "scripts/full_manual_pricing.py", "--local-date", local_date), live_api=True),
    ]

    evidence: list[CommandSpec] = [
        CommandSpec("evidence", "backtest_integrity_audit", _uv(UV_REQUESTS, "scripts/backtest_integrity_audit.py", *sp, "--write"), live_api=sports_predict),
        CommandSpec("evidence", "manual_policy_backtest", _uv(UV_REQUESTS, "scripts/manual_policy_backtest.py", *sp, "--write"), live_api=sports_predict),
        CommandSpec("evidence", "manual_family_blend_backtest", _uv(UV_REQUESTS, "scripts/manual_family_blend_backtest.py", *sp, "--write"), live_api=sports_predict),
        CommandSpec("evidence", "manual_calibration_backtest", _uv(UV_REQUESTS, "scripts/manual_calibration_backtest.py", *sp, "--write"), live_api=sports_predict),
        CommandSpec("evidence", "goal_policy_backtest", _uv(UV_REQUESTS, "scripts/goal_policy_backtest.py", *sp, "--write"), live_api=sports_predict),
        CommandSpec("evidence", "count_policy_backtest", _uv(UV_REQUESTS, "scripts/count_policy_backtest.py", *sp, "--write"), live_api=sports_predict),
        CommandSpec("evidence", "count_no_field_policy_backtest", _uv(UV_REQUESTS, "scripts/count_no_field_policy_backtest.py", *sp, "--write"), live_api=sports_predict),
        CommandSpec("evidence", "player_policy_backtest", _uv(["uv", "run", "--with", "pandas", "--with", "pyyaml", "--with", "requests"], "scripts/player_policy_backtest.py", *sp, "--write"), live_api=sports_predict),
        CommandSpec("evidence", "player_no_field_policy_backtest", _uv(UV_BASE, "scripts/player_no_field_policy_backtest.py", *sp, "--write"), live_api=sports_predict),
        CommandSpec("evidence", "penalty_red_policy_backtest", _uv(UV_REQUESTS, "scripts/penalty_red_policy_backtest.py", *sp, "--write"), live_api=sports_predict),
        CommandSpec("evidence", "odds_conversion_backtest", _uv(UV_NUMPY, "scripts/odds_conversion_backtest.py", "--write")),
        CommandSpec("evidence", "elo_history_residual_audit", _py("scripts/elo_history_residual_audit.py", "--write")),
        CommandSpec("evidence", "count_intensity_replay", _uv(UV_BASE, "scripts/count_intensity_replay.py", *sp, "--write"), live_api=sports_predict),
        CommandSpec("evidence", "count_intensity_policy_replay", _uv(UV_BASE, "scripts/count_intensity_policy_replay.py", *sp, "--write"), live_api=sports_predict),
        CommandSpec("evidence", "count_total_dependence_audit", _uv(UV_PANDAS_NUMPY, "scripts/count_total_dependence_audit.py", "--write")),
        CommandSpec("evidence", "count_total_dependence_policy_replay", _uv(UV_BASE, "scripts/count_total_dependence_policy_replay.py", *sp, "--write"), live_api=sports_predict),
        CommandSpec("evidence", "referee_research_audit", _uv(["uv", "run", "--with", "pyyaml"], "scripts/referee_research_audit.py", "--write")),
        CommandSpec("evidence", "referee_card_adjustment_audit", _py("scripts/referee_card_adjustment_audit.py", "--write")),
        CommandSpec("evidence", "player_prop_boundary_audit", _uv(UV_BASE, "scripts/player_prop_boundary_audit.py", *sp, "--write"), live_api=sports_predict),
        CommandSpec("evidence", "player_role_research_audit", _uv(UV_BASE, "scripts/player_role_research_audit.py", "--write")),
        CommandSpec("evidence", "player_role_policy_backtest", _uv(["uv", "run", "--with", "requests", "--with", "pyyaml"], "scripts/player_role_policy_backtest.py", *sp, "--write"), live_api=sports_predict),
        CommandSpec("evidence", "player_minutes_uncertainty_audit", _uv(UV_NUMPY, "scripts/player_minutes_uncertainty_audit.py", "--write")),
        CommandSpec("evidence", "player_sot_rate_audit", _py("scripts/player_sot_rate_audit.py", "--write")),
        CommandSpec("evidence", "player_goal_involvement_rate_audit", _py("scripts/player_goal_involvement_rate_audit.py", "--write")),
        CommandSpec("evidence", "special_market_backtest", _uv(UV_NUMPY_SCIPY, "scripts/special_market_backtest.py", "--write")),
        CommandSpec("evidence", "special_prior_shrinkage_backtest", _py("scripts/special_prior_shrinkage_backtest.py", "--write")),
        CommandSpec("evidence", "special_official_results_audit", _uv(UV_REQUESTS_NUMPY_SCIPY, "scripts/special_official_results_audit.py", *sp, "--write"), live_api=sports_predict),
        CommandSpec("evidence", "special_boundary_audit", _py("scripts/special_boundary_audit.py", "--write")),
        CommandSpec("evidence", "special_count_threshold_audit", _py("scripts/special_count_threshold_audit.py", "--write")),
        CommandSpec("evidence", "tournament_target_audit", _uv(UV_REQUESTS, "scripts/tournament_target_audit.py", "--write"), live_api=True),
        CommandSpec("evidence", "leaderboard_tail_risk", _uv(UV_REQUESTS_NUMPY_SCIPY, "scripts/leaderboard_tail_risk.py", *sp, "--open-mix", "--special-proxy", "statsbomb", "--trials", "20000", "--stage-weight", "2.0", "--write"), live_api=sports_predict),
        CommandSpec("evidence", "odds_anchor_audit", _uv(["uv", "run", "--with", "requests", "--with", "scipy"], "scripts/odds_anchor_audit.py", "--write"), live_api=True),
        CommandSpec("evidence", "polymarket_direct_coverage_audit", _uv(UV_BASE, "scripts/polymarket_direct_coverage_audit.py", "--all-open", "--write", "--snapshot-csv"), live_api=True),
        CommandSpec("evidence", "polymarket_direct_snapshot_score", _uv(UV_REQUESTS, "scripts/polymarket_direct_snapshot_score.py", *sp, "--write"), live_api=sports_predict),
        CommandSpec("evidence", "question_family_coverage_audit", _py("scripts/question_family_coverage_audit.py", "--write")),
    ]

    readiness: list[CommandSpec] = [
        CommandSpec("readiness", "polymarket_direct_anchor_risk_audit", _py("scripts/polymarket_direct_anchor_risk_audit.py", "--write")),
        CommandSpec("readiness", "odds_total_source_audit", _py("scripts/odds_total_source_audit.py", "--write")),
        CommandSpec("readiness", "brier_decomposition_audit", _uv(UV_REQUESTS, "scripts/brier_decomposition_audit.py", *sp, "--write"), live_api=sports_predict),
        CommandSpec("readiness", "manual_evidence_coverage", _uv(UV_BASE, "scripts/manual_evidence_coverage.py", "--all-open", "--write"), live_api=True),
        CommandSpec("readiness", "match_lock_readiness_audit", _py("scripts/match_lock_readiness_audit.py", "--write")),
        CommandSpec("readiness", "manual_submission_packet_audit", _py("scripts/manual_submission_packet_audit.py", "--write")),
        CommandSpec("readiness", "manual_research_queue", _uv(UV_BASE, "scripts/manual_research_queue.py", "--all-open", "--write"), live_api=True),
        CommandSpec("readiness", "manual_calibration_watchlist", _uv(UV_BASE, "scripts/manual_calibration_watchlist.py", "--all-open", "--write"), live_api=True),
        CommandSpec("readiness", "manual_execution_leverage", _py("scripts/manual_execution_leverage.py", "--write")),
        CommandSpec("readiness", "card_field_component_audit", _uv(UV_REQUESTS, "scripts/card_field_component_audit.py", *sp, "--write"), live_api=sports_predict),
        CommandSpec("readiness", "strategy_evidence_matrix", _py("scripts/strategy_evidence_matrix.py", "--write")),
        CommandSpec("readiness", "promotion_strength_audit", _py("scripts/promotion_strength_audit.py", "--write")),
        CommandSpec("readiness", "evidence_freshness_audit", _py("scripts/evidence_freshness_audit.py", "--write")),
        CommandSpec("readiness", "automation_inventory_audit", _py("scripts/automation_inventory_audit.py", "--write")),
        CommandSpec("readiness", "manual_workflow_readiness_audit", _py("scripts/manual_workflow_readiness_audit.py", "--local-date", local_date, "--write")),
        CommandSpec("readiness", "goal_completion_audit", _py("scripts/goal_completion_audit.py", "--write")),
    ]

    if profile == "live":
        return live + readiness
    if profile == "evidence":
        return evidence + readiness
    if profile == "readiness":
        return readiness
    if profile == "full":
        return live + evidence + readiness
    raise ValueError(f"unknown profile: {profile}")


def validate_plan(plan: list[CommandSpec]) -> None:
    disallowed_exact = {"submit.py", "verify_submissions.py"}
    for spec in plan:
        parts = set(spec.argv)
        basenames = {Path(part).name for part in spec.argv}
        if parts & disallowed_exact or basenames & disallowed_exact:
            raise ValueError(f"{spec.name} includes a submission script: {spec.command_text()}")
        text = spec.command_text()
        if "/predictions" in text:
            raise ValueError(f"{spec.name} includes /predictions: {text}")


def _tail(text: str, max_chars: int = 2400) -> str:
    text = text.strip()
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def run_plan(
    plan: list[CommandSpec],
    *,
    dry_run: bool,
    stop_on_failure: bool,
    max_seconds: int | None,
    command_timeout_seconds: int | None,
    on_update: Callable[[list[CommandResult]], None] | None = None,
    root: Path = ROOT,
) -> list[CommandResult]:
    started = time.monotonic()
    results: list[CommandResult] = []
    for spec in plan:
        elapsed_total = time.monotonic() - started
        if max_seconds is not None and elapsed_total >= max_seconds:
            results.append(CommandResult(spec, "SKIPPED_BUDGET", 0.0, None, "time budget reached before this command started"))
            if on_update is not None:
                on_update(results)
            continue
        if dry_run:
            results.append(CommandResult(spec, "DRY_RUN", 0.0, None, "not executed"))
            if on_update is not None:
                on_update(results)
            continue

        cmd_start = time.monotonic()
        try:
            completed = subprocess.run(
                spec.argv,
                cwd=root,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
                timeout=command_timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            elapsed = time.monotonic() - cmd_start
            output = exc.stdout or ""
            if isinstance(output, bytes):
                output = output.decode("utf-8", errors="replace")
            timeout_tail = _tail(output)
            if timeout_tail:
                timeout_tail += "\n"
            timeout_tail += f"command exceeded --command-timeout-seconds={command_timeout_seconds}"
            result = CommandResult(spec, "TIMEOUT", elapsed, None, timeout_tail)
            results.append(result)
            if on_update is not None:
                on_update(results)
            if stop_on_failure:
                break
            continue
        elapsed = time.monotonic() - cmd_start
        status = "PASS" if completed.returncode == 0 else "FAIL"
        result = CommandResult(spec, status, elapsed, completed.returncode, _tail(completed.stdout))
        results.append(result)
        if on_update is not None:
            on_update(results)
        if status == "FAIL" and stop_on_failure:
            break
    return results


def render(plan: list[CommandSpec], results: list[CommandResult], *, profile: str, local_date: str, dry_run: bool) -> str:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    counts: dict[str, int] = {}
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
    phase_counts: dict[str, int] = {}
    for spec in plan:
        phase_counts[spec.phase] = phase_counts.get(spec.phase, 0) + 1

    lines = [
        "# Manual Research Refresh",
        "",
        f"- run_utc: {now}",
        f"- profile: {profile}",
        f"- local_date: {local_date}",
        f"- dry_run: {str(dry_run).lower()}",
        "- bot_predictions: not_read",
        "- sports_predict: read_only_gets_only_when_requested",
        "- submissions: not_called",
        f"- commands_planned: {len(plan)}",
        f"- commands_recorded: {len(results)}",
        "",
        "## Status Summary",
        "",
        "| status | commands |",
        "| --- | ---: |",
    ]
    for status, count in sorted(counts.items()):
        lines.append(f"| {status} | {count} |")

    lines.extend(["", "## Phase Summary", "", "| phase | commands |", "| --- | ---: |"])
    for phase, count in sorted(phase_counts.items()):
        lines.append(f"| {phase} | {count} |")

    lines.extend(
        [
            "",
            "## Commands",
            "",
            "| phase | name | status | seconds | live_api | command |",
            "| --- | --- | --- | ---: | --- | --- |",
        ]
    )
    for result in results:
        command = result.spec.command_text().replace("|", "\\|")
        lines.append(
            f"| {result.spec.phase} | {result.spec.name} | {result.status} | "
            f"{result.elapsed_s:.1f} | {str(result.spec.live_api).lower()} | `{command}` |"
        )

    failed_or_skipped = [result for result in results if result.status not in {"PASS", "DRY_RUN"}]
    if failed_or_skipped:
        lines.extend(["", "## Output Tails", ""])
        for result in failed_or_skipped:
            lines.extend(
                [
                    f"### {result.spec.name}",
                    "",
                    f"- status: {result.status}",
                    f"- returncode: {result.returncode}",
                    "",
                    "```text",
                    result.output_tail or "-",
                    "```",
                    "",
                ]
            )

    lines.extend(
        [
            "## Interpretation",
            "",
            "- `PASS` means the command exited cleanly.",
            "- `DRY_RUN` means the command was safety-checked and logged but not executed.",
            "- `SKIPPED_BUDGET` means the configured time budget was reached before command start.",
            "- `TIMEOUT` means the command exceeded the per-command timeout and the runner moved on.",
            "- A passing refresh still requires the downstream readiness reports to say `LOCK_READY` or `READY_TO_SUBMIT` before treating entries as final.",
        ]
    )
    return "\n".join(lines) + "\n"


def _default_dates() -> tuple[str, str]:
    today = date.today().isoformat()
    return today, today.replace("-", "")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    default_local, default_espn = _default_dates()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=["live", "evidence", "readiness", "full"], default="full")
    parser.add_argument("--local-date", default=default_local, help="Local date in YYYY-MM-DD form.")
    parser.add_argument("--espn-date", default=default_espn, help="ESPN scoreboard date in YYYYMMDD form.")
    parser.add_argument("--start-date", default="2026-06-11", help="Tournament start date for current-stat audits.")
    parser.add_argument("--dry-run", action="store_true", help="Log the plan without executing commands.")
    parser.add_argument("--no-sports-predict", action="store_true", help="Omit --sports-predict from commands that support it.")
    parser.add_argument(
        "--include-polymarket-snapshot",
        action="store_true",
        help="Deprecated no-op; Polymarket direct snapshot scoring is always included in the evidence refresh.",
    )
    parser.add_argument("--stop-on-failure", action="store_true", help="Stop at the first failed command.")
    parser.add_argument("--max-seconds", type=int, default=None, help="Skip commands after this wall-clock budget is reached.")
    parser.add_argument(
        "--command-timeout-seconds",
        type=int,
        default=900,
        help="Maximum seconds for any one command before it is logged as TIMEOUT.",
    )
    parser.add_argument("--log", type=Path, default=OUT, help="Markdown log path.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    plan = build_plan(
        profile=args.profile,
        local_date=args.local_date,
        espn_date=args.espn_date,
        start_date=args.start_date,
        sports_predict=not args.no_sports_predict,
        include_polymarket_snapshot=args.include_polymarket_snapshot,
    )
    validate_plan(plan)

    def checkpoint(current_results: list[CommandResult]) -> None:
        text = render(plan, current_results, profile=args.profile, local_date=args.local_date, dry_run=args.dry_run)
        args.log.parent.mkdir(parents=True, exist_ok=True)
        args.log.write_text(text, encoding="utf-8")

    results = run_plan(
        plan,
        dry_run=args.dry_run,
        stop_on_failure=args.stop_on_failure,
        max_seconds=args.max_seconds,
        command_timeout_seconds=args.command_timeout_seconds,
        on_update=checkpoint,
    )
    text = render(plan, results, profile=args.profile, local_date=args.local_date, dry_run=args.dry_run)
    args.log.parent.mkdir(parents=True, exist_ok=True)
    args.log.write_text(text, encoding="utf-8")
    print(text)
    return 1 if any(result.status in {"FAIL", "TIMEOUT"} for result in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
