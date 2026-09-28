#!/usr/bin/env python3
"""지표 성능 결과 JSON 에서 ROC 그림 한 장을 그린다.

왜 있는가
---------
`scripts/metric_validation.py` 의 수치를 제출물과 FlyVigilante PR 에 넣을 그림으로 옮긴다. 수치는
모두 결과 JSON 에서 읽고 이 파일에는 적지 않는다. 그림은 낡은 숫자가 살아남기 쉬운 곳이다.

무엇을 그리는가
---------------
- 지표 일곱 개의 ROC 곡선. 점추정치는 실선, 하한은 점선이다. 색이 겹치는 경우(PRR, ROR, IC 는
  거의 같은 곡선이다)에도 선 모양과 범례의 AUC 로 구별된다.
- 고정 규칙 셋(Evans, ROR 신호, IC 신호)은 먹색 표식과 글자 라벨로 한 점씩 찍는다.
- 그림 아래에 참조 세트 정의와 "라벨 기재 예측이며 인과성 아님" 과 실행일(결과 파일 이름의 날짜,
  한국 표준시 기준)을 둔다.
- 색은 `dataviz` 스킬의 기본 범주 팔레트 1-7번 슬롯을 순서대로 쓴다. 인접 쌍 검증은
  `node scripts/validate_palette.js` 로 통과를 확인했고, 대비가 3:1 아래인 슬롯(청록, 노랑, 분홍)은
  범례 글자로 보완한다.
- 한글은 `docs/figures/fonts/Pretendard-*.ttf` 로 그린다(`scripts/make_figures.py` 와 같은 방식).

`--jev` 로 `scripts/metric_validation_jev.py` 결과를 주면(여러 번 줄 수 있다) Jev 곡선을 먹색으로 더
그린다. novel 팔은 일점쇄선, blind 팔은 점선이고 범례에 AUC 와 쌍 수를 적는다. `--jev` 가 없으면
그림은 전과 같다. 확률이 하나도 없는 파일(dry-run)은 건너뛴다.

`--omics` 로 `scripts/omics_plausibility.py --refset` 결과를 주면 다른 그림을 그린다. 오믹스 점수가
있는 행(약 해석, 표적 있음, PT 매핑)만으로 잰 지표 넷(prr, ror_lo, ic025, chi2_yates)의 곡선을 그 결과
JSON 에서 읽어 그리고, 그 위에 Open Targets 곡선 셋(전체, 문헌 제외 API, 문헌 제외 로컬)을 먹색으로
더한다. 선 모양은 실선, 일점쇄선, 점선이고 범례에 AUC 와 쌍 수를 적는다. 고정 규칙 점은 전체 행 기준이라
이 그림에는 찍지 않는다. 출력은 `metric_roc_omics_<date>.png` 이다.

matplotlib 이 필요하다. 저장소 `.venv` 에는 없으므로 matplotlib 이 있는 파이썬으로 돌린다.

사용법:
  <matplotlib 있는 python> scripts/plot_metric_roc.py \\
      --results eval/results/metric_validation_2026-09-28.json \
      [--jev eval/results/metric_validation_jev_novel_<date>.json ...] \\
      [--omics eval/results/omics_plausibility_refset_<date>.json.gz]
"""
from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
FIG_DIR = ROOT / "docs" / "figures"
FONT_DIR = FIG_DIR / "fonts"

