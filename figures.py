"""Draw the two result charts from results/*.csv.  Run simulate.py first."""
import csv
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

HERE = Path(__file__).parent
RES, FIG = HERE / "results", HERE / "figures"
FIG.mkdir(exist_ok=True)

INK, MUTED, GRID, SURFACE = "#0b0b0b", "#52514e", "#e4e3df", "#ffffff"
BLUE, ORANGE, AQUA, YELLOW = "#2a78d6", "#eb6834", "#1baf7a", "#eda100"

if any(f.name == "Inter" for f in font_manager.fontManager.ttflist):
    plt.rcParams["font.family"] = "Inter"
plt.rcParams.update({"font.size": 11, "text.color": INK, "axes.labelcolor": MUTED,
                     "xtick.color": MUTED, "ytick.color": MUTED,
                     "figure.facecolor": SURFACE, "axes.facecolor": SURFACE})


def header(fig, title, subtitle):
    fig.text(0.04, 0.95, title, fontsize=15, fontweight="bold", va="top")
    fig.text(0.04, 0.875, subtitle, fontsize=10.5, color=MUTED, va="top")


def chart_gate_outcomes():
    m = {r["metric"]: float(r["mean"]) for r in csv.DictReader((RES / "exp1_per_1000_actions.csv").read_text().splitlines())}
    no_gate_right = 1000 - m["no_gate_wrong"]
    rows = [("Without the gate", [no_gate_right, m["no_gate_wrong"], 0, 0]),
            ("With the gate", [m["auto_right"], m["auto_wrong"], m["held_wrong"], m["held_right"]])]
    cats = [("Written autonomously, correct", BLUE), ("Written autonomously, wrong", ORANGE),
            ("Held, would have been wrong", AQUA), ("Held, was actually correct", YELLOW)]

    fig, ax = plt.subplots(figsize=(9, 4.2), dpi=200)
    fig.subplots_adjust(left=0.2, right=0.96, top=0.62, bottom=0.14)
    header(fig, f"The gate cut wrong autonomous writes from {m['no_gate_wrong']:.0f} to "
                f"{m['auto_wrong']:.0f} per 1,000",
           "Synthetic CRM with assumed defect rates, mean of 20 runs. "
           "Held = routed to a human draft or a clarifying question.")
    for y, (label, vals) in enumerate(reversed(rows)):
        left = 0
        for v, (name, color) in zip(vals, cats):
            if v <= 0:
                continue
            ax.barh(y, v, left=left, height=0.42, color=color, edgecolor=SURFACE, linewidth=2)
            if v >= 60:
                ax.text(left + v / 2, y, f"{v:.0f}", ha="center", va="center", fontsize=10.5,
                        color="#ffffff" if color in (BLUE, ORANGE) else INK, fontweight="normal")
            elif v > 0:
                ax.annotate(f"{v:.0f}", (left + v / 2, y + 0.21), xytext=(0, 6),
                            textcoords="offset points", ha="center", fontsize=10.5, color=INK)
            left += v
    ax.set_yticks(range(len(rows)), [r[0] for r in reversed(rows)], fontsize=11, color=INK)
    ax.set_xlim(0, 1000)
    ax.set_xticks(range(0, 1001, 200), [f"{x:,}" for x in range(0, 1001, 200)])
    ax.set_xlabel("Renewal actions, per 1,000 proposed")
    ax.grid(axis="x", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(axis="y", length=0)
    handles = [plt.Rectangle((0, 0), 1, 1, color=c) for _, c in cats]
    fig.legend(handles, [n for n, _ in cats], loc="upper left", bbox_to_anchor=(0.035, 0.79),
               ncol=2, frameon=False, fontsize=10, handlelength=1, handleheight=1, columnspacing=1.6)
    fig.savefig(FIG / "fig3_gate_outcomes.png", facecolor=SURFACE)
    plt.close(fig)


def chart_feedback_loop():
    rows = list(csv.DictReader((RES / "exp2_feedback_loop.csv").read_text().splitlines()))
    series = {}
    for r in rows:
        series.setdefault(r["world"], []).append(float(r["cumulative_wrong_sends"]))
    weeks = list(range(1, len(series["provenance"]) + 1))
    lines = [("check_outgoing", "No provenance: reviewers check a sample of outgoing quotes", ORANGE),
             ("history_only", "Field history only: reviewers check agent-written roles first", AQUA),
             ("provenance", "Stamps: confirmed roles leave the queue, spare reviews check quotes", BLUE)]

    fig, ax = plt.subplots(figsize=(9, 5.2), dpi=200)
    fig.subplots_adjust(left=0.1, right=0.86, top=0.6, bottom=0.11)
    end_np, end_h, end_p = (series[k][-1] for k in ("check_outgoing", "history_only", "provenance"))
    header(fig, f"Who wrote it cut wrong quotes to {end_h:.0f}; who confirmed it, to {end_p:.0f}",
           "Synthetic CRM, 1,000 accounts, the same 720 human reviews over 12 weeks in every set-up, "
           "mean of 20 runs.")
    for key, label, color in lines:
        ys = series[key]
        ax.plot(weeks, ys, color=color, linewidth=2, solid_capstyle="round", label=label)
        ax.scatter([weeks[-1]], [ys[-1]], s=40, color=color, edgecolor=SURFACE, linewidth=2, zorder=3)
        ax.annotate(f"{ys[-1]:.0f}", (weeks[-1], ys[-1]), xytext=(8, 0), textcoords="offset points",
                    va="center", fontsize=11, color=INK, fontweight="bold")
    ax.set_xlim(1, 12.4)
    ax.set_xticks(weeks)
    ax.set_xlabel("Week")
    ax.set_ylabel("Wrong-recipient quotes, cumulative")
    ax.set_ylim(0, max(end_np, end_h, end_p) * 1.1)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(GRID)
    fig.legend(loc="upper left", bbox_to_anchor=(0.035, 0.81), ncol=1, frameon=False, fontsize=10,
               handlelength=1.6)
    fig.savefig(FIG / "fig4_feedback_loop.png", facecolor=SURFACE)
    plt.close(fig)


if __name__ == "__main__":
    chart_gate_outcomes()
    chart_feedback_loop()
    print("wrote figures/fig3_gate_outcomes.png and figures/fig4_feedback_loop.png")
