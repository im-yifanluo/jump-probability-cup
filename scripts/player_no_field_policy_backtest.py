"""No-field player-prop policy backtest.

This read-only diagnostic answers a narrower operational question than
`player_policy_backtest.py`: when the manual workflow has no visible
SportsPredict field average and no exact current prop-market anchor, should
player props use the raw player model probability, or a simple time-safe
calibration learned from earlier settled player props?

The candidate set is deliberately small and uses only prior settled rows and
the current row's model probability. It never reads `/predictions` and never
submits. With `--sports-predict`, SportsPredict is used only for GET
`/events`, `/lobbies`, and `/results` to settle older V1 history rows.

Usage:
    uv run --with pandas --with numpy --with scipy --with pyarrow --with pyyaml --with requests --with statsmodels \
      python scripts/player_no_field_policy_backtest.py --sports-predict --write
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
import random
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from backtest.history import (  # noqa: E402
    HistoryMarket,
    brier,
    clip_prob,
    official_delta_rbp,
)
from player_policy_backtest import load_player_rows  # noqa: E402
from questions.parser import parse  # noqa: E402
from questions.teams import ALL_TEAM_NAMES  # noqa: E402


OUT = ROOT / "data" / "logs" / "player_no_field_policy_backtest.md"


@dataclass(frozen=True)
class ScoredPrediction:
    row: HistoryMarket
    predictions: dict[str, float]


def beta_mean(rows: list[HistoryMarket], *, prior: float = 0.5, strength: float = 2.0) -> float:
    """Smoothed empirical hit rate for prior-only chronological calibration."""

    if strength < 0:
        raise ValueError("strength must be non-negative")
    hits = sum(row.outcome or 0 for row in rows)
    return clip_prob((hits + prior * strength) / (len(rows) + strength))


def family_base_rates(
    train: list[HistoryMarket],
    *,
    global_base: float,
    strength: float,
) -> dict[str, float]:
    grouped: dict[str, list[HistoryMarket]] = defaultdict(list)
    for row in train:
        grouped[row.famkey].append(row)
    return {
        family: beta_mean(rows, prior=global_base, strength=strength)
        for family, rows in grouped.items()
    }


def player_bucket(row: HistoryMarket) -> str:
    """Operational bucket for no-field player calibration.

    Broad families hid a meaningful question: 1+ SOT props and 2+/3+ SOT props
    do not necessarily deserve the same no-field base rate. Parse thresholds
    when possible; fall back to the existing famkey for old/unparsed text.
    """

    try:
        spec = parse(row.question, teams=ALL_TEAM_NAMES)
    except Exception:
        return row.famkey
    if spec.family == "player_sot_ge" and spec.threshold:
        return f"{row.famkey}/ge{spec.threshold}"
    return row.famkey


def bucket_base_rates(
    train: list[HistoryMarket],
    *,
    global_base: float,
    strength: float,
) -> dict[str, float]:
    grouped: dict[str, list[HistoryMarket]] = defaultdict(list)
    for row in train:
        grouped[player_bucket(row)].append(row)
    return {
        bucket: beta_mean(rows, prior=global_base, strength=strength)
        for bucket, rows in grouped.items()
    }


def policy_predictions(
    row: HistoryMarket,
    *,
    global_base: float,
    family_base: float,
    bucket_base: float,
) -> dict[str, float]:
    """Predeclared no-field policies for one row.

    Names use `modelXX_*YY`, where XX is the retained raw model weight and YY is
    the prior-base weight. All policies are deployable without field averages.
    """

    p = row.you
    return {
        "raw_model": p,
        "prior_global_base": global_base,
        "prior_family_base": family_base,
        "prior_bucket_base": bucket_base,
        "raw_scores_else_global_base": p if row.family == "player_scores" else global_base,
        "raw_scores_else_family_base": p if row.family == "player_scores" else family_base,
        "raw_scores_else_bucket_base": p if row.family == "player_scores" else bucket_base,
        "model75_global25": 0.75 * p + 0.25 * global_base,
        "model50_global50": 0.50 * p + 0.50 * global_base,
        "model25_global75": 0.25 * p + 0.75 * global_base,
        "model75_family25": 0.75 * p + 0.25 * family_base,
        "model50_family50": 0.50 * p + 0.50 * family_base,
        "model25_family75": 0.25 * p + 0.75 * family_base,
        "model50_bucket50": 0.50 * p + 0.50 * bucket_base,
        "model25_bucket75": 0.25 * p + 0.75 * bucket_base,
    }


def chronological_predictions(
    rows: list[HistoryMarket],
    *,
    min_train: int,
    global_strength: float,
    family_strength: float,
) -> list[ScoredPrediction]:
    """Return one-match-ahead no-field predictions after `min_train` rows."""

    scored: list[ScoredPrediction] = []
    for order in sorted({row.order for row in rows}):
        train = [row for row in rows if row.order < order]
        if len(train) < min_train:
            continue
        global_base = beta_mean(train, prior=0.5, strength=global_strength)
        family_rates = family_base_rates(train, global_base=global_base, strength=family_strength)
        bucket_rates = bucket_base_rates(train, global_base=global_base, strength=family_strength)
        for row in [row for row in rows if row.order == order]:
            family_base = family_rates.get(row.famkey, global_base)
            bucket_base = bucket_rates.get(player_bucket(row), family_base)
            scored.append(
                ScoredPrediction(
                    row=row,
                    predictions={
                        name: clip_prob(value)
                        for name, value in policy_predictions(
                            row,
                            global_base=global_base,
                            family_base=family_base,
                            bucket_base=bucket_base,
                        ).items()
                    },
                )
            )
    return scored


def score_predictions(scored: list[ScoredPrediction], policy: str) -> dict[str, float]:
    if not scored:
        return {"n": 0, "delta_vs_raw": 0.0, "brier": float("nan"), "mean_p": float("nan")}
    delta = 0.0
    br = 0.0
    mean_p = 0.0
    for item in scored:
        row = item.row
        p = clip_prob(item.predictions[policy])
        delta += official_delta_rbp(row, p) - official_delta_rbp(row, row.you)
        br += brier(p, row.outcome or 0)
        mean_p += p
    return {
        "n": len(scored),
        "delta_vs_raw": delta,
        "brier": br / len(scored),
        "mean_p": mean_p / len(scored),
    }


def score_field_context(scored: list[ScoredPrediction]) -> dict[str, float]:
    """Visible-field context only; not a no-field deployable policy."""

    if not scored:
        return {"n": 0, "delta_vs_raw": 0.0, "brier": float("nan"), "mean_p": float("nan")}
    delta = br = mean_p = 0.0
    for item in scored:
        row = item.row
        delta += official_delta_rbp(row, row.crowd) - official_delta_rbp(row, row.you)
        br += brier(row.crowd, row.outcome or 0)
        mean_p += row.crowd
    return {
        "n": len(scored),
        "delta_vs_raw": delta,
        "brier": br / len(scored),
        "mean_p": mean_p / len(scored),
    }


def policy_summary(scored: list[ScoredPrediction]) -> list[dict[str, float | str]]:
    if not scored:
        return []
    names = list(scored[0].predictions)
    rows: list[dict[str, float | str]] = []
    for name in names:
        rows.append({"policy": name, **score_predictions(scored, name)})
    rows.append({"policy": "visible_field_context", **score_field_context(scored)})
    return sorted(rows, key=lambda row: float(row["delta_vs_raw"]), reverse=True)


def sensitivity_grid(
    rows: list[HistoryMarket],
    *,
    policy: str,
    min_trains: list[int],
    global_strengths: list[float],
    family_strength: float,
) -> list[dict[str, float | str]]:
    """Sensitivity of a fixed no-field policy to chronological training choices."""

    out: list[dict[str, float | str]] = []
    for min_train in min_trains:
        for global_strength in global_strengths:
            scored = chronological_predictions(
                rows,
                min_train=min_train,
                global_strength=global_strength,
                family_strength=family_strength,
            )
            if not scored or not scored[0].predictions.get(policy):
                continue
            target = score_predictions(scored, policy)
            deployable = [row for row in policy_summary(scored) if row["policy"] != "visible_field_context"]
            best = deployable[0] if deployable else {"policy": "", "delta_vs_raw": 0.0}
            rank = next(
                idx + 1
                for idx, row in enumerate(deployable)
                if row["policy"] == policy
            )
            out.append(
                {
                    "min_train": min_train,
                    "global_strength": global_strength,
                    "holdout_rows": int(target["n"]),
                    "delta_vs_raw": float(target["delta_vs_raw"]),
                    "brier": float(target["brier"]),
                    "mean_p": float(target["mean_p"]),
                    "deployable_rank": rank,
                    "best_policy": str(best["policy"]),
                    "best_delta_vs_raw": float(best["delta_vs_raw"]),
                }
            )
    return sorted(out, key=lambda row: (int(row["min_train"]), float(row["global_strength"])))


def sensitivity_summary(rows: list[dict[str, float | str]], policy: str) -> dict[str, float | str]:
    if not rows:
        return {
            "policy": policy,
            "configs": 0,
            "positive_configs": 0,
            "best_policy_configs": 0,
            "min_delta_vs_raw": 0.0,
            "median_delta_vs_raw": 0.0,
            "max_delta_vs_raw": 0.0,
        }
    deltas = sorted(float(row["delta_vs_raw"]) for row in rows)
    return {
        "policy": policy,
        "configs": len(rows),
        "positive_configs": sum(1 for row in rows if float(row["delta_vs_raw"]) > 0),
        "best_policy_configs": sum(1 for row in rows if str(row["best_policy"]) == policy),
        "min_delta_vs_raw": deltas[0],
        "median_delta_vs_raw": deltas[len(deltas) // 2],
        "max_delta_vs_raw": deltas[-1],
    }


def bootstrap_policy_diff(
    scored: list[ScoredPrediction],
    left: str,
    right: str,
    *,
    trials: int,
    seed: int,
) -> dict[str, float]:
    """Match-level bootstrap for left-minus-right on fixed walk-forward predictions."""

    by_match: dict[str, list[ScoredPrediction]] = defaultdict(list)
    for item in scored:
        by_match[item.row.match].append(item)
    matches = sorted(by_match)
    if not matches:
        return {"ci_2.5": 0.0, "median": 0.0, "ci_97.5": 0.0, "pr_gt_0": 0.0}

    rng = random.Random(seed)
    draws: list[float] = []
    for _ in range(trials):
        delta = 0.0
        for match in (rng.choice(matches) for _ in matches):
            for item in by_match[match]:
                row = item.row
                delta += official_delta_rbp(row, clip_prob(item.predictions[left]))
                if right == "visible_field_context":
                    delta -= official_delta_rbp(row, row.crowd)
                else:
                    delta -= official_delta_rbp(row, clip_prob(item.predictions[right]))
        draws.append(delta)
    draws.sort()
    return {
        "ci_2.5": draws[int(0.025 * (trials - 1))],
        "median": draws[int(0.500 * (trials - 1))],
        "ci_97.5": draws[int(0.975 * (trials - 1))],
        "pr_gt_0": sum(value > 0 for value in draws) / len(draws),
    }


def family_diagnostics(scored: list[ScoredPrediction], policy: str) -> list[dict[str, float | str]]:
    grouped: dict[str, list[ScoredPrediction]] = defaultdict(list)
    for item in scored:
        grouped[item.row.famkey].append(item)
    out: list[dict[str, float | str]] = []
    for family, part in sorted(grouped.items()):
        delta = sum(
            official_delta_rbp(item.row, clip_prob(item.predictions[policy]))
            - official_delta_rbp(item.row, item.row.you)
            for item in part
        )
        out.append(
            {
                "family": family,
                "n": len(part),
                "delta_vs_raw": delta,
                "raw": sum(item.row.you for item in part) / len(part),
                "policy": sum(item.predictions[policy] for item in part) / len(part),
                "field": sum(item.row.crowd for item in part) / len(part),
                "hit": sum(item.row.outcome or 0 for item in part) / len(part),
            }
        )
    return out


def bucket_diagnostics(scored: list[ScoredPrediction], policy: str) -> list[dict[str, float | str]]:
    grouped: dict[str, list[ScoredPrediction]] = defaultdict(list)
    for item in scored:
        grouped[player_bucket(item.row)].append(item)
    out: list[dict[str, float | str]] = []
    for bucket, part in sorted(grouped.items()):
        delta = sum(
            official_delta_rbp(item.row, clip_prob(item.predictions[policy]))
            - official_delta_rbp(item.row, item.row.you)
            for item in part
        )
        out.append(
            {
                "bucket": bucket,
                "n": len(part),
                "delta_vs_raw": delta,
                "raw": sum(item.row.you for item in part) / len(part),
                "policy": sum(item.predictions[policy] for item in part) / len(part),
                "field": sum(item.row.crowd for item in part) / len(part),
                "hit": sum(item.row.outcome or 0 for item in part) / len(part),
            }
        )
    return out


def build_current_source_proxy_lookup():
    """Best-effort current player-source classifier for historical rows.

    This is deliberately labelled as a proxy: it uses today's StatsBomb cache
    and player overrides, not a time-machine reconstruction of the source that
    existed when each historical prediction was made.
    """

    try:
        from models.players import PlayerModel
        from wcdata.statsbomb import build_aggregates

        _team_halves, players = build_aggregates()
        model = PlayerModel(players)
    except Exception as exc:  # pragma: no cover - optional dependency/data guard
        return None, f"{exc.__class__.__name__}: {exc}"

    def _lookup(row: HistoryMarket) -> str:
        try:
            spec = parse(row.question, teams=ALL_TEAM_NAMES)
        except Exception:
            return "unparsed"
        if not spec.player:
            return "non_player"
        return model.rates(spec.player).source

    return _lookup, ""


def source_proxy_diagnostics(
    scored: list[ScoredPrediction],
    policy: str,
    *,
    source_lookup,
) -> list[dict[str, float | str]]:
    grouped: dict[str, list[ScoredPrediction]] = defaultdict(list)
    for item in scored:
        grouped[str(source_lookup(item.row))].append(item)
    out: list[dict[str, float | str]] = []
    for source, part in sorted(grouped.items()):
        delta = sum(
            official_delta_rbp(item.row, clip_prob(item.predictions[policy]))
            - official_delta_rbp(item.row, item.row.you)
            for item in part
        )
        out.append(
            {
                "source_proxy": source,
                "n": len(part),
                "delta_vs_raw": delta,
                "raw": sum(item.row.you for item in part) / len(part),
                "policy": sum(item.predictions[policy] for item in part) / len(part),
                "field": sum(item.row.crowd for item in part) / len(part),
                "hit": sum(item.row.outcome or 0 for item in part) / len(part),
            }
        )
    return out


def md_table(headers: list[str], rows: list[list[str]]) -> list[str]:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return lines


def render(
    rows: list[HistoryMarket],
    *,
    min_train: int,
    global_strength: float,
    family_strength: float,
    trials: int,
    seed: int,
) -> str:
    scored = chronological_predictions(
        rows,
        min_train=min_train,
        global_strength=global_strength,
        family_strength=family_strength,
    )
    summaries = policy_summary(scored)
    best = next(row["policy"] for row in summaries if row["policy"] != "visible_field_context")
    best = str(best)
    source_lookup, source_error = build_current_source_proxy_lookup()
    direct = bootstrap_policy_diff(scored, best, "raw_model", trials=trials, seed=seed) if trials else None
    field_direct = (
        bootstrap_policy_diff(scored, "raw_model", "visible_field_context", trials=trials, seed=seed)
        if trials
        else None
    )
    sensitivity_rows = sensitivity_grid(
        rows,
        policy=best,
        min_trains=[10, 20, 30, 40, 50],
        global_strengths=[0.0, 2.0, 5.0, 10.0, 20.0],
        family_strength=family_strength,
    )
    robustness = sensitivity_summary(sensitivity_rows, best)

    lines = [
        "# Player No-Field Policy Backtest",
        "",
        "Read-only analysis. `/predictions` was not read and no submissions were made.",
        "",
        f"- settled_player_rows: {len(rows)}",
        f"- matches: {len({row.match for row in rows})}",
        f"- chronological_holdout_rows: {len(scored)}",
        f"- min_train: {min_train}",
        f"- global_beta_strength: {global_strength:g}",
        f"- family_beta_strength: {family_strength:g}",
        "- scoring: each candidate is trained only on earlier settled player rows, then scored against the next match/order",
        "- deployable inputs: row model probability plus earlier settled player-prop outcomes only; no field average and no bot predictions",
        "",
        "## Chronological No-Field Policies",
        "",
    ]
    lines += md_table(
        ["policy", "n", "delta_vs_raw", "brier", "mean_p"],
        [
            [
                str(row["policy"]),
                f"{int(row['n'])}",
                f"{float(row['delta_vs_raw']):.2f}",
                f"{float(row['brier']):.4f}",
                f"{float(row['mean_p']):.3f}",
            ]
            for row in summaries
        ],
    )
    lines += [
        "",
        "## Best No-Field Family Split",
        "",
        f"Best deployable no-field policy by chronological delta: `{best}`.",
        "",
    ]
    lines += md_table(
        ["family", "n", "delta_vs_raw", "raw", best, "field_context", "hit"],
        [
            [
                str(row["family"]),
                f"{int(row['n'])}",
                f"{float(row['delta_vs_raw']):.2f}",
                f"{float(row['raw']):.3f}",
                f"{float(row['policy']):.3f}",
                f"{float(row['field']):.3f}",
                f"{float(row['hit']):.3f}",
            ]
            for row in family_diagnostics(scored, best)
        ],
    )
    lines += [
        "",
        "## Threshold Bucket Diagnostics",
        "",
    ]
    lines += md_table(
        ["bucket", "n", "delta_vs_raw", "raw", best, "field_context", "hit"],
        [
            [
                str(row["bucket"]),
                f"{int(row['n'])}",
                f"{float(row['delta_vs_raw']):.2f}",
                f"{float(row['raw']):.3f}",
                f"{float(row['policy']):.3f}",
                f"{float(row['field']):.3f}",
                f"{float(row['hit']):.3f}",
            ]
            for row in bucket_diagnostics(scored, best)
        ],
    )
    lines += [
        "",
        "## Sensitivity Summary",
        "",
        f"- robustness_policy: {robustness['policy']}",
        f"- robustness_configs: {robustness['configs']}",
        f"- robustness_positive_configs: {robustness['positive_configs']}",
        f"- robustness_best_policy_configs: {robustness['best_policy_configs']}",
        f"- robustness_min_delta_vs_raw: {float(robustness['min_delta_vs_raw']):.2f}",
        f"- robustness_median_delta_vs_raw: {float(robustness['median_delta_vs_raw']):.2f}",
        f"- robustness_max_delta_vs_raw: {float(robustness['max_delta_vs_raw']):.2f}",
        "",
        "The grid varies `min_train` over 10, 20, 30, 40, 50 and global beta strength over 0, 2, 5, 10, 20 while holding family beta strength fixed. It is a parameter-sensitivity check, not a new tuning search.",
        "",
    ]
    lines += md_table(
        [
            "min_train",
            "global_strength",
            "holdout_rows",
            "delta_vs_raw",
            "brier",
            "mean_p",
            "deployable_rank",
            "best_policy",
            "best_delta_vs_raw",
        ],
        [
            [
                f"{int(row['min_train'])}",
                f"{float(row['global_strength']):g}",
                f"{int(row['holdout_rows'])}",
                f"{float(row['delta_vs_raw']):.2f}",
                f"{float(row['brier']):.4f}",
                f"{float(row['mean_p']):.3f}",
                f"{int(row['deployable_rank'])}",
                str(row["best_policy"]),
                f"{float(row['best_delta_vs_raw']):.2f}",
            ]
            for row in sensitivity_rows
        ],
    )
    lines += [
        "",
        "## Current Source-Proxy Diagnostics",
        "",
        "- source_proxy: current PlayerModel source from today's StatsBomb cache and player overrides; this is not a historical source reconstruction.",
        "- purpose: check whether the promoted no-field fallback is supported only by researched/history-backed players or also by source-poor prior rows.",
        "",
    ]
    if source_lookup is None:
        lines.append(f"- source_proxy_unavailable: {source_error}")
    else:
        source_rows = source_proxy_diagnostics(scored, best, source_lookup=source_lookup)
        lines += md_table(
            ["source_proxy", "n", "delta_vs_raw", "raw", best, "field_context", "hit"],
            [
                [
                    str(row["source_proxy"]),
                    f"{int(row['n'])}",
                    f"{float(row['delta_vs_raw']):.2f}",
                    f"{float(row['raw']):.3f}",
                    f"{float(row['policy']):.3f}",
                    f"{float(row['field']):.3f}",
                    f"{float(row['hit']):.3f}",
                ]
                for row in source_rows
            ],
        )
        prior_n = sum(int(row["n"]) for row in source_rows if str(row["source_proxy"]) == "prior")
        if prior_n == 0:
            lines += [
                "",
                "- source_proxy_guardrail: no holdout rows are classified as current-source `prior`; current `PRIOR_ONLY` props should be treated as source-unvalidated unless exact prop odds or structured player research clears the flag.",
            ]
    if trials:
        lines += [
            "",
            "## Match-Level Bootstrap",
            "",
            f"Trials: {trials}; seed: {seed}. Bootstrap is over fixed walk-forward predictions on holdout rows.",
            "",
            "| comparison | point_delta | ci_2.5 | median | ci_97.5 | pr_gt_0 |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
        point = score_predictions(scored, best)["delta_vs_raw"]
        lines.append(
            f"| {best}_minus_raw_model | {point:.2f} | {direct['ci_2.5']:.2f} | "
            f"{direct['median']:.2f} | {direct['ci_97.5']:.2f} | {direct['pr_gt_0']:.3f} |"
        )
        field_point = -score_field_context(scored)["delta_vs_raw"]
        lines.append(
            f"| raw_model_minus_visible_field_context | {field_point:.2f} | "
            f"{field_direct['ci_2.5']:.2f} | {field_direct['median']:.2f} | "
            f"{field_direct['ci_97.5']:.2f} | {field_direct['pr_gt_0']:.3f} |"
        )
    lines += [
        "",
        "## Interpretation",
        "",
        "- This is a no-field fallback diagnostic only. If a current exact prop-market anchor exists, use that; field averages are unavailable before lock.",
        "- The chronological holdout currently contains scorer, score/assist, and 1+ SOT buckets, but no 2+/3+ SOT threshold rows. Do not apply the global no-field base to higher-threshold player SOT props until settled rows validate that extrapolation.",
        "- Promote a no-field fallback only if it beats `raw_model` chronologically and the match-bootstrap does not make the gain look like pure noise.",
        "- If no no-field candidate is robust, keep the current manual sheet behavior but mark unresolved player props as research/field-dependent rather than pretending the raw value is high confidence.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sports-predict", action="store_true", help="GET-only settlement for older V1 history rows")
    ap.add_argument("--min-train", type=int, default=30)
    ap.add_argument("--global-strength", type=float, default=2.0)
    ap.add_argument("--family-strength", type=float, default=4.0)
    ap.add_argument("--bootstrap", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=31)
    ap.add_argument("--write", action="store_true", help=f"write {OUT}")
    args = ap.parse_args()

    rows = load_player_rows(sports_predict=args.sports_predict)
    text = render(
        rows,
        min_train=args.min_train,
        global_strength=args.global_strength,
        family_strength=args.family_strength,
        trials=args.bootstrap,
        seed=args.seed,
    )
    print(text)
    if args.write:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(text, encoding="utf-8")
        print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
