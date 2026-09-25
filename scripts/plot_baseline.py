"""Slide plots of the baseline comparison (README 'Compared with Claude without this server').

Run: uv run --with matplotlib python scripts/plot_baseline.py   -> eval/plots/*.png
"""

from pathlib import Path

import matplotlib.pyplot as plt

OUT = Path(__file__).resolve().parents[1] / "eval" / "plots"
MODES = ["With this MCP server", "Claude + web search", "Claude alone"]
COLORS = ["#d52b1e", "#8c8c8c", "#c8c8c8"]  # Swiss red for the server, greys for the baselines

plt.rcParams.update({"font.size": 14, "axes.spines.top": False, "axes.spines.right": False,
                     "font.family": "sans-serif"})


def correctness() -> None:
    groups = [("17 evaluation questions", [16, 11, 6], 17), ("11 practice cases", [11, 7, 3], 11)]
    fig, ax = plt.subplots(figsize=(10, 5.6))
    width = 0.26
    for i, (mode, color) in enumerate(zip(MODES, COLORS)):
        xs = [g + (i - 1) * width for g in range(len(groups))]
        vals = [100 * passed[i] / total for _, passed, total in groups]
        bars = ax.bar(xs, vals, width * 0.92, color=color, label=mode)
        for bar, (_, passed, total) in zip(bars, groups):
            ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 1.5, f"{passed[i]}/{total}",
                    ha="center", va="bottom", fontsize=13, fontweight="bold" if i == 0 else "normal")
    ax.set_xticks(range(len(groups)), [g[0] for g in groups])
    ax.set_ylim(0, 108)
    ax.set_ylabel("Answered correctly (%)")
    ax.set_title("Correct, sourced answers", loc="left", fontweight="bold")
    ax.legend(frameon=False, loc="upper center", ncol=3, fontsize=12, bbox_to_anchor=(0.5, -0.1))
    fig.tight_layout()
    fig.savefig(OUT / "baseline_correctness.png", dpi=200)


def sources_and_effort() -> None:
    fig, (left, right) = plt.subplots(1, 2, figsize=(11, 5.2))
    names = ["MCP server", "Web search", "Claude alone"]

    links = [94, 72, 0]
    bars = left.bar(names, links, color=COLORS, width=0.6)
    for bar, label in zip(bars, ["94%\nof 34 links", "72%\nof 67 links", "1 link in\n28 answers"]):
        left.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 2, label, ha="center", va="bottom",
                  fontsize=12)
    left.set_ylim(0, 118)
    left.set_ylabel("Links to official Swiss authorities (%)")
    left.set_title("Where the links point", loc="left", fontweight="bold")

    seconds, calls = [14, 28, 15], ["1.2 tool calls", "2.4 searches", "no tools"]
    bars = right.bar(names, seconds, color=COLORS, width=0.6)
    for bar, s, c in zip(bars, seconds, calls):
        right.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.6, f"{s} s\n{c}", ha="center",
                   va="bottom", fontsize=12)
    right.set_ylim(0, 36)
    right.set_ylabel("Average time per answer (s)")
    right.set_title("Time and calls (17 questions)", loc="left", fontweight="bold")

    fig.tight_layout(w_pad=3)
    fig.savefig(OUT / "baseline_sources_time.png", dpi=200)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    correctness()
    sources_and_effort()
    print(f"wrote {OUT}")
