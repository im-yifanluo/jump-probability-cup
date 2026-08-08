"""Research diagnostics for Probability Cup strategy choices.

This script does not read bot stored predictions or submit anything.  It uses:

* data_reference/rbp_full.csv: settled live-history rows with our probability,
  field average, outcome, and official RBP.
* data_reference/backtest_results.parquet: large offline model-validation rows
  with model probability and realized outcome.

The goal is to separate two questions:

1. Manual/live layer: given a model probability and historical field-average
   context, what transform held up under chronological scoring for diagnostics?
2. Model layer: are the underlying model probabilities calibrated on large
   historical samples, and would simple post-calibration improve them?
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Callable

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

LIVE_PATH = ROOT / "data_reference" / "rbp_full.csv"
CLUB_PATH = ROOT / "data_reference" / "backtest_results.parquet"
REPORT_PATH = ROOT / "data" / "logs" / "strategy_research.md"
HISTORY_PATH = ROOT / "docs" / "history.md"

SPECIAL_FAMILIES = {
    "goal_before_hydration",
    "goal_after_second_hydration",
    "goal_in_stoppage",
    "offside_before_hydration",
    "card_after_second_hydration",
    "substitution_before_halftime",
    "substitute_scores",
    "any_player_goals_ge",
    "any_player_sot_ge",
    "total_count_before_hydration",
}


def brier(p, o):
    return (p - o) ** 2


def clip(p):
    return np.clip(p, 0.01, 0.99)


def _read_only_outcomes_from_sports_predict() -> dict[str, int]:
    from backtest.history import unique_outcomes_by_question
    from manual.anchors import sp_get, sports_predict_key

    key = sports_predict_key()
    if not key:
        raise SystemExit("No SportsPredict key found for --sports-predict")
    events = sp_get("/events?limit=100", key)
    event = next(e for e in events if "probability cup" in e.get("title", "").lower())
    lobby = sp_get(f"/lobbies?event_id={event['id']}", key)[0]
    results = sp_get(f"/results?lobby_id={lobby['id']}", key)
    return unique_outcomes_by_question(results)


def live_df(
    path: Path = LIVE_PATH,
    *,
    history_path: Path | None = None,
    sports_predict: bool = False,
) -> pd.DataFrame:
    if history_path is not None:
        from backtest.history import exact_rows, load_history, settle_v1_rows

        rows = load_history(history_path)
        if sports_predict:
            rows = settle_v1_rows(rows, _read_only_outcomes_from_sports_predict())
        records = []
        for row in exact_rows(rows):
            records.append(
                {
                    "ver": row.version,
                    "order": row.order,
                    "match": row.match,
                    "question": row.question,
                    "you": row.you,
                    "crowd": row.crowd,
                    "outcome": row.outcome,
                    "rbp": row.official_rbp,
                    "family": row.family,
                    "stat": row.stat,
                    "period": row.period,
                }
            )
        df = pd.DataFrame(records)
    else:
        df = pd.read_csv(path)
    df["stat"] = df["stat"].fillna("")
    df["period"] = df["period"].fillna("match")
    df["famkey"] = df["family"] + np.where(df["stat"].ne(""), "/" + df["stat"], "")
    df["famkey"] += np.where(df["period"].ne("match"), "/" + df["period"], "")
    df["category"] = df.apply(live_category, axis=1)
    return df


def live_category(row) -> str:
    family = row["family"]
    stat = row["stat"]
    if family.startswith("player_"):
        return "player_props"
    if family in SPECIAL_FAMILIES or stat == "shots":
        return "special_markets"
    if family in {"penalty_or_red", "penalty_awarded", "red_card"}:
        return "event_base_rates"
    if stat == "goals" or family in {
        "team_win",
        "match_drawn",
        "ht_tied",
        "ht_team_winning",
        "team_first_goal_game",
        "team_first_goal_2h",
        "team_leads_any_time",
        "team_more_goals_2h",
        "team_scores_in_half",
        "team_goals_ge",
        "total_goals",
        "btts",
        "btts_and_total_ge",
        "goal_each_half",
        "penalty_shootout",
    }:
        return "goal_markets"
    if family == "team_more_count_than":
        return "count_h2h"
    if family in {"team_count_ge", "total_count_ge", "both_teams_count_ge", "total_cards_more_than_goals"}:
        return "count_thresholds"
    return "other"


Strategy = Callable[[pd.DataFrame], np.ndarray]


def live_score(df: pd.DataFrame, p: np.ndarray) -> dict[str, float]:
    p = clip(np.asarray(p, dtype=float))
    delta = np.sum(100 * (brier(df["you"].to_numpy(), df["outcome"].to_numpy()) - brier(p, df["outcome"].to_numpy())))
    rbp = df["rbp"].sum() + delta
    return {
        "n": float(len(df)),
        "rbp": float(rbp),
        "delta": float(delta),
        "brier": float(np.mean(brier(p, df["outcome"].to_numpy()))),
    }


def p_current(df):
    return df["you"].to_numpy()


def p_crowd(df):
    return df["crowd"].to_numpy()


def p_flat50(df):
    return np.full(len(df), 0.50)


def p_shrink(k: float) -> Strategy:
    def _p(df):
        return df["crowd"].to_numpy() + k * (df["you"].to_numpy() - df["crowd"].to_numpy())

    return _p


def p_asym(above_k: float, below_k: float, cap: float | None = None) -> Strategy:
    def _p(df):
        d = df["you"].to_numpy() - df["crowd"].to_numpy()
        k = np.where(d > 0, above_k, below_k)
        adj = k * d
        if cap is not None:
            adj = np.clip(adj, -cap, cap)
        return df["crowd"].to_numpy() + adj

    return _p


def live_fixed_policy_table(df: pd.DataFrame) -> pd.DataFrame:
    specs: list[tuple[str, Strategy]] = [
        ("current", p_current),
        ("crowd", p_crowd),
        ("flat50", p_flat50),
        ("shrink25", p_shrink(0.25)),
        ("shrink50", p_shrink(0.50)),
        ("shrink75", p_shrink(0.75)),
        ("asym0_075", p_asym(0.0, 0.75)),
        ("asym0_075_cap12", p_asym(0.0, 0.75, 0.12)),
        ("asym0_100_cap15", p_asym(0.0, 1.0, 0.15)),
        ("below125_cap15", p_asym(0.0, 1.25, 0.15)),
        ("expand125", p_shrink(1.25)),
    ]
    rows = []
    for name, strat in specs:
        s = live_score(df, strat(df))
        rows.append({"strategy": name, **s})
    out = pd.DataFrame(rows)
    return out.sort_values("delta", ascending=False)


def category_diagnostics(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for category, part in df.groupby("category"):
        rows.append(
            {
                "category": category,
                "n": len(part),
                "rbp": part["rbp"].sum(),
                "skill_vs_field": np.sum(
                    100 * (brier(part["crowd"], part["outcome"]) - brier(part["you"], part["outcome"]))
                ),
                "brier_you": np.mean(brier(part["you"], part["outcome"])),
                "brier_field": np.mean(brier(part["crowd"], part["outcome"])),
                "you": part["you"].mean(),
                "field": part["crowd"].mean(),
                "hit": part["outcome"].mean(),
                "dev": (part["you"] - part["crowd"]).mean(),
            }
        )
    return pd.DataFrame(rows).sort_values("skill_vs_field", ascending=False)


def family_diagnostics(df: pd.DataFrame, min_n: int = 5) -> pd.DataFrame:
    rows = []
    for fam, part in df.groupby("famkey"):
        if len(part) < min_n:
            continue
        rows.append(
            {
                "famkey": fam,
                "category": part["category"].iloc[0],
                "n": len(part),
                "rbp": part["rbp"].sum(),
                "skill_vs_field": np.sum(
                    100 * (brier(part["crowd"], part["outcome"]) - brier(part["you"], part["outcome"]))
                ),
                "brier_you": np.mean(brier(part["you"], part["outcome"])),
                "brier_field": np.mean(brier(part["crowd"], part["outcome"])),
                "you": part["you"].mean(),
                "field": part["crowd"].mean(),
                "hit": part["outcome"].mean(),
                "dev": (part["you"] - part["crowd"]).mean(),
            }
        )
    return pd.DataFrame(rows).sort_values("skill_vs_field", ascending=False)


def policy_grid() -> list[tuple[str, Strategy]]:
    out = []
    for k in [0.0, 0.25, 0.5, 0.75, 1.0, 1.25]:
        out.append((f"shrink{k:g}", p_shrink(k)))
    for above in [0.0, 0.25, 0.5, 0.75, 1.0]:
        for below in [0.5, 0.75, 1.0, 1.25]:
            for cap in [0.10, 0.12, 0.15, None]:
                out.append((f"a{above:g}_b{below:g}_cap{cap or 'none'}", p_asym(above, below, cap)))
    return out


def choose_best(train: pd.DataFrame, specs: list[tuple[str, Strategy]]) -> tuple[str, Strategy]:
    best_name, best_strat, best_delta = "current", p_current, -10**9
    for name, strat in specs:
        delta = live_score(train, strat(train))["delta"]
        if delta > best_delta:
            best_name, best_strat, best_delta = name, strat, delta
    return best_name, best_strat


def live_walk_forward(df: pd.DataFrame, min_train: int = 120, min_category_n: int = 35) -> pd.DataFrame:
    specs = policy_grid()
    scored = []
    choices = Counter()
    orders = sorted(df["order"].unique())
    for order in orders:
        train = df[df["order"] < order]
        test = df[df["order"] == order]
        if len(train) < min_train:
            for _, row in test.iterrows():
                scored.append((row.name, row["you"], "current"))
            choices["current"] += len(test)
            continue

        global_name, global_strat = choose_best(train, specs)
        for idx, row in test.iterrows():
            cat_train = train[train["category"] == row["category"]]
            if len(cat_train) >= min_category_n:
                name, strat = choose_best(cat_train, specs)
            else:
                name, strat = global_name, global_strat
            p = float(strat(pd.DataFrame([row]))[0])
            scored.append((idx, p, name))
            choices[name] += 1
    pred = pd.Series({idx: p for idx, p, _name in scored}).reindex(df.index).to_numpy()
    out = live_score(df, pred)
    out["selected"] = ", ".join(f"{k}:{v}" for k, v in choices.most_common(8))
    return pd.DataFrame([out])


def club_backtest(path: Path = CLUB_PATH) -> tuple[pd.DataFrame, pd.DataFrame]:
    df = pd.read_parquet(path)
    fam_rows = []
    for family, part in df.groupby("family"):
        base = part["outcome"].mean()
        fam_rows.append(
            {
                "family": family,
                "n": len(part),
                "base": base,
                "p_mean": part["p"].mean(),
                "hit": base,
                "brier_model": np.mean(brier(part["p"], part["outcome"])),
                "brier_50": np.mean(brier(0.5, part["outcome"])),
                "brier_base": np.mean(brier(base, part["outcome"])),
                "cal_gap": part["p"].mean() - base,
            }
        )
    fam = pd.DataFrame(fam_rows)
    fam["edge_vs_50"] = fam["brier_50"] - fam["brier_model"]
    fam["edge_vs_base"] = fam["brier_base"] - fam["brier_model"]

    seasons = sorted(df["season"].unique())
    grid = [0.0, 0.25, 0.5, 0.75, 1.0, 1.10, 1.25, 1.5]
    oof = []
    choices = Counter()
    for season in seasons:
        train = df[df["season"] < season]
        test = df[df["season"] == season]
        if train.empty:
            oof.append(test.assign(p_cal=test["p"], cal_choice="current"))
            choices["current"] += len(test)
            continue
        calibrated = []
        for family, test_part in test.groupby("family"):
            train_part = train[train["family"] == family]
            if len(train_part) < 500:
                calibrated.append(test_part.assign(p_cal=test_part["p"], cal_choice="current"))
                choices["current"] += len(test_part)
                continue
            base = train_part["outcome"].mean()
            best_k, best_b = 1.0, 10**9
            for k in grid:
                p = clip(base + k * (train_part["p"] - base))
                br = np.mean(brier(p, train_part["outcome"]))
                if br < best_b:
                    best_k, best_b = k, br
            p_test = clip(base + best_k * (test_part["p"] - base))
            calibrated.append(test_part.assign(p_cal=p_test, cal_choice=f"k={best_k:g}"))
            choices[f"{family}:k={best_k:g}"] += len(test_part)
        oof.append(pd.concat(calibrated, ignore_index=True))
    oof_df = pd.concat(oof, ignore_index=True)
    club_summary = pd.DataFrame(
        [
            {
                "n": len(oof_df),
                "brier_model_oof": np.mean(brier(oof_df["p"], oof_df["outcome"])),
                "brier_calibrated_oof": np.mean(brier(oof_df["p_cal"], oof_df["outcome"])),
                "delta_brier": np.mean(brier(oof_df["p"], oof_df["outcome"]))
                - np.mean(brier(oof_df["p_cal"], oof_df["outcome"])),
                "choices": ", ".join(f"{k}:{v}" for k, v in choices.most_common(10)),
            }
        ]
    )
    return fam.sort_values("edge_vs_base", ascending=False), club_summary


def md_table(df: pd.DataFrame, max_rows: int = 20) -> str:
    show = df.head(max_rows).copy()
    for col in show.columns:
        if pd.api.types.is_float_dtype(show[col]):
            show[col] = show[col].map(lambda x: f"{x:.4f}" if abs(x) < 1 else f"{x:.2f}")
    return show.to_markdown(index=False)


def build_report(*, use_history: bool, sports_predict: bool) -> str:
    live = live_df(
        history_path=HISTORY_PATH if use_history else None,
        sports_predict=sports_predict,
    )
    fixed = live_fixed_policy_table(live)
    cat = category_diagnostics(live)
    fam = family_diagnostics(live)
    wf = live_walk_forward(live)
    club_fam, club_summary = club_backtest()

    lines = [
        "# Strategy Research Diagnostics",
        "",
        "Generated by `scripts/strategy_research.py`. This is read-only analysis; it does not use bot stored predictions or submit anything.",
        f"Live source: {'docs/history.md' if use_history else 'data_reference/rbp_full.csv'}"
        + (" plus read-only SportsPredict /results settlement." if sports_predict else "."),
        "",
        "## Live Manual Layer",
        "",
        f"Rows: {len(live)} settled markets across {live['match'].nunique()} matches.",
        "",
        "### Fixed Field-Relative Policies",
        md_table(fixed, 20),
        "",
        "### One-Match-Ahead Category-Aware Policy Selection",
        md_table(wf, 5),
        "",
        "### Category Diagnostics",
        md_table(cat, 20),
        "",
        "### Family Diagnostics",
        md_table(fam, 30),
        "",
        "## Offline Model Layer",
        "",
        "Large club-data validation of raw model probabilities; this validates model families independent of live field behavior.",
        "",
        "### Cross-Season Simple Post-Calibration",
        md_table(club_summary, 5),
        "",
        "### Family Model Edges",
        md_table(club_fam, 30),
        "",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write", action="store_true", help=f"write {REPORT_PATH}")
    ap.add_argument("--history", action="store_true", help="use docs/history.md instead of data_reference/rbp_full.csv")
    ap.add_argument("--sports-predict", action="store_true", help="GET-only /results read to settle older history rows")
    args = ap.parse_args()
    report = build_report(use_history=args.history, sports_predict=args.sports_predict)
    print(report)
    if args.write:
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(report)
        print(f"wrote {REPORT_PATH}")


if __name__ == "__main__":
    main()
