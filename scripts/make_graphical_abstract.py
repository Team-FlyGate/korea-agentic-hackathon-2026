#!/usr/bin/env python3
"""Draw the graphical abstract for the pharmacovigilance paper.

Why a graphical abstract at all
-------------------------------
Several journals print one figure on the first page that has to carry the whole paper.
A reader decides from it whether to read on, so it cannot be a teaser: it must state the
problem, the design, and both findings, with the numbers visible.

Layout decision
---------------
Three panels left to right, because the paper's argument is a sequence and not a set.
Panel A is why a ground truth is unavailable, B is what we use instead, C is the two
results. The one thing a reader should take away is the downward arrow in C: our own
number falls when names are hidden. Putting the unflattering result first, inside the
figure, is the same ordering the paper uses.

Run:
  /opt/anaconda3/envs/rag/bin/python scripts/make_graphical_abstract.py
"""
from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

ROOT = Path(__file__).resolve().parents[1]
FONT_DIR = ROOT / "docs" / "figures" / "fonts"
OUT = ROOT / "docs" / "figures" / "graphical-abstract-pv.png"

for ttf in FONT_DIR.glob("Pretendard-*.ttf"):
    font_manager.fontManager.addfont(str(ttf))
plt.rcParams["font.family"] = "Pretendard"
plt.rcParams["axes.unicode_minus"] = False

# Muted Tableau tones. Saturated primaries read as clip art at this size.
INK = "#2f3337"
MUTED = "#7f8c8d"
BLUE = "#5b7fa6"
RED = "#b4656f"
GREEN = "#6f9e7f"
SAND = "#c8b88a"
PANEL = "#f4f2ee"


def panel(ax, x, w, title, letter):
    ax.add_patch(FancyBboxPatch((x, 0.06), w, 0.80, boxstyle="round,pad=0.012,rounding_size=0.02",
                                fc=PANEL, ec="#ddd8d0", lw=1.2, zorder=0))
    ax.text(x + 0.018, 0.905, letter, fontsize=13, fontweight="bold", color=MUTED, va="top")
    ax.text(x + 0.055, 0.905, title, fontsize=12.5, fontweight="semibold", color=INK, va="top")


