"""Rebuild every results figure (light and dark) and the tables behind them.

    pip install matplotlib   # or: make figures
    python results/make_figures.py

Inputs are small committed files: the platform's settled-results export
(results/data/settled_markets_bot*.csv), the boards (examples/boards.json), the
agent's run log (agent/logs/runs.log) and two tables derived once from local
files that are too big to commit (the June 13 club backtest and the full
submission log); those are refreshed here when the local files exist.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.patches import PathPatch  # noqa: E402
from matplotlib.path import Path as MPath  # noqa: E402

from probcup.questions.parser import parse  # noqa: E402
from probcup.questions.teams import ALL_TEAM_NAMES  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "results" / "data"
FIGS = ROOT / "results" / "figures"

THEMES = {
    "light": dict(surface="#fcfcfb", ink="#0b0b0b", ink2="#52514e", muted="#898781",
                  grid="#e1e0d9", axis="#c3c2b7", s1="#2a78d6", s2="#eb6834"),
    "dark": dict(surface="#1a1a19", ink="#f0efec", ink2="#c3c2b7", muted="#898781",
                 grid="#2c2c2a", axis="#383835", s1="#3987e5", s2="#d95926"),
}
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "svg.fonttype": "path"})
LINE = 1.5          # 2px at 96 dpi
BAR_PX = 16         # bar thickness (<= 24px)
CAP_PX = 4          # rounded data-end radius


# --------------------------------------------------------------------------- #
# tables

def settled(bot: int) -> pd.DataFrame:
    return pd.read_csv(DATA / f"settled_markets_bot{bot}.csv")


def match_order() -> pd.DataFrame:
    """Matches in the order they were played, with their stage."""
    boards = json.loads((ROOT / "examples" / "boards.json").read_text())
    first = pd.concat([settled(1), settled(2)]).groupby("match_id")["created_date"].min()
    stage = {}
    for b in boards:
        qs = " ".join(m["question"] for m in b["markets"])
        stage[b["match_id"]] = ("Final" if b["match_name"] == "ESP vs ARG"
                                else "Knockout" if "in regulation" in qs else "Group")
    out = first.rename("first_created").reset_index().sort_values("first_created")
    out["stage"] = out["match_id"].map(stage)
    # the two boards missing from examples/ (three teams mentioned) are group-stage boards
    out["stage"] = out["stage"].fillna("Group")
    out["n"] = np.arange(1, len(out) + 1)
    return out


CATEGORIES = [  # (label, test on the parsed spec)
    ("Player shots on target", lambda s: s.family in ("player_sot_ge", "player_more_sot_than")),
    ("Team & total shots on target", lambda s: s.stat == "sot" or s.family in
     ("any_player_sot_ge", "team_any_player_sot_ge")),
    ("Cards", lambda s: s.stat == "cards" or s.family in
     ("red_card", "cards_gt_goals", "card_each_half", "first_card_before_goal")),
    ("Corners", lambda s: s.stat == "corners"),
    ("Offsides", lambda s: s.stat == "offsides"),
    ("Fouls", lambda s: s.stat == "fouls"),
    ("Timing windows", lambda s: s.family == "timing_event"),
    ("Total shots", lambda s: s.family in ("total_shots_ge", "team_distinct_shooters_ge")),
    ("Goal scorers", lambda s: s.family in (
        "player_scores", "player_scores_or_assists", "substitute_scores", "any_player_braces",
        "first_goal_not_players", "first_goal_single_digit", "own_goal", "header_goal",
        "outside_box_goal", "first_goal_assisted")),
    ("Penalties", lambda s: s.family in ("penalty_awarded", "penalty_or_red", "penalty_scored")),
    ("Other", lambda s: s.family in (
        "substitution_before_ht", "substitution_at_ht", "team_first_substitution", "var_review",
        "plays_full_match", "gk_saves_ge", "total_subs_ge")),
]
GOALS_LABEL = "Goals, results & halftime"


def category(question: str) -> str:
    spec = parse(question, teams=ALL_TEAM_NAMES)
    for label, test in CATEGORIES:
        if test(spec):
            return label
    return GOALS_LABEL


def points_by_category() -> pd.DataFrame:
    rows = []
    for bot in (1, 2):
        d = settled(bot)
        d["category"] = d["question"].map(category)
        g = d.groupby("category").agg(n=("rbp", "size"), points=("rbp", "sum"),
                                      beat_crowd=("outcome_tag", lambda t: (t == "beat_crowd").mean()))
        rows.append(g.add_prefix(f"bot{bot}_"))
    out = pd.concat(rows, axis=1).sort_values("bot1_points", ascending=False).round(3)
    out.to_csv(DATA / "points_by_category.csv")
    return out


def deviation_table() -> pd.DataFrame:
    d = settled(1)
    d["dev"] = d["probability_submitted"] - d["crowd_value"]
    bins = [-100, -15, -5, 5, 15, 100]
    labels = ["15+ below", "5–15 below", "within 5", "5–15 above", "15+ above"]
    d["bin"] = pd.cut(d["dev"], bins=bins, labels=labels, right=False)
    out = d.groupby("bin", observed=False).agg(n=("rbp", "size"), points=("rbp", "sum"),
                                               per_market=("rbp", "mean")).round(2)
    out.to_csv(DATA / "points_by_deviation.csv")
    return out


def calibration_tables() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Club backtest (June 13, 782k predictions) and the live tournament (bot 1)."""
    path = DATA / "backtest_calibration.csv"
    local = ROOT / "data" / "cache" / "backtest_results.parquet"
    if local.exists():
        r = pd.read_parquet(local).dropna(subset=["p"])
        r["bucket"] = (r["p"] * 20).clip(0, 19).astype(int)
        bt = r.groupby("bucket").agg(n=("outcome", "size"), predicted=("p", "mean"),
                                    observed=("outcome", "mean")).round(4)
        bt.to_csv(path)
    bt = pd.read_csv(path)
    d = settled(1)
    d["p"] = d["probability_submitted"] / 100
    d["o"] = (d["result_value"] == 100).astype(int)
    d["bucket"] = (d["p"] * 10).clip(0, 9).astype(int)
    live = d.groupby("bucket").agg(n=("o", "size"), predicted=("p", "mean"),
                                   observed=("o", "mean")).round(4).reset_index()
    live.to_csv(DATA / "live_calibration_bot1.csv", index=False)
    return bt, live


