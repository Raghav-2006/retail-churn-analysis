"""Shared chart style so every figure uses the same palette and minimal layout."""
from pathlib import Path

import matplotlib.pyplot as plt

FIGURES = Path(__file__).resolve().parents[1] / "figures"

# One palette across the project: blue is the primary series, orange the comparison
BLUE = "#2a78d6"
ORANGE = "#eb6834"
AQUA = "#1baf7a"
GRAY = "#8c8b86"
INK = "#0b0b0b"
INK_2 = "#52514e"


def apply_style() -> None:
    plt.rcParams.update({
        "figure.figsize": (8, 4.5),
        "figure.dpi": 100,
        "savefig.dpi": 150,
        "savefig.bbox": "tight",
        "font.size": 10,
        "axes.titlesize": 12,
        "axes.titleweight": "bold",
        "axes.titlelocation": "left",
        "axes.labelcolor": INK_2,
        "axes.edgecolor": "#c9c8c3",
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.grid": True,
        "axes.axisbelow": True,
        "axes.grid.axis": "y",
        "grid.color": "#e6e5e0",
        "grid.linewidth": 0.8,
        "xtick.color": INK_2,
        "ytick.color": INK_2,
        "text.color": INK,
        "legend.frameon": False,
        "lines.linewidth": 2,
    })


def save(fig, name: str) -> Path:
    FIGURES.mkdir(exist_ok=True)
    path = FIGURES / name
    fig.savefig(path)
    return path