# dataviz 기본 팔레트(라이트). 지표 순서가 슬롯 순서다.
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_SOFT = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
SERIES = {
    "prr": ("#2a78d6", "PRR", "-"),
    "ror": ("#eb6834", "ROR", "-"),
    "chi2_yates": ("#1baf7a", "카이제곱(Yates)", "-"),
    "ic": ("#eda100", "IC", "-"),
    "prr_lo": ("#e87ba4", "PRR 95% 하한", "--"),
    "ror_lo": ("#008300", "ROR 95% 하한", "--"),
    "ic025": ("#4a3aa7", "IC025", "--"),
}
RULES = {
    "evans_signal": ("o", "Evans"),
    "ror_signal": ("s", "ROR 신호"),
    "ic_signal": ("^", "IC 신호"),
}
OMICS_STYLE = {
    "max_score": ("Open Targets 연관 (전체)", "-"),
    "max_score_no_literature_api": ("문헌 제외 API", "-."),
    "max_score_no_literature_local": ("문헌 제외 로컬", ":"),
}
JEV_STYLE = {"novel": ("Jev novel", "-."), "blind": ("Jev blind", ":")}
WIDTH_IN, HEIGHT_IN, DPI = 8.0, 8.4, 200     # 1600 x 1680 px


def register_fonts() -> str:
    """Pretendard 네 굵기를 등록하고 기본 글꼴로 삼는다. 없으면 한글이 네모로 나온다."""
    found = sorted(FONT_DIR.glob("Pretendard-*.ttf"))
    if not found:
        raise FileNotFoundError(f"Pretendard 폰트가 없습니다: {FONT_DIR}")
    for path in found:
        font_manager.fontManager.addfont(str(path))
    family = font_manager.FontProperties(fname=str(found[0])).get_name()
    plt.rcParams["font.family"] = family
    plt.rcParams["axes.unicode_minus"] = False
    return family


def caption(res: dict, stamp: str) -> str:
    m = res["refset_meta"]
    return (f"참조 세트: SIDER 4.1 라벨 PT(MedDRA 16.1) 대 FlyVigilante FAERS 웨어하우스 {res['warehouse_asof']}, "
            f"약 {m['n_drugs']}종(보고 수 상위 {m['top_n']} + 손 선정).\n"
            f"양성(라벨 기재) {res['n_positive']:,}쌍, 음성(라벨 부재, SIDER PT 우주 안) {res['n_negative']:,}쌍, "
            f"유병률 {res['prevalence']:.1%}, 모든 쌍 a≥3.\n"
            f"라벨 기재 예측이며 인과성 아님. 실행일 {stamp}(한국 표준시 기준).")


