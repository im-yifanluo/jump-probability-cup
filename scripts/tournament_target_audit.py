"""Read-only tournament target audit for the manual Probability Cup entry.

This does not read `/predictions` and does not submit anything. It answers:

* how many known open markets remain,
* how large the manual score gap is to target ranks,
* what average RBP per remaining known open market would be required,
* which market families make up the known opportunity set.

The manual score and target scores are user-visible leaderboard numbers, so they
are command-line inputs rather than inferred from the bot API.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from manual.anchors import fetch_sports_predict_open_markets, sports_predict_key  # noqa: E402
from manual.policy import is_count_market, is_odds_goal_market, is_special_market  # noqa: E402
from questions.parser import MarketSpec, parse  # noqa: E402
from questions.teams import ALL_TEAM_NAMES  # noqa: E402


OUT = ROOT / "data" / "logs" / "tournament_target_audit.md"
MANUAL_POLICY_LOG = ROOT / "data" / "logs" / "manual_policy_backtest.md"
LEADERBOARD_TAIL_RISK_LOG = ROOT / "data" / "logs" / "leaderboard_tail_risk.md"

DEFAULT_CURRENT_SCORE = 1243.59
DEFAULT_SMALL_PRIZE_TARGET = 1971.75
DEFAULT_FIRST_TARGET = 2114.68
DEFAULT_KNOCKOUT_WEIGHT = 2.0

CATEGORY_EDGE_PER_ROW_FALLBACK = {
    # From data/logs/manual_policy_backtest.md after safe V1 settlement,
    # combined_delta / n. Runtime parsing below keeps this from going stale.
    "player": 165.43 / 80,
    "count_cards": 157.01 / 39,
    "count_corners": 26.56 / 36,
    "count_sot": 9.71 / 89,
    "count_offsides": -4.49 / 45,
    "odds_goal": 0.0,
    "penalty_red": 0.63 / 24,
    "count_fouls": -6.57 / 48,
    # No settled SportsPredict special-history rows yet; use zero rather than
    # inventing edge from the StatsBomb base-rate diagnostic.
    "special": 0.0,
    "other": 0.0,
    "unparsed": 0.0,
}


def _extract_category_edges() -> dict[str, float]:
    """Return historical combined-policy delta per row by category.

    The source of truth is the latest manual-policy backtest log. Fallback
    constants are only for fresh checkouts without that generated artifact.
    """

    if not MANUAL_POLICY_LOG.exists():
        return dict(CATEGORY_EDGE_PER_ROW_FALLBACK)
    text = MANUAL_POLICY_LOG.read_text(encoding="utf-8")
    edges = dict(CATEGORY_EDGE_PER_ROW_FALLBACK)
    in_table = False
    for line in text.splitlines():
        if line.strip() == "## Category Contribution":
            in_table = True
            continue
        if in_table and line.startswith("## "):
            break
        if not in_table or not line.startswith("| "):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 4 or cells[0] in {"category", "---"}:
            continue
        try:
            n = int(cells[1])
            combined_delta = float(cells[3])
        except ValueError:
            continue
        if n > 0:
            edges[cells[0]] = combined_delta / n
    return edges


@dataclass(frozen=True)
class OpenMarket:
    match_name: str
    kickoff: datetime
    market_id: str
    question: str
    category: str


@dataclass(frozen=True)
class TailRiskStrategy:
    strategy: str
    mean: str
    p95: str
    p99: str
    target_probabilities: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class TailRiskSummary:
    run_utc: str
    special_proxy: str
    special_proxy_model: str
    special_proxy_rows: str
    trials: str
    stage_weight_multiplier: str
    caveat: str
    strategies: tuple[TailRiskStrategy, ...]


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _category(spec: MarketSpec | None) -> str:
    if spec is None:
        return "unparsed"
    if spec.player:
        return "player"
    if is_odds_goal_market(spec):
        return "odds_goal"
    if is_special_market(spec):
        return "special"
    if is_count_market(spec):
        return f"count_{spec.stat or 'unknown'}"
    if spec.family in {"penalty_awarded", "penalty_or_red", "red_card"}:
        return "penalty_red"
    return "other"


def read_open_markets() -> list[OpenMarket]:
    key = sports_predict_key()
    if not key:
        raise SystemExit("SportsPredict key missing from environment/.env")
    _matches, markets = fetch_sports_predict_open_markets(key)
    rows: list[OpenMarket] = []
    for market in markets:
        match = market.get("match") or {}
        question = market.get("question", "")
        try:
            spec = parse(question, teams=ALL_TEAM_NAMES)
        except Exception:
            spec = None
        rows.append(
            OpenMarket(
                match_name=match.get("name", ""),
                kickoff=_dt(match.get("opening_time")),
                market_id=market.get("id", ""),
                question=question,
                category=_category(spec),
            )
        )
    return sorted(rows, key=lambda r: (r.kickoff, r.match_name, r.market_id))


def _extract_policy_summary() -> dict[str, str]:
    if not MANUAL_POLICY_LOG.exists():
        return {}
    text = MANUAL_POLICY_LOG.read_text(encoding="utf-8")
    out: dict[str, str] = {}
    for key, pattern in {
        "combined_delta": r"combined_policy_delta_point: ([+-]?\d+(?:\.\d+)?)",
        "ci_low": r"ci_2\.5: ([+-]?\d+(?:\.\d+)?)",
        "ci_high": r"ci_97\.5: ([+-]?\d+(?:\.\d+)?)",
    }.items():
        m = re.search(pattern, text)
        if m:
            out[key] = m.group(1)
    return out


def _extract_tail_risk_summary() -> TailRiskSummary | None:
    """Parse the latest special-aware leaderboard tail-risk diagnostic.

    This remains separate from the historical category edge table because the
    special-market rows in that diagnostic are StatsBomb proxy rows, not settled
    SportsPredict special-market history.
    """

    if not LEADERBOARD_TAIL_RISK_LOG.exists():
        return None
    text = LEADERBOARD_TAIL_RISK_LOG.read_text(encoding="utf-8")
    meta: dict[str, str] = {}
    for line in text.splitlines():
        m = re.match(r"- ([a-zA-Z0-9_]+): (.*)", line.strip())
        if m:
            meta[m.group(1)] = m.group(2)

    lines = text.splitlines()
    try:
        start = lines.index("## Strategy Tail Distribution") + 1
    except ValueError:
        return None
    table_lines: list[str] = []
    for line in lines[start:]:
        if line.startswith("## "):
            break
        if line.startswith("| "):
            table_lines.append(line)
    if len(table_lines) < 3:
        return None

    headers = [cell.strip() for cell in table_lines[0].strip().strip("|").split("|")]
    probability_indexes = [
        idx for idx, header in enumerate(headers)
        if header.startswith("Pr >= ")
    ]
    required = {"strategy", "mean", "p95", "p99"}
    if not required.issubset(headers):
        return None
    idx = {header: headers.index(header) for header in required}

    strategies: list[TailRiskStrategy] = []
    for line in table_lines[2:]:
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != len(headers):
            continue
        strategies.append(
            TailRiskStrategy(
                strategy=cells[idx["strategy"]],
                mean=cells[idx["mean"]],
                p95=cells[idx["p95"]],
                p99=cells[idx["p99"]],
                target_probabilities=tuple(
                    (headers[i], cells[i]) for i in probability_indexes
                ),
            )
        )
    if not strategies:
        return None
    return TailRiskSummary(
        run_utc=meta.get("run_utc", "NA"),
        special_proxy=meta.get("special_proxy", "none"),
        special_proxy_model=meta.get("special_proxy_model", "NA"),
        special_proxy_rows=meta.get("special_proxy_rows", "0"),
        trials=meta.get("trials", "NA"),
        stage_weight_multiplier=meta.get("stage_weight_multiplier", "NA"),
        caveat=meta.get("caveat", "NA"),
        strategies=tuple(strategies),
    )


def _target_rows(current: float, targets: list[tuple[str, float]], n_open: int, weight: float) -> list[list[str]]:
    rows = []
    for label, target in targets:
        gap = target - current
        per_market = gap / n_open if n_open else float("nan")
        per_base_weighted = gap / (n_open * weight) if n_open and weight else float("nan")
        rows.append(
            [
                label,
                f"{target:.2f}",
                f"{gap:.2f}",
                f"{per_market:.2f}",
                f"{per_base_weighted:.2f}",
            ]
        )
    return rows


def _md_table(headers: list[str], rows: list[list[str]]) -> list[str]:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return lines


def render(
    markets: list[OpenMarket],
    *,
    current_score: float,
    small_prize_target: float,
    first_target: float,
    assumed_weight: float,
    now: datetime,
) -> str:
    by_match: dict[tuple[datetime, str], list[OpenMarket]] = defaultdict(list)
    for row in markets:
        by_match[(row.kickoff, row.match_name)].append(row)

    cats = Counter(row.category for row in markets)
    category_edges = _extract_category_edges()
    estimated_incremental_edge = sum(
        cats[cat] * category_edges.get(cat, 0.0)
        for cat in cats
    )
    policy = _extract_policy_summary()
    tail_risk = _extract_tail_risk_summary()

    lines = [
        "# Tournament Target Audit",
        "",
        f"- run_utc: {now.astimezone(timezone.utc).isoformat(timespec='seconds')}",
        "- bot_predictions: not_read",
        "- sports_predict: read_only GET /events, /lobbies, /matches, /markets only",
        f"- current_manual_score_input: {current_score:.2f}",
        f"- known_open_markets: {len(markets)}",
        f"- known_open_matches: {len(by_match)}",
        f"- assumed_stage_weight_for_base_equivalent: {assumed_weight:.1f}x",
        "- category_edge_source: latest `data/logs/manual_policy_backtest.md` category table when present; safe-settlement fallback constants otherwise",
        "",
        "## Target Gap",
        "",
    ]
    lines += _md_table(
        [
            "target",
            "score",
            "gap",
            "required_rbp_per_known_open_market",
            "required_base_rbp_per_weighted_unit",
        ],
        _target_rows(
            current_score,
            [("#10 small prize", small_prize_target), ("#1 first", first_target)],
            len(markets),
            assumed_weight,
        ),
    )
    lines += [
        "",
        "## Known Open Matches",
        "",
        "| kickoff_utc | match | open_markets |",
        "| --- | --- | ---: |",
    ]
    for (kickoff, match), part in sorted(by_match.items()):
        lines.append(f"| {kickoff.isoformat(timespec='minutes')} | {match} | {len(part)} |")

    lines += [
        "",
        "## Known Open Market Mix",
        "",
        "| category | markets | historical_combined_policy_delta_per_row | rough_mix_edge |",
        "| --- | ---: | ---: | ---: |",
    ]
    for cat, n in sorted(cats.items()):
        per = category_edges.get(cat, 0.0)
        lines.append(f"| {cat} | {n} | {per:.2f} | {n * per:.2f} |")
    lines += [
        f"| **total_known_mix** | **{len(markets)}** |  | **{estimated_incremental_edge:.2f}** |",
        "",
        "## Backtest Anchor",
        "",
        "- Combined manual policy backtest log: `data/logs/manual_policy_backtest.md`.",
    ]
    if policy:
        lines += [
            f"- combined_policy_delta_point: {policy.get('combined_delta', 'NA')} RBP on settled history",
            f"- match_bootstrap_ci: {policy.get('ci_low', 'NA')} to {policy.get('ci_high', 'NA')} RBP",
        ]
    if tail_risk:
        wanted = {
            "combined_manual_policy",
            "combined_count_field",
            "combined_goal_field",
            "combined_penalty_red_field",
            "combined_player_field",
            "combined_raw_special",
            "current",
            "hard_model_side_99_1",
            "hard_field_deviation_99_1",
        }
        selected = [row for row in tail_risk.strategies if row.strategy in wanted]
        if selected:
            prob_headers = [label for label, _ in selected[0].target_probabilities]
            lines += [
                "",
                "## Special-Aware Tail-Risk Proxy",
                "",
                f"- Source log: `data/logs/leaderboard_tail_risk.md`, run {tail_risk.run_utc}.",
                f"- special_proxy: {tail_risk.special_proxy}; special_proxy_rows: {tail_risk.special_proxy_rows}; trials: {tail_risk.trials}; stage_weight_multiplier: {tail_risk.stage_weight_multiplier}.",
                f"- special_proxy_model: {tail_risk.special_proxy_model}",
                f"- Caveat: {tail_risk.caveat}",
                "- This is not folded into `rough_mix_edge`; it is a separate future-slate simulation because the special rows are proxy data, not settled SportsPredict history.",
                "",
            ]
            table_rows = []
            for row in selected:
                table_rows.append(
                    [
                        row.strategy,
                        row.mean,
                        row.p95,
                        row.p99,
                        *[value for _, value in row.target_probabilities],
                    ]
                )
            lines += _md_table(
                ["strategy", "mean", "p95", "p99", *prob_headers],
                table_rows,
            )
    lines += [
        "",
        "## Interpretation",
        "",
        "- The target rows are arithmetic, not an expected-score forecast.",
        "- `known_open_markets` only includes markets currently exposed by the API; later unopened matches can add opportunity.",
        "- The rough mix edge is a transfer of historical combined-policy delta by category. It is useful for scale, but it is not a promise of future RBP.",
        "- The required average RBP per known open market is much larger than the historical per-row combined-policy lift. The rational response is not extremizing; it is maximizing calibrated edge and clearing lineup/player/referee/current-source flags before each kickoff.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--current-score", type=float, default=DEFAULT_CURRENT_SCORE)
    ap.add_argument("--small-prize-target", type=float, default=DEFAULT_SMALL_PRIZE_TARGET)
    ap.add_argument("--first-target", type=float, default=DEFAULT_FIRST_TARGET)
    ap.add_argument("--assumed-weight", type=float, default=DEFAULT_KNOCKOUT_WEIGHT)
    ap.add_argument("--write", action="store_true", help=f"write {OUT}")
    args = ap.parse_args()

    markets = read_open_markets()
    text = render(
        markets,
        current_score=args.current_score,
        small_prize_target=args.small_prize_target,
        first_target=args.first_target,
        assumed_weight=args.assumed_weight,
        now=datetime.now(timezone.utc),
    )
    print(text)
    if args.write:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(text, encoding="utf-8")
        print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