def activity_table() -> pd.DataFrame:
    path = DATA / "agent_activity.csv"
    runs = []
    for line in (ROOT / "agent" / "logs" / "runs.log").read_text().splitlines():
        m = re.match(r"\[(\d{4}-\d{2}-\d{2})T[^\]]+\] daily_run hours=\S+ mode=(\S+)", line)
        if m:
            runs.append((m.group(1), m.group(2)))
    r = pd.DataFrame(runs, columns=["date", "mode"])
    out = (r.assign(submit=r["mode"] == "SUBMIT", preview=r["mode"] == "dry-run")
           .groupby("date")[["submit", "preview"]].sum().astype(int))
    local = ROOT / "data" / "logs" / "submissions.csv"
    if local.exists():
        lines = [ln.split(",") for ln in local.read_text().splitlines()]
        rows = [(f[0][:10], f[-2] if len(f) == 6 else f[3]) for f in lines
                if f[0] != "submitted_at" and len(f) >= 5]
        s = pd.DataFrame(rows, columns=["date", "action"])
        acts = s.pivot_table(index="date", columns="action", aggfunc="size", fill_value=0)
        out = out.join(acts.add_prefix("actions_"), how="outer").fillna(0).astype(int)
    elif path.exists():
        prev = pd.read_csv(path, index_col="date")
        out = out.join(prev.filter(like="actions_"), how="outer").fillna(0).astype(int)
    days = pd.date_range(out.index.min(), out.index.max()).strftime("%Y-%m-%d")
    out = out.reindex(days, fill_value=0)
    out.index.name = "date"
    out.to_csv(path)
    return out


# --------------------------------------------------------------------------- #
# drawing helpers

def _frame(ax, t, grid_axis="y"):
    ax.set_facecolor(t["surface"])
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(t["axis"])
    ax.spines["bottom"].set_linewidth(0.75)
    ax.tick_params(colors=t["muted"], labelcolor=t["ink2"], length=0, labelsize=9)
    ax.grid(axis=grid_axis, color=t["grid"], linewidth=0.75)
    ax.set_axisbelow(True)