def draw(res: dict, out: Path, stamp: str, jev: list[dict] | None = None) -> None:
    register_fonts()
    fig = plt.figure(figsize=(WIDTH_IN, HEIGHT_IN), dpi=DPI, facecolor=SURFACE)
    ax = fig.add_axes([0.10, 0.18, 0.86, 0.71], facecolor=SURFACE)
    ax.plot([0, 1], [0, 1], color=AXIS, lw=1, zorder=1)
    ax.text(0.83, 0.79, "무작위", color=MUTED, fontsize=9, rotation=45, ha="center", va="center")

    order = sorted(SERIES, key=lambda k: -res["metrics"][k]["auc"])
    for key in order:
        color, label, ls = SERIES[key]
        pts = res["metrics"][key]["roc"]
        ax.plot([p[0] for p in pts], [p[1] for p in pts], color=color, lw=2, ls=ls,
                solid_capstyle="round", dash_capstyle="round", zorder=3,
                label=f"{label}  AUC {res['metrics'][key]['auc']:.3f}")
    for doc in jev or []:
        name, ls = JEV_STYLE[doc["arm"]]
        pts = doc["jev"]["roc"]
        ax.plot([p[0] for p in pts], [p[1] for p in pts], color=INK, lw=2, ls=ls, zorder=4,
                label=f"{name}  AUC {doc['jev']['auc']:.3f} ({doc['jev']['n_used']:,}쌍)")

    # 표식 셋이 곡선 사이에 몰려 있어 라벨은 빈 자리(왼쪽 위, 대각선 아래)에 두고 가는 선으로 잇는다.
    spots = {"ror_signal": (0.03, 0.93), "ic_signal": (0.03, 0.87), "evans_signal": (0.30, 0.14)}
    for key, (marker, name) in RULES.items():
        r = res["fixed_rules"][key]
        x, y = 1 - r["specificity"], r["sensitivity"]
        ax.scatter([x], [y], s=70, marker=marker, color=INK, edgecolor=SURFACE, linewidth=2, zorder=5)
        ax.annotate(f"{name}  민감도 {r['sensitivity']:.2f} · 특이도 {r['specificity']:.2f}",
                    (x, y), xytext=spots[key], textcoords="data", fontsize=9, color=INK,
                    ha="left", va="center", zorder=6,
                    bbox={"boxstyle": "round,pad=0.25", "fc": SURFACE, "ec": "none"},
                    arrowprops={"arrowstyle": "-", "color": MUTED, "lw": 0.8,
                                "shrinkA": 0, "shrinkB": 5,
                                "relpos": (0, 0.5) if key == "evans_signal" else (1, 0)})

    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.set_aspect("equal")
    ax.set_xlabel("1 - 특이도(라벨 부재 쌍 중 신호로 잡힌 비율)", color=INK_SOFT, fontsize=10.5)
    ax.set_ylabel("민감도(라벨 기재 쌍 중 신호로 잡힌 비율)", color=INK_SOFT, fontsize=10.5)
    ax.grid(color=GRID, lw=1)
    ax.set_axisbelow(True)
    ax.tick_params(colors=MUTED, labelsize=9, length=0)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(AXIS)
    leg = ax.legend(loc="lower right", fontsize=9.5, frameon=True, framealpha=1,
                    edgecolor=GRID, facecolor=SURFACE, labelcolor=INK, handlelength=2.6,
                    title="지표(실선 점추정치, 점선 하한)", title_fontsize=9.5)
    leg.get_title().set_color(INK_SOFT)

    fig.text(0.10, 0.955, "불균형 지표의 라벨 기재 판별 성능 (파일럿)", fontsize=15,
             color=INK, weight="bold", ha="left")
    fig.text(0.10, 0.925, "문턱을 바꿔 가며 그린 ROC. 검은 점은 FlyVigilante 고정 규칙. PRR, ROR, IC 곡선과 두 하한 곡선은 서로 겹친다.",
             fontsize=10.5, color=INK_SOFT, ha="left")
    fig.text(0.10, 0.10, caption(res, stamp), fontsize=9, color=INK_SOFT, ha="left", va="top",
             linespacing=1.6)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=DPI, facecolor=SURFACE)
    plt.close(fig)