def main() -> int:
    fig, ax = plt.subplots(figsize=(13.2, 5.0), dpi=200)
    ax.set_xlim(0, 1); ax.set_ylim(0, 1); ax.axis("off")
    fig.patch.set_facecolor("white")

    ax.text(0.5, 0.975, "정답이 부실한 도메인에서 판정 모델을 평가하는 설계",
            fontsize=15.5, fontweight="bold", color=INK, ha="center", va="top")

    # ---- A. no ground truth -------------------------------------------------
    panel(ax, 0.015, 0.29, "사람 정답이 흔들린다", "A")
    ax.text(0.16, 0.755, "같은 사례, 다른 결론", fontsize=10.5, color=MUTED, ha="center")

    # The kappa value sits above its bar, right-aligned to the bar end. Putting it to the
    # right of the bar pushed it past the panel edge and into the panel arrow.
    bars = [("척도 없는 전문가 6명", 0.30, RED, "κ 0.21–0.40"),
            ("같은 전문가, 척도 사용", 0.775, GREEN, "κ 0.69–0.86")]
    y = 0.615
    for label, frac, color, note in bars:
        ax.text(0.038, y + 0.058, label, fontsize=9.5, color=INK)
        ax.text(0.273, y + 0.058, note, fontsize=9.5, color=color, fontweight="bold", ha="right")
        ax.add_patch(FancyBboxPatch((0.038, y - 0.005), 0.235, 0.042,
                                    boxstyle="round,pad=0,rounding_size=0.008",
                                    fc="#e6e2da", ec="none"))
        ax.add_patch(FancyBboxPatch((0.038, y - 0.005), 0.235 * frac, 0.042,
                                    boxstyle="round,pad=0,rounding_size=0.008",
                                    fc=color, ec="none"))
        y -= 0.150

    ax.text(0.038, 0.335, "도구를 바꾸면 결론이 옮겨간다", fontsize=9.8, color=INK, fontweight="semibold")
    ax.text(0.038, 0.285, "같은 399건", fontsize=8.8, color=MUTED, va="top")
    ax.text(0.038, 0.235, "WHO-UMC   53.3% 확실", fontsize=9, color=INK, va="top")
    ax.text(0.038, 0.190, "Naranjo    96.74% 가능성 높음", fontsize=9, color=INK, va="top")
    ax.text(0.038, 0.100, "저자가 만든 정답은 라벨을 잴 뿐이다",
            fontsize=9.4, color=RED, fontweight="semibold")

    # ---- B. four label families --------------------------------------------
    panel(ax, 0.335, 0.29, "제3자가 확정한 라벨 네 계열", "B")
    ax.text(0.48, 0.755, "계열마다 자기 천장을 함께 잰다", fontsize=10.5, color=MUTED, ha="center")

    fams = [("규제 문서 원문", 0.474, "기재 쌍의 52.6%에 신호 없음"),
            ("규제 조치 이력", 0.15, "조치 9건, 오경보 분모 없음"),
            ("규제 목록·규칙", 1.0, "정의로 주어진다"),
            ("운영 종점", 0.55, "행정 분류이지 임상 아님")]
    y = 0.645
    for label, frac, note in fams:
        ax.text(0.357, y + 0.038, label, fontsize=9.5, color=INK)
        ax.add_patch(FancyBboxPatch((0.357, y - 0.018), 0.245, 0.032,
                                    boxstyle="round,pad=0,rounding_size=0.006",
                                    fc="#e6e2da", ec="none"))
        ax.add_patch(FancyBboxPatch((0.357, y - 0.018), 0.245 * frac, 0.032,
                                    boxstyle="round,pad=0,rounding_size=0.006",
                                    fc=SAND if frac < 0.99 else GREEN, ec="none"))
        ax.text(0.357, y - 0.052, note, fontsize=8.2, color=MUTED)
        y -= 0.145

    ax.text(0.357, 0.095, "정확도를 100%가 아니라 이 천장에 대고 읽는다",
            fontsize=9.2, color=INK, fontweight="semibold")

    # ---- C. two findings ----------------------------------------------------
    panel(ax, 0.655, 0.33, "재 보니 둘이 뒤집혔다", "C")

    # The drop label goes to the LEFT of the arrow. On the right it landed on the 0.815
    # reference line, which is the one number a reader must be able to read here.
    ax.text(0.668, 0.782, "① 판별력이 통계가 아니라 이름에서 왔다",
            fontsize=10, color=INK, fontweight="semibold")
    ax.plot([0.724, 0.724], [0.560, 0.730], color="#ddd8d0", lw=1.0, zorder=1)
    for yy, val, tag, color in [(0.730, "0.960", "이름 보임", MUTED), (0.560, "0.790", "이름 가림", RED)]:
        ax.plot([0.724], [yy], "o", ms=9, color=color, zorder=3)
        ax.text(0.740, yy, f"{val}   {tag}", fontsize=9.8, color=INK, va="center")
    ax.plot([0.724], [0.645], "^", ms=7, color=MUTED, zorder=3)
    ax.text(0.740, 0.645, "0.815   최고 통계 지표", fontsize=8.8, color=MUTED, va="center")
    ax.add_patch(FancyArrowPatch((0.702, 0.724), (0.702, 0.566), arrowstyle="-|>",
                                 mutation_scale=13, color=RED, lw=2.2))
    ax.text(0.693, 0.660, "0.170", fontsize=11, color=RED, fontweight="bold", ha="right")
    ax.text(0.693, 0.618, "누수", fontsize=9, color=RED, ha="right")

    ax.text(0.672, 0.455, "② 이득은 순위가 아니라 경로에서 왔다",
            fontsize=10, color=INK, fontweight="semibold")
    ax.text(0.672, 0.405, "모델 단독 AUROC 0.898", fontsize=8.8, color=MUTED)

    for x0, label, human, serious, color in [(0.690, "경로 2개", 302, "234 / 250", MUTED),
                                             (0.845, "경로 3개", 138, "247 / 250", BLUE)]:
        ax.add_patch(FancyBboxPatch((x0, 0.185), 0.115, 0.165,
                                    boxstyle="round,pad=0.008,rounding_size=0.012",
                                    fc="white", ec=color, lw=1.6))
        ax.text(x0 + 0.0575, 0.318, label, fontsize=9.2, color=INK, ha="center", fontweight="semibold")
        ax.text(x0 + 0.0575, 0.262, f"{human}", fontsize=17, color=color, ha="center",
                fontweight="bold")
        ax.text(x0 + 0.0575, 0.228, "사람이 먼저 볼 건수", fontsize=7.8, color=MUTED, ha="center")
        ax.text(x0 + 0.0575, 0.198, f"중대 {serious}", fontsize=8, color=INK, ha="center")

    ax.add_patch(FancyArrowPatch((0.812, 0.268), (0.840, 0.268), arrowstyle="-|>",
                                 mutation_scale=13, color=BLUE, lw=2.0))
    ax.text(0.672, 0.118, "같은 모델인데 사람 일이 절반 아래로 줄고 중대 사례는 더 닿는다",
            fontsize=9.2, color=INK, fontweight="semibold")

    for x in (0.310, 0.630):
        ax.add_patch(FancyArrowPatch((x, 0.46), (x + 0.020, 0.46), arrowstyle="-|>",
                                     mutation_scale=15, color=MUTED, lw=2.0))

    fig.savefig(OUT, bbox_inches="tight", facecolor="white")
    print(f"{OUT.relative_to(ROOT)}  {OUT.stat().st_size // 1024} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