def _figure(t, w=9.0, h=4.4):
    fig, ax = plt.subplots(figsize=(w, h), dpi=96)
    fig.patch.set_facecolor(t["surface"])
    return fig, ax


def _title(fig, t, title, subtitle):
    fig.text(0.012, 0.965, title, color=t["ink"], fontsize=13, fontweight="bold", va="top")
    fig.text(0.012, 0.905, subtitle, color=t["ink2"], fontsize=9.5, va="top")


def _px(ax, axis: str) -> float:
    """Data units per pixel along an axis (after limits and layout are final)."""
    bbox = ax.get_window_extent()
    lo, hi = ax.get_xlim() if axis == "x" else ax.get_ylim()
    return abs(hi - lo) / (bbox.width if axis == "x" else bbox.height)


def _bar(ax, base, end, center, thick, color, horizontal=True):
    """A bar square at the baseline with a 4px rounded data-end."""
    if end == base:
        return
    rx = CAP_PX * _px(ax, "x" if horizontal else "y")   # along the bar
    ry = CAP_PX * _px(ax, "y" if horizontal else "x")   # across the bar
    sign = 1 if end > base else -1
    rx = min(rx, abs(end - base))
    ry = min(ry, thick / 2)
    k = 0.5523
    a, b = center - thick / 2, center + thick / 2
    e = end - sign * rx
    verts = [(base, a), (e, a),
             (e + sign * k * rx, a), (end, b - (thick - ry) - ry * (1 - k)), (end, a + ry),
             (end, b - ry),
             (end, b - ry * (1 - k)), (e + sign * k * rx, b), (e, b),
             (base, b), (base, a)]
    codes = [MPath.MOVETO, MPath.LINETO, MPath.CURVE4, MPath.CURVE4, MPath.CURVE4,
             MPath.LINETO, MPath.CURVE4, MPath.CURVE4, MPath.CURVE4,
             MPath.LINETO, MPath.CLOSEPOLY]
    if not horizontal:
        verts = [(y, x) for x, y in verts]
    ax.add_patch(PathPatch(MPath(verts, codes), facecolor=color, edgecolor="none", zorder=3))


def _save(fig, name, mode):
    FIGS.mkdir(parents=True, exist_ok=True)
    fig.savefig(FIGS / f"{name}-{mode}.svg", facecolor=fig.get_facecolor())
    plt.close(fig)


# --------------------------------------------------------------------------- #
# figures

def fig_cumulative(mode):
    t = THEMES[mode]
    order = match_order()
    fig, ax = _figure(t)
    fig.subplots_adjust(left=0.07, right=0.83, top=0.80, bottom=0.12)
    _frame(ax, t)
    ends = {}
    for bot, color, name in ((1, t["s1"], "Bot 1 (stable)"), (2, t["s2"], "Bot 2 (A/B bench)")):
        per = settled(bot).groupby("match_id")["rbp"].sum()
        y = order["match_id"].map(per).fillna(0).cumsum()
        ax.plot(order["n"], y, color=color, linewidth=LINE, solid_capstyle="round", label=name, zorder=3)
        ax.plot(order["n"].iloc[-1], y.iloc[-1], "o", color=color, markersize=8,
                markeredgecolor=t["surface"], markeredgewidth=2, zorder=4)
        ends[bot] = y.iloc[-1]
    for bot, dy in ((1, 6), (2, -12)):
        ax.annotate(f"{ends[bot]:+,.0f} · #{17 if bot == 1 else 31}", (order['n'].iloc[-1], ends[bot]),
                    xytext=(10, dy), textcoords="offset points", color=t["ink"], fontsize=9.5, va="center")
    ko = order.loc[order["stage"] != "Group", "n"].min()
    fin = order.loc[order["stage"] == "Final", "n"].min()
    for x in (ko - 0.5, fin - 0.5):
        ax.axvline(x, color=t["axis"], linewidth=0.75, zorder=1)
    top = max(ends.values()) * 1.08
    ax.text(ko / 2, top, "Group stage · 1×", color=t["muted"], ha="center", fontsize=9)
    ax.text((ko + fin) / 2, top, "Knockouts · 2×", color=t["muted"], ha="center", fontsize=9)
    ax.text(fin + 0.3, top, "Final · 3×", color=t["muted"], ha="left", fontsize=9)
    ax.set_xlim(0.5, order["n"].iloc[-1] + 0.5)
    ax.set_ylim(min(0, ax.get_ylim()[0]), top * 1.06)
    ax.set_xlabel("Matches, in the order they were played", color=t["ink2"], fontsize=9)
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))
    ax.legend(loc="upper left", frameon=False, labelcolor=t["ink2"], fontsize=9)
    _title(fig, t, "Points against the crowd, accumulated over the tournament",
           "Stage-weighted points per settled market (platform export) · rank of 4,013 at right "
           "· bot 2 missed its first ~15 matches")
    _save(fig, "cumulative_points", mode)


