"""Build the report's figures, tables and numbers from the committed results.

    make paper                      # assets, then the PDF
    python paper/build_assets.py    # assets only

Inputs are small committed files: the platform's settled-results export
(results/data/settled_markets_bot*.csv) and the tables derived from it, the
fitted count models (data/models/count_models.json) and the agent's activity
table. Outputs are vector figures in paper/figures/ and LaTeX macros and table
rows in paper/generated/, so every number in the report is computed, not typed.
Re-running on the same inputs reproduces the same files byte for byte.
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "results"))
import make_figures as mf  # noqa: E402  shared loaders: settled(), match_order(), category()

FIGS = ROOT / "paper" / "figures"
GEN = ROOT / "paper" / "generated"
DATA = ROOT / "results" / "data"
WEIGHT = {"Group": 1, "Knockout": 2, "Final": 3}
N_BOOT, SEED = 4000, 7
MIN_CATEGORY_N = 25      # smaller categories are left out of the bias panel
TEXT_W = 6.5             # \textwidth in inches (US letter, 1 in margins)

# Print style: Computer Modern to match the text; sizes are final (figures are
# included at 100%). Two series colours, validated earlier for colour-blind
# separation; everything else is ink or grey.
C = dict(ink="#0b0b0b", ink2="#52514e", muted="#898781", grid="#e8e7e1", axis="#b9b8ae",
         s1="#2a78d6", s2="#eb6834", s3="#b9b8ae")
plt.rcParams.update({
    "font.family": "serif", "font.serif": ["cmr10"], "mathtext.fontset": "cm",
    "axes.formatter.use_mathtext": True, "axes.unicode_minus": False,
    "font.size": 8.5, "axes.labelsize": 8.5, "axes.titlesize": 8.5, "xtick.labelsize": 8,
    "ytick.labelsize": 8, "legend.fontsize": 8, "pdf.fonttype": 42,
    "axes.edgecolor": C["axis"], "axes.linewidth": 0.6, "axes.labelcolor": C["ink2"],
    "text.color": C["ink"], "xtick.color": C["ink2"], "ytick.color": C["ink2"],
    "xtick.major.size": 0, "ytick.major.size": 0, "xtick.major.pad": 3, "ytick.major.pad": 3,
    "axes.spines.top": False, "axes.spines.right": False, "axes.spines.left": False,
    "axes.grid": True, "grid.color": C["grid"], "grid.linewidth": 0.5, "axes.axisbelow": True,
    "legend.frameon": False, "legend.handlelength": 1.2, "legend.borderaxespad": 0.2,
    "lines.solid_capstyle": "round", "figure.constrained_layout.h_pad": 0.02,
    "figure.constrained_layout.w_pad": 0.02,
})
logging.getLogger("fontTools").setLevel(logging.ERROR)  # bundled CM fonts have odd header dates


# --------------------------------------------------------------------------- #
# data

def markets(bot: int) -> pd.DataFrame:
    """One row per settled market, with the decomposition of report §5.2.

    The platform scores RBP = 100·w·(field-average Brier − our Brier). The
    field-average Brier equals the Brier of the crowd's average forecast plus
    the cross-sectional variance of the field's forecasts, so
        RBP = edge (consensus Brier − ours) + premium (the field's variance),
    each times 100·w. The variance is recovered from the export and must be
    positive on every market, which also checks the stage weights."""
    order = mf.match_order()
    d = mf.settled(bot).merge(order[["match_id", "stage", "n"]], on="match_id", how="left")
    d["w"] = d["stage"].map(WEIGHT)
    d["y"] = (d["result_value"] == 100).astype(float)
    d["p"] = d["probability_submitted"] / 100
    d["c"] = d["crowd_value"] / 100
    d["brier"] = (d["p"] - d["y"]) ** 2
    d["brier_consensus"] = (d["c"] - d["y"]) ** 2
    d["brier_field"] = d["rbp"] / (100 * d["w"]) + d["brier"]
    d["variance"] = d["brier_field"] - d["brier_consensus"]
    if d["variance"].min() <= 0:
        raise SystemExit(f"bot {bot}: non-positive field variance — check the stage weights")
    d["edge"] = 100 * d["w"] * (d["brier_consensus"] - d["brier"])
    d["premium"] = 100 * d["w"] * d["variance"]
    d["category"] = d["question"].map(mf.category)
    d["dev"] = d["probability_submitted"] - d["crowd_value"]
    return d


def cluster_ci(d: pd.DataFrame, col: str, per_market: bool = False) -> tuple[float, float]:
    """95% percentile interval, resampling whole matches: markets in a match
    settle together (one blowout decides a dozen of them), so they are not
    independent observations."""
    g = d.groupby("match_id")[col].agg(["sum", "size"])
    s, n = g["sum"].to_numpy(), g["size"].to_numpy()
    idx = np.random.default_rng(SEED).integers(0, len(g), size=(N_BOOT, len(g)))
    stat = s[idx].sum(1) / (n[idx].sum(1) if per_market else 1)
    lo, hi = np.percentile(stat, [2.5, 97.5])
    return float(lo), float(hi)


def reliability(f: pd.Series, y: pd.Series, bins: int = 10) -> pd.DataFrame:
    b = (f * bins).clip(0, bins - 1e-9).astype(int)
    return pd.DataFrame({"f": f, "y": y, "b": b}).groupby("b").agg(
        n=("y", "size"), predicted=("f", "mean"), observed=("y", "mean"))


def murphy(f: pd.Series, y: pd.Series) -> dict:
    """Binned Murphy decomposition: Brier ≈ reliability − resolution + uncertainty."""
    r = reliability(f, y)
    base = y.mean()
    return dict(ece=float((r.n * (r.predicted - r.observed).abs()).sum() / r.n.sum()),
                rel=float((r.n * (r.predicted - r.observed) ** 2).sum() / r.n.sum()),
                res=float((r.n * (r.observed - base) ** 2).sum() / r.n.sum()),
                unc=float(base * (1 - base)))


# --------------------------------------------------------------------------- #
# LaTeX output

def num(x: float, d: int = 0, sign: bool = False) -> str:
    """A number for LaTeX text: thousands separators, a true minus sign."""
    s = f"{abs(x):,.{d}f}"
    if x < 0 and float(s.replace(",", "")) != 0:
        return "$-$" + s
    return ("$+$" if sign and float(s.replace(",", "")) != 0 else "") + s


def pct(x: float, d: int = 1) -> str:
    return f"{100 * x:.{d}f}"


def tex_escape(s: str) -> str:
    return s.replace("&", r"\&").replace("%", r"\%")


def write(name: str, text: str) -> None:
    GEN.mkdir(parents=True, exist_ok=True)
    (GEN / name).write_text(text)


# --------------------------------------------------------------------------- #
# numbers and tables

def build_numbers(b1: pd.DataFrame, b2: pd.DataFrame) -> dict[str, str]:
    m: dict[str, str] = {}
    for tag, d in (("One", b1), ("Two", b2)):
        lo, hi = cluster_ci(d, "rbp")
        plo, phi = cluster_ci(d, "premium")
        elo, ehi = cluster_ci(d, "edge")
        eplo, ephi = cluster_ci(d, "edge", per_market=True)
        m |= {
            f"Markets{tag}": num(len(d)), f"Matches{tag}": num(d.match_id.nunique()),
            f"Points{tag}": num(d.rbp.sum()), f"Points{tag}Exact": num(d.rbp.sum(), 1),
            f"Points{tag}Lo": num(lo), f"Points{tag}Hi": num(hi),
            f"Brier{tag}": f"{d.brier.mean():.4f}",
            f"ConsensusBrier{tag}": f"{d.brier_consensus.mean():.4f}",
            f"FieldBrier{tag}": f"{d.brier_field.mean():.4f}",
            f"AboveField{tag}": pct((d.rbp > 0).mean()),
            f"Closer{tag}": pct((d.brier < d.brier_consensus).mean()),
            f"Premium{tag}": num(d.premium.sum()), f"Premium{tag}Lo": num(plo),
            f"Premium{tag}Hi": num(phi), f"PremiumShare{tag}": pct(d.premium.sum() / d.rbp.sum(), 0),
            f"PremiumPerMarket{tag}": f"{d.premium.mean():.2f}",
            f"Edge{tag}": num(d.edge.sum(), sign=True), f"Edge{tag}Lo": num(elo),
            f"Edge{tag}Hi": num(ehi, sign=True), f"EdgePerMarket{tag}": num(d.edge.mean(), 2, sign=True),
            f"EdgePerMarket{tag}Lo": num(eplo, 2), f"EdgePerMarket{tag}Hi": num(ephi, 2, sign=True),
        }
        st = d.groupby("stage").agg(n=("rbp", "size"), pts=("rbp", "sum"))
        m |= {f"GroupPoints{tag}": num(st.loc["Group", "pts"]),
              f"GroupMarkets{tag}": num(st.loc["Group", "n"]),
              f"KnockoutPoints{tag}": num(st.loc["Knockout", "pts"]),
              f"KnockoutMarkets{tag}": num(st.loc["Knockout", "n"]),
              f"FinalPoints{tag}": num(st.loc["Final", "pts"], 1),
              f"AfterGroupShare{tag}": pct(1 - st.loc["Group", "pts"] / d.rbp.sum(), 0)}
    m["MarketsBoth"] = num(len(b1) + len(b2))
    m["FieldSD"] = f"{100 * np.sqrt(b1.variance.mean()):.0f}"
    m["FieldVariance"] = f"{b1.variance.mean():.4f}"
    m["GroupMatches"] = num((mf.match_order().stage == "Group").sum())
    m["KnockoutMatches"] = num((mf.match_order().stage != "Group").sum())

    ours, crowd = murphy(b1.p, b1.y), murphy(b1.c, b1.y)
    m |= {"ECEOurs": f"{ours['ece']:.3f}", "ECECrowd": f"{crowd['ece']:.3f}",
          "RELOurs": f"{ours['rel']:.4f}", "RELCrowd": f"{crowd['rel']:.4f}",
          "RESOurs": f"{ours['res']:.4f}", "RESCrowd": f"{crowd['res']:.4f}",
          "UNC": f"{ours['unc']:.4f}", "MeanForecast": pct(b1.p.mean()),
          "MeanCrowd": pct(b1.c.mean()), "BaseRate": pct(b1.y.mean())}

    # Bot 2 against bot 1 on the markets both played (identical odds and inputs).
    j = b1.merge(b2, on="market_id", suffixes=("_1", "_2"))
    j["gain"] = 100 * j["w_1"] * (j["brier_1"] - j["brier_2"])          # > 0: bot 2 better
    div = j[j.probability_submitted_1 != j.probability_submitted_2].rename(
        columns={"match_id_1": "match_id"})
    lo, hi = cluster_ci(div, "gain")
    only1 = b1[~b1.market_id.isin(b2.market_id)]
    m |= {"CommonMarkets": num(len(j)), "DivergentMarkets": num(len(div)),
          "DivergentMatches": num(div.match_id.nunique()),
          "BotTwoBetter": num((div.brier_2 < div.brier_1).sum()),
          "BotOneBetter": num((div.brier_1 < div.brier_2).sum()),
          "ABNet": num(div.gain.sum(), 1, sign=True), "ABLo": num(lo), "ABHi": num(hi, sign=True),
          "CommonBrierOne": f"{j.brier_1.mean():.4f}", "CommonBrierTwo": f"{j.brier_2.mean():.4f}",
          "BotOneOnlyMarkets": num(len(only1)), "BotOneOnlyMatches": num(only1.match_id.nunique()),
          "BotOneOnlyPoints": num(only1.rbp.sum(), 1), "BotTwoFirstMatch": num(b2.n.min()),
          "GapPoints": num(b1.rbp.sum() - b2.rbp.sum(), 1)}

    # Per-category figures quoted in the text (bot 1).
    cat = b1.groupby("category").agg(n=("rbp", "size"), edge=("edge", "sum"), ours=("p", "mean"),
                                     crowd=("c", "mean"), real=("y", "mean"))
    keys = {"Cards": "Cards", "PlayerSOT": "Player shots on target", "Penalties": "Penalties",
            "Scorers": "Goal scorers", "Timing": "Timing windows",
            "Goals": "Goals, results & halftime", "TeamSOT": "Team & total shots on target"}
    for key, label in keys.items():
        r = cat.loc[label]
        m |= {f"CrowdBias{key}": num(100 * (r.crowd - r.real), 1, sign=True),
              f"OurBias{key}": num(100 * (r.ours - r.real), 1, sign=True),
              f"OurBiasAbs{key}": f"{abs(100 * (r.ours - r.real)):.1f}",
              f"Edge{key}": num(r.edge, sign=True),
              f"EdgePerMarket{key}": num(r.edge / r.n, 1, sign=True),
              f"Markets{key}": num(r.n)}
    bins = deviation_bins(b1)
    m |= {"DevPointsLowest": num(bins.per.iloc[0], 1, sign=True),
          "DevPointsNear": num(bins.per.iloc[2], 1, sign=True),
          "DevPointsHighest": num(bins.per.iloc[-1], 1, sign=True)}

    dev = b1.dev
    m |= {"BelowMarkets": num((dev < -5).sum()), "AboveMarkets": num((dev >= 5).sum()),
          "FarAboveMarkets": num((dev >= 15).sum()), "NearMarkets": num(dev.between(-5, 4).sum())}

    bt = pd.read_csv(DATA / "backtest_report.csv")
    cal = pd.read_csv(DATA / "backtest_calibration.csv")
    m |= {"BacktestForecasts": num(cal.n.sum()), "BacktestFamilies": num(len(bt)),
          "BacktestBeatHalf": num((bt.edge_vs_50 > 0).sum()),
          "BacktestBeatBase": num((bt.edge_vs_baserate > 0).sum()),
          "BacktestPerFamily": num(bt.n.min())}

    act = pd.read_csv(DATA / "agent_activity.csv")
    actions = act.filter(like="actions_").sum()
    m |= {"RunsTotal": num(act.submit.sum() + act.preview.sum()), "RunsSubmit": num(act.submit.sum()),
          "RunsPreview": num(act.preview.sum()), "ActionsTotal": num(actions.sum()),
          "ActionsCreate": num(actions["actions_create"]), "ActionsUpdate": num(actions["actions_update"]),
          "ActionsUnchanged": num(actions["actions_unchanged"]),
          "ActionsError": num(actions["actions_error"]), "OperatedDays": num(len(act))}

    boards = json.loads((ROOT / "examples" / "boards.json").read_text())
    m |= {"Boards": num(len(boards)), "BoardMarkets": num(sum(len(b["markets"]) for b in boards)),
          "numfamilies": num(len(family_counts()))}
    return m


def write_numbers(m: dict[str, str]) -> None:
    lines = ["% Generated by paper/build_assets.py from results/data — do not edit by hand."]
    lines += [rf"\newcommand{{\{k}}}{{{v}}}" for k, v in m.items()]
    write("numbers.tex", "\n".join(lines) + "\n")


def table_decomposition(b1: pd.DataFrame, b2: pd.DataFrame) -> None:
    rows = []
    for name, d in (("Bot 1 (stable)", b1), ("Bot 2 (A/B bench)", b2)):
        values, cis = [name], [""]
        for col in ("rbp", "premium", "edge"):
            lo, hi = cluster_ci(d, col)
            values.append(num(d[col].sum(), sign=col == "edge"))
            cis.append(f"\\ci{{{num(lo)}}}{{{num(hi)}}}")
        values += [f"{d.brier.mean():.4f}", f"{d.brier_consensus.mean():.4f}",
                   f"{d.brier_field.mean():.4f}"]
        cis += ["", "", ""]
        rows.append(" & ".join(values) + r" \\")
        rows.append(" & ".join(cis) + r" \\[3pt]")
    write("tab_decomposition.tex", "\n".join(rows) + "\n")


def table_categories(d: pd.DataFrame) -> pd.DataFrame:
    g = d.groupby("category").agg(
        n=("rbp", "size"), points=("rbp", "sum"), edge=("edge", "sum"),
        ours=("p", "mean"), crowd=("c", "mean"), real=("y", "mean"))
    g["per"] = g.points / g.n
    g = g.sort_values("points", ascending=False)
    rows = [" & ".join([tex_escape(cat), num(r.n), num(r.points, sign=True), num(r.per, 1, sign=True),
                        num(r.edge, sign=True), num(100 * (r.ours - r.real), 1, sign=True),
                        num(100 * (r.crowd - r.real), 1, sign=True)]) + r" \\"
            for cat, r in g.iterrows()]
    total = [r"\midrule All markets", num(len(d)), num(d.rbp.sum(), sign=True),
             num(d.rbp.mean(), 1, sign=True), num(d.edge.sum(), sign=True),
             num(100 * (d.p.mean() - d.y.mean()), 1, sign=True),
             num(100 * (d.c.mean() - d.y.mean()), 1, sign=True)]
    write("tab_categories.tex", "\n".join(rows + [" & ".join(total) + r" \\"]) + "\n")
    return g


# Half-open bins on d = our probability minus the crowd's average, in points
# (the same bins as results/make_figures.py): label for LaTeX, label for matplotlib.
DEV_BINS = [(-100, -15, r"$d < -15$", r"$d<-15$"),
            (-15, -5, r"$-15 \le d < -5$", r"$[-15,-5)$"),
            (-5, 5, r"$-5 \le d < 5$", r"$[-5,5)$"),
            (5, 15, r"$5 \le d < 15$", r"$[5,15)$"),
            (15, 100, r"$d \ge 15$", r"$d\geq 15$")]


def deviation_bins(d: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for lo, hi, label, math in DEV_BINS:
        s = d[(d.dev >= lo) & (d.dev < hi)]
        rows.append(dict(label=label, math=math, n=len(s), ours=s.p.mean(), crowd=s.c.mean(), real=s.y.mean(),
                         per=s.rbp.mean(), edge=s.edge.mean(), premium=s.premium.mean()))
    return pd.DataFrame(rows)


def table_deviation(t: pd.DataFrame) -> None:
    rows = [" & ".join([r.label, num(r.n), pct(r.ours, 0), pct(r.crowd, 0), pct(r.real, 0),
                        num(r.per, 1, sign=True), num(r.premium, 1, sign=True),
                        num(r.edge, 1, sign=True)]) + r" \\" for r in t.itertuples()]
    write("tab_deviation.tex", "\n".join(rows) + "\n")


BACKTEST_NAMES = {
    "more_sot": "Team has more shots on target", "team_win": "Team wins",
    "more_corners": "Team has more corners", "team_goals_ge2": "Team scores 2+",
    "team_sot_ge4": "Team has 4+ shots on target", "team_more_goals_2h": "Team scores more in 2nd half",
    "ht_team_winning": "Team leads at halftime", "team_corners_ge5": "Team has 5+ corners",
    "team_scores_2h": "Team scores in 2nd half", "total_sot_ge8": "8+ total shots on target",
    "total_goals_ge3": "3+ total goals", "total_goals_2h_ge2": "2+ goals in 2nd half",
    "btts_and_ge3": "Both score and 3+ goals", "ht_tied": "Tied at halftime",
    "total_corners_ge10": "10+ total corners", "more_cards": "Team has more cards",
    "h2_more_than_h1": "More goals in 2nd half than 1st", "more_fouls": "Team commits more fouls",
    "total_cards_ge4": "4+ total cards",
}


def table_backtest() -> None:
    bt = pd.read_csv(DATA / "backtest_report.csv")
    rows = [" & ".join([BACKTEST_NAMES[r.family], f"{r.brier_model:.4f}", f"{r.brier_50:.4f}",
                        f"{r.brier_baserate:.4f}", num(100 * r.edge_vs_baserate, 2, sign=True)])
            + r" \\" for r in bt.itertuples()]
    write("tab_backtest.tex", "\n".join(rows) + "\n")


def table_count_models() -> None:
    params = json.loads((ROOT / "data" / "models" / "count_models.json").read_text())
    names = {"corners": "Corners", "cards": "Cards", "sot": "Shots on target", "fouls": "Fouls"}
    rows = []
    for stat, label in names.items():
        c, a = params[stat]["coef"], params[stat]["alpha"]
        rows.append(" & ".join([label] + [num(c[k], 3) for k in
                                          ("const", "log_lam_team", "log_lam_opp", "is_home")]
                               + [f"{a:.3f}"]) + r" \\")
    write("tab_count_models.tex", "\n".join(rows) + "\n")


def family_counts() -> pd.Series:
    """How many archived questions parse into each question family."""
    from probcup.questions.parser import parse
    from probcup.questions.teams import ALL_TEAM_NAMES
    boards = json.loads((ROOT / "examples" / "boards.json").read_text())
    counts = pd.Series([parse(m["question"], teams=ALL_TEAM_NAMES).family
                        for b in boards for m in b["markets"]]).value_counts()
    return counts.sort_values(ascending=False, kind="stable")


def table_families() -> None:
    """Every question family on the archived boards, with how often it was asked,
    laid out in three column pairs for the appendix."""
    counts = family_counts()
    items = [(r"\texttt{" + f.replace("_", r"\_") + "}", str(n)) for f, n in counts.items()]
    rows = -(-len(items) // 3)
    cols = [items[i * rows:(i + 1) * rows] for i in range(3)]
    lines = []
    for r in range(rows):
        cells = []
        for c in cols:
            cells += list(c[r]) if r < len(c) else ["", ""]
        lines.append(" & ".join(cells) + r" \\")
    write("tab_families.tex", "\n".join(lines) + "\n")


# --------------------------------------------------------------------------- #
# figures

def _save(fig, name: str) -> None:
    FIGS.mkdir(parents=True, exist_ok=True)
    fig.savefig(FIGS / f"{name}.pdf", metadata={"CreationDate": None, "ModDate": None,
                                                "Producer": None, "Creator": None})
    plt.close(fig)


def _thousands(ax, axis="y"):
    f = matplotlib.ticker.FuncFormatter(lambda v, _: "$" + f"{v:,.0f}".replace(",", "{,}") + "$")
    (ax.yaxis if axis == "y" else ax.xaxis).set_major_formatter(f)


SIGNED = matplotlib.ticker.FuncFormatter(lambda v, _: f"${v:+.0f}$" if v else "$0$")


def fig_cumulative(b1: pd.DataFrame, b2: pd.DataFrame) -> None:
    order = mf.match_order()
    fig, ax = plt.subplots(figsize=(TEXT_W, 2.5), layout="constrained")
    ax.grid(axis="x", visible=False)
    ranks = {"Bot 1": "#17", "Bot 2": "#31"}
    series = [(b1, "rbp", C["s1"], "-", 1.4, "Bot 1 (stable)"),
              (b2, "rbp", C["s2"], "-", 1.4, "Bot 2 (A/B bench)"),
              (b1, "premium", C["ink2"], (0, (3, 2)), 0.9,
               "Crowd average, on bot 1's markets")]
    top = 0.0
    for d, col, color, style, lw, label in series:
        per = d.groupby("match_id")[col].sum()
        y = order["match_id"].map(per).fillna(0).cumsum()
        end_ = y.iloc[-1]
        rank = next((f", {r}" for k, r in ranks.items() if label.startswith(k)), "")
        ax.plot(order["n"], y, color=color, linestyle=style, linewidth=lw, zorder=3,
                label=f"{label}: " + f"${end_:,.0f}$".replace(",", "{,}") + rank)
        top = max(top, y.max())
    ko = order.loc[order.stage != "Group", "n"].min()
    fin = order.loc[order.stage == "Final", "n"].min()
    for x in (ko - 0.5, fin - 0.5):
        ax.axvline(x, color=C["axis"], linewidth=0.6, zorder=1)
    band = top * 1.13
    ax.text((ko - 0.5) / 2, band, r"Group stage (1$\times$)", color=C["muted"], ha="center")
    ax.text((ko + fin - 1) / 2, band, r"Knockouts (2$\times$)", color=C["muted"], ha="center")
    ax.text(fin - 1.4, top * 0.12, r"Final (3$\times$)", color=C["muted"], ha="right",
            va="bottom", rotation=90)
    ax.set_xlim(0.5, order["n"].iloc[-1] + 0.5)
    ax.set_ylim(0, top * 1.24)
    ax.set_xlabel("Matches in the order played")
    ax.set_ylabel("Cumulative points")
    _thousands(ax)
    ax.legend(loc="upper left", bbox_to_anchor=(0.0, 0.9))
    _save(fig, "cumulative_points")


SHORT = {"Goals, results & halftime": "Goals & results", "Player shots on target": "Player SOT",
         "Team & total shots on target": "Team & total SOT"}


def fig_bias_and_deviation(b1: pd.DataFrame, dev: pd.DataFrame) -> None:
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(TEXT_W, 2.75), layout="constrained",
                                 gridspec_kw={"width_ratios": [1.05, 1]})
    fig.get_layout_engine().set(wspace=0.08)
    g = b1.groupby("category").agg(n=("y", "size"), ours=("p", "mean"), crowd=("c", "mean"),
                                   real=("y", "mean"))
    g = g[g.n >= MIN_CATEGORY_N]
    g["b_ours"], g["b_crowd"] = 100 * (g.ours - g.real), 100 * (g.crowd - g.real)
    g = g.sort_values("b_crowd")
    ys = np.arange(len(g))
    a1.grid(axis="y", visible=False)
    a1.axvline(0, color=C["axis"], linewidth=0.6, zorder=1)
    for y, (_, r) in zip(ys, g.iterrows()):
        a1.plot([r.b_ours, r.b_crowd], [y, y], color=C["grid"], linewidth=2.4, zorder=2,
                solid_capstyle="butt")
    a1.scatter(g.b_crowd, ys, s=18, color=C["s2"], zorder=3, label="Crowd average",
               edgecolor="white", linewidth=0.6)
    a1.scatter(g.b_ours, ys, s=18, color=C["s1"], zorder=4, label="Bot 1",
               edgecolor="white", linewidth=0.6)
    a1.set_yticks(ys, [f"{SHORT.get(c, c)} ({n})" for c, n in zip(g.index, g.n)])
    a1.set_xlabel("Mean forecast minus realized rate (points)")
    a1.xaxis.set_major_formatter(SIGNED)
    a1.set_ylim(-0.7, len(g) - 0.3)
    a1.legend(loc="lower right", handletextpad=0.1, borderaxespad=0.1)
    a1.set_title("(a) Bias by market type", loc="left", color=C["ink"])

    # Bars sit between the bin edges, so the axis reads as a number line of d.
    xs = np.arange(len(dev))
    wbar = 0.38
    a2.grid(axis="x", visible=False)
    a2.axhline(0, color=C["axis"], linewidth=0.6, zorder=2)
    a2.bar(xs - wbar / 2, dev.per, wbar, color=C["s1"], label="Points per market", zorder=3)
    a2.bar(xs + wbar / 2, dev.edge, wbar, color=C["s2"], label="Edge over the crowd average",
           zorder=3)
    edges = [lo for lo, *_ in DEV_BINS[1:]]
    a2.set_xticks(xs[:-1] + 0.5, [f"${e:d}$" for e in edges])
    a2.set_xlim(-0.6, len(dev) - 0.4)
    lo, hi = min(dev.per.min(), dev.edge.min()), max(dev.per.max(), dev.edge.max())
    a2.set_ylim(lo - 1.2, hi + 2.0)
    for x, n in zip(xs, dev.n):
        a2.text(x, hi + 0.5, f"$n={n}$", ha="center", va="bottom", color=C["muted"], fontsize=7.5)
    a2.set_xlabel("$d$ = bot 1 minus the crowd average (points)")
    a2.yaxis.set_major_formatter(SIGNED)
    a2.legend(loc="lower left", handletextpad=0.4, borderaxespad=0.1)
    a2.set_title("(b) Points by disagreement", loc="left", color=C["ink"])
    _save(fig, "bias_and_deviation")


def fig_calibration(b1: pd.DataFrame) -> None:
    bt = pd.read_csv(DATA / "backtest_calibration.csv")
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(TEXT_W, 3.0), layout="constrained")
    pct_fmt = matplotlib.ticker.PercentFormatter(1.0, decimals=0, symbol="%")
    for ax in (a1, a2):
        ax.plot([0, 1], [0, 1], color=C["axis"], linewidth=0.6, zorder=1)
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.set_aspect("equal")
        ax.xaxis.set_major_formatter(pct_fmt)
        ax.yaxis.set_major_formatter(pct_fmt)
        ax.set_xlabel("Forecast probability")
    a1.set_ylabel("Observed frequency")
    a1.plot(bt.predicted, bt.observed, "-o", color=C["s1"], linewidth=1.2, markersize=3.2,
            markeredgecolor="white", markeredgewidth=0.5, zorder=3)
    a1.set_title(f"(a) Club backtest, " + f"${bt.n.sum():,}$".replace(",", "{,}") + " forecasts",
                 loc="left", color=C["ink"])
    for col, color, label in (("p", C["s1"], "Bot 1"), ("c", C["s2"], "Crowd average")):
        r = reliability(b1[col], b1.y)
        r = r[r.n >= 20]
        a2.plot(r.predicted, r.observed, "-o", color=color, linewidth=1.2, markersize=3.6,
                markeredgecolor="white", markeredgewidth=0.5, zorder=3, label=label)
    a2.legend(loc="upper left")
    a2.set_title(f"(b) Tournament, " + f"${len(b1):,}$".replace(",", "{,}") + " markets (bot 1)",
                 loc="left", color=C["ink"])
    _save(fig, "calibration")


def fig_activity() -> None:
    act = pd.read_csv(DATA / "agent_activity.csv", parse_dates=["date"])
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(TEXT_W, 2.7), sharex=True, layout="constrained",
                                 gridspec_kw={"height_ratios": [1, 1.25]})
    xs = np.arange(len(act))
    w = 0.72
    for ax in (a1, a2):
        ax.grid(axis="x", visible=False)
    a1.bar(xs, act.submit, w, color=C["s1"], zorder=3)
    a1.bar(xs, act.preview, w, bottom=act.submit, color=C["s2"], zorder=3)
    a1.set_ylabel("Pipeline runs")
    a1.legend(handles=[Patch(color=C["s1"], label="Submitting"),
                       Patch(color=C["s2"], label="Preview (dry run)")],
              loc="upper right", ncol=2)
    a1.set_ylim(0, (act.submit + act.preview).max() * 1.35)
    stack = [("actions_create", C["s1"], "Created"), ("actions_update", C["s2"], "Updated"),
             ("actions_unchanged", C["s3"], "Checked, unchanged")]
    bottom = np.zeros(len(act))
    for col, color, label in stack:
        a2.bar(xs, act[col], w, bottom=bottom, color=color, zorder=3, label=label)
        bottom += act[col].to_numpy()
    a2.set_ylabel("Submission actions")
    _thousands(a2)
    a2.legend(loc="upper right", ncol=3)
    a2.set_ylim(0, bottom.max() * 1.3)
    ticks = [i for i, d in enumerate(act.date) if d.weekday() == 0]
    a2.set_xticks(ticks, [act.date[i].strftime("%b %-d") for i in ticks])
    a2.set_xlim(-0.6, len(act) - 0.4)
    _save(fig, "agent_activity")


def main() -> None:
    b1, b2 = markets(1), markets(2)
    dev = deviation_bins(b1)
    write_numbers(build_numbers(b1, b2))
    table_decomposition(b1, b2)
    table_categories(b1)
    table_deviation(dev)
    table_backtest()
    table_count_models()
    table_families()
    fig_cumulative(b1, b2)
    fig_bias_and_deviation(b1, dev)
    fig_calibration(b1)
    fig_activity()
    print(f"wrote {len(list(GEN.glob('*.tex')))} generated files and "
          f"{len(list(FIGS.glob('*.pdf')))} figures")


if __name__ == "__main__":
    main()
