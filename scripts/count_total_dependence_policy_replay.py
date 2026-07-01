"""Replay direct-total NB count-shape candidates on settled Cup count rows.

`count_total_dependence_audit.py` found strong club-history proxy evidence that
ordinary total-count thresholds are better represented by a direct total NB
distribution than by convolving independent team NB marginals. This script asks
the stricter SportsPredict-relevance question: on settled Cup total-count rows
with reconstructed ESPN/DraftKings match context, would that shape improve the
raw model and the deployable manual-field policy?

It is read-only. It may use SportsPredict only for GET `/events`, `/lobbies`,
and `/results` through `count_policy_backtest.load_count_rows`; it never reads
`/predictions` and never submits anything.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
import random
import sys

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "logs" / "count_total_dependence_policy_replay.md"

sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from backtest.history import HistoryMarket, brier, official_delta_rbp  # noqa: E402
from count_intensity_replay import MatchContext, build_contexts, price_count_spec  # noqa: E402
from count_policy_backtest import load_count_rows  # noqa: E402
from count_total_dependence_audit import add_implied_lambdas, load_source_matches, p_count_ge  # noqa: E402
from manual.policy import manual_decision  # noqa: E402
from models.counts import _moment_alpha, load_count_models  # noqa: E402
from questions.parser import MarketSpec, parse  # noqa: E402
from questions.teams import ALL_TEAM_NAMES  # noqa: E402


BASELINE = "current_independent"
CANDIDATES = ("current_independent", "direct_total_nb_match_only", "direct_total_nb_all_periods")
SUPPORTED_STATS = ("cards", "corners", "sot", "fouls")
MIN_PROMOTION_ROWS = 40
MIN_PROMOTION_MATCHES = 20


@dataclass(frozen=True)
class TotalAlpha:
    stat: str
    alpha_total: float
    source_rows: int
    mean_total: float


@dataclass(frozen=True)
class ReplayedRow:
    row: HistoryMarket
    candidate: str
    model_probability: float
    manual_probability: float
    policy: str
    applied_direct_total: bool


def _finite(values: list[np.ndarray]) -> np.ndarray:
    mask = np.ones(len(values[0]), dtype=bool)
    for value in values:
        mask &= np.isfinite(value)
    return mask


def _side_means_from_params(matches: pd.DataFrame, stat: str, params: dict) -> tuple[np.ndarray, np.ndarray]:
    c = params[stat]["coef"]
    lam_home = matches["lam_home"].to_numpy(dtype=float)
    lam_away = matches["lam_away"].to_numpy(dtype=float)
    home = np.exp(
        c["const"]
        + c["log_lam_team"] * np.log(lam_home)
        + c["log_lam_opp"] * np.log(lam_away)
        + c["is_home"]
    )
    away = np.exp(
        c["const"]
        + c["log_lam_team"] * np.log(lam_away)
        + c["log_lam_opp"] * np.log(lam_home)
    )
    return home, away


def estimate_total_alphas(params: dict | None = None, source_matches: pd.DataFrame | None = None) -> dict[str, TotalAlpha]:
    """Estimate total-count NB dispersion from production marginal means.

    This deliberately reuses the production count-model coefficients and only
    estimates total residual dispersion on club history. It does not write
    `data/count_models.json`.
    """

    params = params or load_count_models()
    matches = add_implied_lambdas(source_matches if source_matches is not None else load_source_matches())
    out: dict[str, TotalAlpha] = {}
    for stat in SUPPORTED_STATS:
        home = pd.to_numeric(matches[f"home_{stat}"], errors="coerce").to_numpy(dtype=float)
        away = pd.to_numeric(matches[f"away_{stat}"], errors="coerce").to_numpy(dtype=float)
        home_mu, away_mu = _side_means_from_params(matches, stat, params)
        mask = _finite([home, away, home_mu, away_mu])
        total_y = home[mask] + away[mask]
        total_mu = home_mu[mask] + away_mu[mask]
        out[stat] = TotalAlpha(
            stat=stat,
            alpha_total=_moment_alpha(total_y, total_mu),
            source_rows=int(mask.sum()),
            mean_total=float(total_y.mean()) if len(total_y) else float("nan"),
        )
    return out


def _direct_total_probability(spec: MarketSpec, ctx: MatchContext, alpha: float) -> float:
    mean = ctx.counts.mean(spec.stat, "home", spec.period) + ctx.counts.mean(spec.stat, "away", spec.period)
    return float(p_count_ge(np.array([mean]), alpha, spec.threshold)[0])


def price_candidate(spec: MarketSpec, ctx: MatchContext, alphas: dict[str, TotalAlpha], candidate: str) -> tuple[float, bool]:
    current = price_count_spec(spec, ctx)
    if spec.family != "total_count_ge":
        return current, False
    if spec.stat not in alphas:
        return current, False
    if candidate == "direct_total_nb_match_only" and spec.period != "match":
        return current, False
    if candidate in {"direct_total_nb_match_only", "direct_total_nb_all_periods"}:
        return _direct_total_probability(spec, ctx, alphas[spec.stat].alpha_total), True
    return current, False


def total_count_rows(rows: list[HistoryMarket]) -> list[HistoryMarket]:
    return [row for row in rows if row.family == "total_count_ge"]


def replay_rows(
    rows: list[HistoryMarket],
    contexts: dict[str, MatchContext],
    alphas: dict[str, TotalAlpha],
    *,
    candidate: str,
) -> tuple[list[ReplayedRow], list[str]]:
    out: list[ReplayedRow] = []
    skipped: list[str] = []
    for row in rows:
        ctx = contexts.get(row.match)
        if ctx is None:
            skipped.append(f"{row.match}: no context")
            continue
        try:
            spec = parse(row.question, teams=ALL_TEAM_NAMES)
            model_p, applied = price_candidate(spec, ctx, alphas, candidate)
            decision = manual_decision(spec, model_p, field_probability=row.crowd)
        except Exception as exc:
            skipped.append(f"{row.match}: {row.question}: {exc}")
            continue
        out.append(
            ReplayedRow(
                row=row,
                candidate=candidate,
                model_probability=model_p,
                manual_probability=decision.probability / 100,
                policy=decision.policy,
                applied_direct_total=applied,
            )
        )
    return out, skipped


def summarize(rows: list[ReplayedRow], *, probability_attr: str) -> dict[str, float]:
    if not rows:
        return {"n": 0, "brier": float("nan"), "delta_vs_submitted": 0.0, "mean_p": float("nan"), "direct_rows": 0}
    probabilities = [float(getattr(row, probability_attr)) for row in rows]
    return {
        "n": len(rows),
        "brier": sum(brier(p, row.row.outcome or 0) for p, row in zip(probabilities, rows)) / len(rows),
        "delta_vs_submitted": sum(official_delta_rbp(row.row, p) for p, row in zip(probabilities, rows)),
        "mean_p": sum(probabilities) / len(probabilities),
        "direct_rows": sum(1 for row in rows if row.applied_direct_total),
    }


def _key(row: ReplayedRow) -> tuple[str, str]:
    return row.row.match, row.row.question


def paired_delta(candidate_rows: list[ReplayedRow], baseline_rows: list[ReplayedRow], *, probability_attr: str) -> dict[str, float]:
    baseline = {_key(row): row for row in baseline_rows}
    paired = [(row, baseline[_key(row)]) for row in candidate_rows if _key(row) in baseline]
    if not paired:
        return {"n": 0, "matches": 0, "delta": 0.0}
    return {
        "n": len(paired),
        "matches": len({row.row.match for row, _base in paired}),
        "delta": sum(
            official_delta_rbp(row.row, float(getattr(row, probability_attr)))
            - official_delta_rbp(base.row, float(getattr(base, probability_attr)))
            for row, base in paired
        ),
    }


def bootstrap_delta(
    candidate_rows: list[ReplayedRow],
    baseline_rows: list[ReplayedRow],
    *,
    probability_attr: str,
    trials: int,
    seed: int,
) -> dict[str, float]:
    baseline = {_key(row): row for row in baseline_rows}
    by_match: dict[str, list[tuple[ReplayedRow, ReplayedRow]]] = defaultdict(list)
    for row in candidate_rows:
        base = baseline.get(_key(row))
        if base is not None:
            by_match[row.row.match].append((row, base))
    matches = sorted(by_match)
    if not matches:
        return {"n": 0, "matches": 0, "point": 0.0, "ci_2.5": 0.0, "ci_50": 0.0, "ci_97.5": 0.0}
    point = sum(
        official_delta_rbp(row.row, float(getattr(row, probability_attr)))
        - official_delta_rbp(base.row, float(getattr(base, probability_attr)))
        for pairs in by_match.values()
        for row, base in pairs
    )
    rng = random.Random(seed)
    draws = []
    for _ in range(trials):
        total = 0.0
        for match in (rng.choice(matches) for _ in matches):
            for row, base in by_match[match]:
                total += official_delta_rbp(row.row, float(getattr(row, probability_attr)))
                total -= official_delta_rbp(base.row, float(getattr(base, probability_attr)))
        draws.append(total)
    draws.sort()
    return {
        "n": sum(len(pairs) for pairs in by_match.values()),
        "matches": len(matches),
        "point": point,
        "ci_2.5": draws[int(0.025 * (trials - 1))],
        "ci_50": draws[int(0.500 * (trials - 1))],
        "ci_97.5": draws[int(0.975 * (trials - 1))],
    }


def decision(bootstraps: dict[str, dict[str, dict[str, float]]]) -> str:
    manual = bootstraps["manual_probability"]
    best_name, best = max(
        ((name, row) for name, row in manual.items() if name != BASELINE),
        key=lambda item: item[1]["point"],
        default=(BASELINE, {"point": 0.0, "ci_2.5": 0.0, "n": 0, "matches": 0}),
    )
    raw = bootstraps["model_probability"].get(best_name, {"point": 0.0})
    if (
        best["point"] > 0
        and best["ci_2.5"] > 0
        and best["n"] >= MIN_PROMOTION_ROWS
        and best["matches"] >= MIN_PROMOTION_MATCHES
        and raw["point"] >= 0
    ):
        return f"promote_{best_name}_for_settled_total_count_thresholds"
    return "not_promoted_keep_current_independent_total_count_shape"


def _md_table(headers: list[str], rows: list[list[str]]) -> list[str]:
    out = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    out.extend("| " + " | ".join(row) + " |" for row in rows)
    return out


def render(
    rows: list[HistoryMarket],
    contexts: dict[str, MatchContext],
    missing_contexts: list[str],
    alphas: dict[str, TotalAlpha],
    by_candidate: dict[str, list[ReplayedRow]],
    skipped: dict[str, list[str]],
    *,
    now: datetime,
    trials: int,
    seed: int,
) -> str:
    baseline = by_candidate.get(BASELINE, [])
    summaries = {
        attr: {name: summarize(items, probability_attr=attr) for name, items in by_candidate.items()}
        for attr in ("model_probability", "manual_probability")
    }
    bootstraps = {
        attr: {
            name: bootstrap_delta(items, baseline, probability_attr=attr, trials=trials, seed=seed)
            for name, items in by_candidate.items()
        }
        for attr in ("model_probability", "manual_probability")
    }
    final_decision = decision(bootstraps)
    lines = [
        "# Count Total Dependence Manual-Policy Replay",
        "",
        "Read-only analysis. `/predictions` was not read and no submissions were made.",
        "",
        f"- run_utc: {now.astimezone(timezone.utc).isoformat(timespec='seconds')}",
        f"- decision: {final_decision}",
        f"- settled_total_count_rows_requested: {len(rows)}",
        f"- matched_match_contexts: {len(contexts)}",
        f"- missing_match_contexts: {len(missing_contexts)}",
        "- odds_source: ESPN/DraftKings historical pickcenter",
        "- model: current CountPricer means; direct-total NB uses Football-Data total residual dispersion",
        "- policy: live `manual_decision()` with each historical row's displayed field average",
        f"- bootstrap: match-level, trials={trials}, seed={seed}, compared to `{BASELINE}`",
        "- limitations: historical Polymarket blends, old intraday odds snapshots, and old referee overrides are not reconstructed",
        "",
        "## Total Dispersion Inputs",
        "",
    ]
    lines += _md_table(
        ["stat", "alpha_total", "source_rows", "mean_total"],
        [
            [stat, f"{alpha.alpha_total:.6f}", str(alpha.source_rows), f"{alpha.mean_total:.3f}"]
            for stat, alpha in sorted(alphas.items())
        ],
    )
    for attr, title in (
        ("model_probability", "Raw Model Replay"),
        ("manual_probability", "Manual-Policy Replay"),
    ):
        lines += ["", f"## {title}", ""]
        lines += _md_table(
            ["candidate", "n", "direct_rows", "brier", "delta_vs_submitted", "delta_vs_current", "mean_p"],
            [
                [
                    name,
                    str(int(summary["n"])),
                    str(int(summary["direct_rows"])),
                    f"{summary['brier']:.4f}",
                    f"{summary['delta_vs_submitted']:.2f}",
                    f"{paired_delta(items, baseline, probability_attr=attr)['delta']:.2f}",
                    f"{summary['mean_p']:.3f}",
                ]
                for name, items in sorted(
                    by_candidate.items(),
                    key=lambda item: summaries[attr][item[0]]["delta_vs_submitted"],
                    reverse=True,
                )
                for summary in [summaries[attr][name]]
            ],
        )
        lines += ["", f"## {title} Bootstrap Vs Current", ""]
        lines += _md_table(
            ["candidate", "paired_n", "matches", "point_delta", "ci_2.5", "ci_50", "ci_97.5"],
            [
                [
                    name,
                    str(int(row["n"])),
                    str(int(row["matches"])),
                    f"{row['point']:.2f}",
                    f"{row['ci_2.5']:.2f}",
                    f"{row['ci_50']:.2f}",
                    f"{row['ci_97.5']:.2f}",
                ]
                for name, row in bootstraps[attr].items()
            ],
        )
    lines += ["", "## Policy Mix", ""]
    mix_rows = []
    for candidate, items in by_candidate.items():
        counts = defaultdict(int)
        for item in items:
            counts[item.policy] += 1
        for policy, n in sorted(counts.items()):
            mix_rows.append([candidate, policy, str(n)])
    lines += _md_table(["candidate", "policy", "rows"], mix_rows)
    if missing_contexts:
        lines += ["", "## Missing Match Contexts", "", *[f"- {item}" for item in missing_contexts[:25]]]
    skipped_rows = sorted({item for values in skipped.values() for item in values})
    if skipped_rows:
        lines += ["", "## Skipped Rows", "", *[f"- {item}" for item in skipped_rows[:25]]]
    lines += [
        "",
        "## Interpretation",
        "",
        f"- Promotion gate: manual-policy point delta and 2.5% match-bootstrap lower bound must be positive with at least {MIN_PROMOTION_ROWS} paired rows and {MIN_PROMOTION_MATCHES} matches; raw model delta must not be negative.",
        "- This replay is stronger than the club-history proxy because it scores settled Cup rows, but it still lacks old intraday Polymarket/referee context.",
        "",
    ]
    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sports-predict", action="store_true", help="GET-only settlement for older V1 history rows")
    ap.add_argument("--start", default="2026-06-11", help="first ESPN scoreboard date, YYYY-MM-DD")
    ap.add_argument("--end", default="2026-06-28", help="last ESPN scoreboard date, YYYY-MM-DD")
    ap.add_argument("--trials", type=int, default=1000, help="match-bootstrap trials")
    ap.add_argument("--seed", type=int, default=20260629)
    ap.add_argument("--write", action="store_true", help=f"write {OUT}")
    args = ap.parse_args()

    rows = total_count_rows(load_count_rows(sports_predict=args.sports_predict))
    contexts, missing = build_contexts(
        [row.match for row in rows],
        start=date.fromisoformat(args.start),
        end=date.fromisoformat(args.end),
    )
    alphas = estimate_total_alphas()
    by_candidate: dict[str, list[ReplayedRow]] = {}
    skipped: dict[str, list[str]] = {}
    for candidate in CANDIDATES:
        replayed, candidate_skipped = replay_rows(rows, contexts, alphas, candidate=candidate)
        by_candidate[candidate] = replayed
        skipped[candidate] = candidate_skipped

    text = render(
        rows,
        contexts,
        missing,
        alphas,
        by_candidate,
        skipped,
        now=datetime.now(timezone.utc),
        trials=args.trials,
        seed=args.seed,
    )
    print(text)
    if args.write:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(text, encoding="utf-8")
        print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