def fig_categories(mode):
    t = THEMES[mode]
    tab = pd.read_csv(DATA / "points_by_category.csv", index_col=0).sort_values("bot1_points")
    fig, ax = _figure(t, h=4.8)
    fig.subplots_adjust(left=0.30, right=0.93, top=0.82, bottom=0.10)
    _frame(ax, t, grid_axis="x")
    ys = np.arange(len(tab))
    lo, hi = min(0, tab.bot1_points.min()), tab.bot1_points.max()
    pad = (hi - lo) * 0.14
    ax.set_xlim(lo - (pad if lo < 0 else 0), hi + pad)
    ax.set_ylim(-0.7, len(tab) - 0.3)
    ax.set_yticks(ys, [f"{c}  ({int(n)})" for c, n in zip(tab.index, tab.bot1_n)])
    ax.axvline(0, color=t["axis"], linewidth=0.75, zorder=2)
    fig.canvas.draw()
    thick = BAR_PX * _px(ax, "y")
    for y, v in zip(ys, tab.bot1_points):
        _bar(ax, 0, v, y, thick, t["s1"], horizontal=True)
        ax.annotate(f"{v:+,.0f}", (v, y), xytext=(5 if v >= 0 else -5, 0), textcoords="offset points",
                    ha="left" if v >= 0 else "right", va="center", color=t["ink2"], fontsize=8.5)
    ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))
    _title(fig, t, "Where bot 1's points came from",
           "Total points against the crowd by market type (number of settled markets in brackets)")
    _save(fig, "points_by_category", mode)


def fig_deviation(mode):
    t = THEMES[mode]
    tab = pd.read_csv(DATA / "points_by_deviation.csv")
    fig, ax = _figure(t, h=4.0)
    fig.subplots_adjust(left=0.08, right=0.97, top=0.78, bottom=0.17)
    _frame(ax, t)
    xs = np.arange(len(tab))
    vals = tab["per_market"].to_numpy()
    lo, hi = min(0, vals.min()), max(0, vals.max())
    ax.set_ylim(lo - (hi - lo) * 0.12 if lo < 0 else 0, hi + (hi - lo) * 0.16)
    ax.set_xlim(-0.6, len(tab) - 0.4)
    ax.axhline(0, color=t["axis"], linewidth=0.75, zorder=2)
    ax.set_xticks(xs, [f"{b}\n{int(n)} markets" for b, n in zip(tab["bin"], tab["n"])])
    fig.canvas.draw()
    thick = BAR_PX * 1.5 * _px(ax, "x")
    for x, v in zip(xs, vals):
        _bar(ax, 0, v, x, thick, t["s1"], horizontal=False)
        ax.annotate(f"{v:+.1f}", (x, v), xytext=(0, 5 if v >= 0 else -5), textcoords="offset points",
                    ha="center", va="bottom" if v >= 0 else "top", color=t["ink2"], fontsize=9)
    ax.set_xlabel("Bot 1's probability minus the crowd's average (percentage points)",
                  color=t["ink2"], fontsize=9)
    _title(fig, t, "Pricing below the crowd paid most; pricing far above it lost",
           "Average points per settled market, by how far bot 1's forecast sat from the crowd's")
    _save(fig, "points_by_deviation", mode)