def draw_omics(res: dict, omics: dict, out: Path, stamp: str) -> None:
    """오믹스 점수가 있는 같은 행에서 지표 넷과 Open Targets 점수 셋의 ROC 를 그린다."""
    register_fonts()
    ev = omics["evaluation"]
    fig = plt.figure(figsize=(WIDTH_IN, HEIGHT_IN), dpi=DPI, facecolor=SURFACE)
    ax = fig.add_axes([0.10, 0.17, 0.86, 0.68], facecolor=SURFACE)
    ax.plot([0, 1], [0, 1], color=AXIS, lw=1, zorder=1)
    ax.text(0.83, 0.79, "무작위", color=MUTED, fontsize=9, rotation=45, ha="center", va="center")
    blocks = ev["metrics_same_rows"]
    for key in sorted(blocks, key=lambda k: -(blocks[k]["auc"] or 0)):
        color, label, ls = SERIES[key]
        pts = blocks[key]["roc"]
        ax.plot([p[0] for p in pts], [p[1] for p in pts], color=color, lw=2, ls=ls,
                solid_capstyle="round", dash_capstyle="round", zorder=3,
                label=f"{label}  AUC {blocks[key]['auc']:.3f}")
    for key, (name, ls) in OMICS_STYLE.items():
        b = ev["scores"].get(key)
        if not b or b["auc"] is None:
            continue
        pts = b["roc"]
        ax.plot([p[0] for p in pts], [p[1] for p in pts], color=INK, lw=2, ls=ls, zorder=4,
                label=f"{name}  AUC {b['auc']:.3f} ({b['n_used']:,}쌍)")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.set_aspect("equal")
    ax.set_xlabel("1 - 특이도(라벨 부재 쌍 중 신호로 잡힌 비율)", color=INK_SOFT, fontsize=10.5)
    ax.set_ylabel("민감도(라벨 기재 쌍 중 신호로 잡힌 비율)", color=INK_SOFT, fontsize=10.5)
    ax.grid(color=GRID, lw=1)
    ax.set_axisbelow(True)
    ax.tick_params(colors=MUTED, labelsize=9, length=0)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(AXIS)
    leg = ax.legend(loc="lower right", fontsize=9.5, frameon=True, framealpha=1,
                    edgecolor=GRID, facecolor=SURFACE, labelcolor=INK, handlelength=2.6,
                    title="지표(색)와 Open Targets 기전 점수(먹색)", title_fontsize=9.5)
    leg.get_title().set_color(INK_SOFT)
    fig.text(0.10, 0.962, "기전 타당성 점수와 불균형 지표의 라벨 기재 판별 (같은 행)", fontsize=15,
             color=INK, weight="bold", ha="left")
    fig.text(0.10, 0.930, f"Open Targets {omics['source']['data_version']} 표적-질환 연관 점수의 표적별 최댓값. "
             "문헌 제외는 Europe PMC 가중치 0(API) 또는\nliterature 외 유형 최댓값(로컬). "
             "두 문헌 제외 곡선이 서로 겹치면 점선이 일점쇄선 밑에 가려진다.",
             fontsize=10, color=INK_SOFT, ha="left", va="top", linespacing=1.4)
    ex = ev["excluded"]
    text = (f"참조 세트: SIDER 4.1 라벨 PT 대 FAERS 웨어하우스 {res['warehouse_asof']}, 전체 {ev['n_rows_total']:,}쌍 가운데 "
            f"오믹스 점수가 있는 {ev['n_rows_evaluated']:,}쌍(양성 {ev['n_positive']:,}, 음성 {ev['n_negative']:,}).\n"
            f"제외: 약 미해석 {ex['drug_unresolved']:,}, 약 표적 없음 {ex['drug_no_targets']:,}, "
            f"PT 매핑 없음(라벨 정확 일치 실패) {ex['pt_unmapped']:,}. 지표 곡선도 같은 행에서 다시 쟀다.\n"
            f"라벨 기재 예측이며 인과성 아님. 실행일 {stamp}(한국 표준시 기준).")
    fig.text(0.10, 0.10, text, fontsize=9, color=INK_SOFT, ha="left", va="top", linespacing=1.6)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=DPI, facecolor=SURFACE)
    plt.close(fig)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--results", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--jev", type=Path, action="append", default=[],
                    help="metric_validation_jev.py 결과. 여러 번 줄 수 있다")
    ap.add_argument("--omics", type=Path, default=None,
                    help="omics_plausibility.py --refset 결과. 주면 같은 행 기준 오믹스 그림을 그린다")
    args = ap.parse_args(argv)
    res = json.loads(args.results.read_text())
    if args.omics:
        raw = args.omics.read_bytes()
        omics = json.loads(gzip.decompress(raw) if args.omics.suffix == ".gz" else raw)
        stamp = args.omics.name.split(".")[0].rsplit("_", 1)[-1]
        out = args.out or FIG_DIR / f"metric_roc_omics_{stamp}.png"
        draw_omics(res, omics, out, stamp)
        print(out)
        return 0
    jev = []
    for path in args.jev:
        doc = json.loads(path.read_text())
        if not doc.get("jev"):
            print(f"{path}: 확률이 없어 건너뛴다")
            continue
        jev.append(doc)
    stamp = args.results.stem.rsplit("_", 1)[-1]
    out = args.out or FIG_DIR / f"metric_roc_{stamp}.png"
    draw(res, out, stamp, jev)
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