def fig_calibration(mode):
    t = THEMES[mode]
    bt = pd.read_csv(DATA / "backtest_calibration.csv")
    live = pd.read_csv(DATA / "live_calibration_bot1.csv")
    live = live[live["n"] >= 20]
    fig, ax = _figure(t, w=6.4, h=6.0)
    fig.subplots_adjust(left=0.12, right=0.96, top=0.84, bottom=0.11)
    _frame(ax, t, grid_axis="both")
    ax.plot([0, 1], [0, 1], color=t["axis"], linewidth=0.75, zorder=1)
    ax.plot(bt.predicted, bt.observed, "-o", color=t["s1"], linewidth=LINE, markersize=6,
            markeredgecolor=t["surface"], markeredgewidth=1.5, zorder=3,
            label=f"Club backtest, {int(bt.n.sum()):,} forecasts")
    ax.plot(live.predicted, live.observed, "-o", color=t["s2"], linewidth=LINE, markersize=8,
            markeredgecolor=t["surface"], markeredgewidth=2, zorder=4,
            label=f"Live tournament (bot 1), {int(live.n.sum()):,} markets")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    pct = matplotlib.ticker.PercentFormatter(1.0, decimals=0)
    ax.xaxis.set_major_formatter(pct)
    ax.yaxis.set_major_formatter(pct)
    ax.set_xlabel("Forecast probability", color=t["ink2"], fontsize=9)
    ax.set_ylabel("How often it happened", color=t["ink2"], fontsize=9)
    ax.legend(loc="upper left", frameon=False, labelcolor=t["ink2"], fontsize=9)
    _title(fig, t, "Calibration: forecasts vs outcomes",
           "Backtest: 5-point bins · tournament: 10-point bins, 20+ markets · diagonal = perfect")
    _save(fig, "calibration", mode)


def fig_activity(mode):
    t = THEMES[mode]
    tab = pd.read_csv(DATA / "agent_activity.csv", parse_dates=["date"])
    fig, ax = _figure(t, h=3.8)
    fig.subplots_adjust(left=0.07, right=0.97, top=0.76, bottom=0.14)
    _frame(ax, t)
    xs = np.arange(len(tab))
    total = (tab.submit + tab.preview).to_numpy()
    ax.set_xlim(-0.7, len(tab) - 0.3)
    ax.set_ylim(0, total.max() * 1.3)
    fig.canvas.draw()
    thick = min(BAR_PX, 0.62 / _px(ax, "x")) * _px(ax, "x")
    gap = 2 * _px(ax, "y")
    for x, s, p in zip(xs, tab.submit, tab.preview):
        if s:
            _bar(ax, 0, s, x, thick, t["s1"], horizontal=False) if not p else \
                ax.add_patch(plt.Rectangle((x - thick / 2, 0), thick, s - gap / 2, color=t["s1"], lw=0, zorder=3))
        if p:
            _bar(ax, s + (gap / 2 if s else 0), s + p, x, thick, t["s2"], horizontal=False)
    ticks = [i for i, d in enumerate(tab.date) if d.weekday() == 0 or i == 0]
    ax.set_xticks(ticks, [tab.date[i].strftime("%b %-d") for i in ticks])
    from matplotlib.patches import Patch
    ax.legend(handles=[Patch(color=t["s1"], label="Submitting runs"),
                       Patch(color=t["s2"], label="Preview (dry) runs")],
              loc="upper left", frameon=False, labelcolor=t["ink2"], fontsize=9, ncol=2)
    busiest = int(np.argmax(total))
    ax.annotate(f"{int(total[busiest])} runs", (busiest, total[busiest]), xytext=(0, 4),
                textcoords="offset points", ha="center", va="bottom", color=t["ink2"], fontsize=8.5)
    _title(fig, t, "The agent's pipeline runs, day by day",
           f"{int(tab.submit.sum())} submitting and {int(tab.preview.sum())} preview runs of the "
           f"production pipeline, June 12 – July 19 (agent/logs/runs.log)")
    _save(fig, "agent_activity", mode)


def main() -> None:
    points_by_category()
    deviation_table()
    calibration_tables()
    activity_table()
    for mode in THEMES:
        fig_cumulative(mode)
        fig_categories(mode)
        fig_deviation(mode)
        fig_calibration(mode)
        fig_activity(mode)
    print(f"wrote {len(list(FIGS.glob('*.svg')))} figures to {FIGS.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
