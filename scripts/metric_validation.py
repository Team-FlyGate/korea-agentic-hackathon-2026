#!/usr/bin/env python3
"""파일럿 참조 세트로 불균형 지표의 민감도, 특이도, 양성예측도, ROC, AUC 를 잰다.

왜 있는가
---------
FlyVigilante 와 우리 근거 등급은 PRR, ROR, 카이제곱, IC 와 그 하한에 고정 문턱을 걸어 신호를
낸다. 그 문턱이 라벨 기재 쌍을 얼마나 골라내는지는 아직 잰 적이 없다. 이 스크립트는
`scripts/build_refset.py` 가 만든 참조 세트(양성 쌍, 음성 쌍)에 웨어하우스 지표를 붙여 그 성능을
잰다. 규격은 `.scratch/metric-validation/issues/03-refset-spec.md` 와 04 티켓이다.

무엇을 재고 무엇을 재지 않는가
-----------------------------
- 지표 일곱 개. 점추정치 prr, ror, chi2_yates, ic 와 하한 prr_lo, ror_lo, ic025. 지표마다 모든
  문턱에서 "지표 >= 문턱" 을 신호로 보고 민감도, 특이도, 양성예측도를 낸다. AUC 는 순위 통계
  (Mann-Whitney U / (양성 수 x 음성 수))로 구하고 동점은 0.5 로 센다.
- 고정 규칙 셋(evans_signal, ror_signal, ic_signal)은 웨어하우스 플래그를 그대로 읽어 한 점으로
  낸다. Evans 는 PRR>=2, 카이제곱>=4, a>=3 이다. 근거 등급이 쓰는 합(Evans 또는 ROR 신호)도
  `grade_signal` 로 한 점 더 낸다.
- 귀무 쌍 민감도 분석. 선정 약 안에서 양성 쌍의 PT 를 약 사이에 뒤섞고, 웨어하우스에 행이 있는
  쌍을 양성과 같은 수가 될 때까지 모은다. 그 쌍을 "귀무 양성" 으로, 나머지 참조 행을 음성으로 두고
  AUC 를 다시 잰다. 100회, 시드는 참조 세트의 seed+i 다.
- 약별 AUC. 보고 수 상위 약은 라벨이 두꺼워 전체 AUC 가 낙관적일 수 있어서 약마다 따로 잰다.
- 지표 값이 NaN 인 행은 그 지표에서만 빼고 수를 남긴다. 무한대는 순위상 가장 큰 값으로 남긴다.
- 양성은 라벨 기재이고 인과성이 아니다. 음성은 라벨 부재이고 "일어나지 않음" 이 아니다. 여기서
  나온 수치는 라벨 기재 예측 성능이다.

사용법:
  .venv/bin/python scripts/metric_validation.py \\
      --refset eval/refsets/pilot_sider_2026-09-28.json.gz \\
      --pairs-tsv /data/hps/assoc/private/rsc/user/ybae/tmp/metric-validation/warehouse_pairs_2026q2.tsv.gz
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "eval" / "results"

POINT_METRICS = ["prr", "ror", "chi2_yates", "ic"]
LOWER_METRICS = ["prr_lo", "ror_lo", "ic025"]
METRICS = POINT_METRICS + LOWER_METRICS
FIXED_RULES = {
    "evans_signal": "prr>=2 AND chi2_yates>=4 AND a>=3",
    "ror_signal": "ror_lo>1 AND a>=3",
    "ic_signal": "ic025>0",
}
# 관례상 쓰는 문턱. 스윕 표와 별도로 이 문턱의 성능을 따로 적는다.
CONVENTIONAL = {
    "prr": [1, 2, 3], "ror": [1, 2, 3], "chi2_yates": [4], "ic": [0],
    "prr_lo": [1], "ror_lo": [1], "ic025": [0],
}
N_SWEEP = 25          # 분위수 문턱 개수(결과 JSON 의 문턱 표)
N_ROC_POINTS = 400    # 그림용 ROC 점 상한
NULL_MAX_ROUNDS = 200


def rankdata(x: np.ndarray) -> np.ndarray:
    """평균 순위(1부터). 동점은 같은 평균 순위를 받는다."""
    order = np.argsort(x, kind="mergesort")
    xs = x[order]
    ranks = np.empty(len(x), dtype=float)
    boundaries = np.flatnonzero(np.r_[True, xs[1:] != xs[:-1], True])
    for lo, hi in zip(boundaries[:-1], boundaries[1:]):
        ranks[order[lo:hi]] = (lo + 1 + hi) / 2.0
    return ranks


def auc(scores: np.ndarray, labels: np.ndarray) -> float | None:
    """순위 통계 AUC. 양성이 음성보다 높을 확률이고 동점은 0.5 로 센다."""
    labels = labels.astype(bool)
    n_pos, n_neg = int(labels.sum()), int((~labels).sum())
    if n_pos == 0 or n_neg == 0:
        return None
    r = rankdata(scores)
    u = r[labels].sum() - n_pos * (n_pos + 1) / 2.0
    return float(u / (n_pos * n_neg))


def confusion(pred: np.ndarray, labels: np.ndarray) -> dict:
    """이진 예측의 민감도, 특이도, 양성예측도와 네 칸."""
    labels = labels.astype(bool)
    pred = pred.astype(bool)
    tp = int((pred & labels).sum())
    fp = int((pred & ~labels).sum())
    fn = int((~pred & labels).sum())
    tn = int((~pred & ~labels).sum())
    return {
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "sensitivity": tp / (tp + fn) if tp + fn else None,
        "specificity": tn / (tn + fp) if tn + fp else None,
        "ppv": tp / (tp + fp) if tp + fp else None,
    }


def at_threshold(scores: np.ndarray, labels: np.ndarray, t: float) -> dict:
    return {"threshold": float(t), **confusion(scores >= t, labels)}


def roc_points(scores: np.ndarray, labels: np.ndarray) -> list[list[float]]:
    """모든 고유 문턱의 (1-특이도, 민감도, 문턱). (0,0) 에서 (1,1) 까지."""
    labels = labels.astype(bool)
    order = np.argsort(-scores, kind="mergesort")
    s, y = scores[order], labels[order]
    last = np.r_[s[1:] != s[:-1], True]         # 같은 값 묶음의 끝에서만 점을 찍는다
    tp = np.cumsum(y)[last]
    fp = np.cumsum(~y)[last]
    fpr = fp / max((~labels).sum(), 1)
    tpr = tp / max(labels.sum(), 1)
    pts = [[0.0, 0.0, float("inf")]] + [[float(a), float(b), float(t)]
                                        for a, b, t in zip(fpr, tpr, s[last])]
    return pts


def thin(points: list[list[float]], n: int) -> list[list[float]]:
    """그림용으로 점을 줄인다. 처음과 끝은 남긴다."""
    if len(points) <= n:
        return points
    idx = np.unique(np.r_[np.linspace(0, len(points) - 1, n).round().astype(int), len(points) - 1])
    return [points[i] for i in idx]


def null_positive_masks(frame: pd.DataFrame, seed: int, repeats: int) -> list[dict]:
    """양성 쌍의 PT 를 약 사이에 뒤섞어 양성과 같은 수의 귀무 양성 마스크를 repeats 개 만든다.

    한 번 뒤섞을 때 웨어하우스에 행이 없는 (약, PT) 는 버려지므로, 같은 난수원으로 뒤섞기를 되풀이해
    새로 맞은 쌍을 더하고, 양성 수를 넘으면 마지막 회차에서 무작위로 잘라 정확히 맞춘다.
    """
    pos = frame[frame["label"]]
    drugs, pts = pos["drug"].to_numpy(), pos["pt"].to_numpy()
    index = {k: i for i, k in enumerate(zip(frame["drug"], frame["pt"]))}
    target = len(pos)
    out = []
    for i in range(repeats):
        rng = np.random.default_rng(seed + i)
        chosen: list[int] = []
        seen: set[int] = set()
        rounds = 0
        while len(chosen) < target and rounds < NULL_MAX_ROUNDS:
            rounds += 1
            shuffled = pts[rng.permutation(len(pts))]
            new = []
            for d, p in zip(drugs, shuffled):
                j = index.get((d, p))
                if j is not None and j not in seen:
                    seen.add(j)
                    new.append(j)
            need = target - len(chosen)
            if len(new) > need:
                new = list(rng.choice(np.array(new), size=need, replace=False))
            chosen += new
        mask = np.zeros(len(frame), dtype=bool)
        mask[np.array(chosen, dtype=int)] = True
        overlap = int((mask & frame["label"].to_numpy()).sum())
        out.append({"seed": seed + i, "mask": mask, "rounds": rounds,
                    "n_null_positive": int(mask.sum()), "overlap_with_positive": overlap})
    return out


def summarise(values: list[float]) -> dict:
    arr = np.array([v for v in values if v is not None], dtype=float)
    if not len(arr):
        return {"n": 0}
    return {"n": int(len(arr)), "mean": float(arr.mean()), "p2_5": float(np.percentile(arr, 2.5)),
            "p97_5": float(np.percentile(arr, 97.5)), "min": float(arr.min()),
            "median": float(np.median(arr)), "max": float(arr.max())}


def load_frame(refset: dict, pairs_tsv: Path) -> tuple[pd.DataFrame, dict]:
    rows = pd.DataFrame(refset["rows"])
    cols = ["drug", "pt", "a"] + METRICS + list(FIXED_RULES)
    pairs = pd.read_csv(pairs_tsv, sep="\t", usecols=cols, dtype={"drug": str, "pt": str},
                        keep_default_na=False, na_values=[""])
    frame = rows.merge(pairs, on=["drug", "pt"], how="left", indicator=True)
    unmatched = int((frame["_merge"] != "both").sum())
    frame = frame[frame["_merge"] == "both"].drop(columns="_merge").reset_index(drop=True)
    for r in FIXED_RULES:
        frame[r] = frame[r].astype(str).str.lower().isin(["true", "1"])
    frame["label"] = frame["class"] == "positive"
    return frame, {"refset_rows_without_warehouse_row": unmatched}


def evaluate(frame: pd.DataFrame, seed: int, repeats: int) -> dict:
    labels = frame["label"].to_numpy()
    prevalence = float(labels.mean())
    metrics = {}
    for m in METRICS:
        v = frame[m].to_numpy(dtype=float)
        ok = ~np.isnan(v)
        s, y = v[ok], labels[ok]
        q = np.unique(np.quantile(s, np.linspace(0, 1, N_SWEEP)))
        metrics[m] = {
            "kind": "point" if m in POINT_METRICS else "lower_bound",
            "n_used": int(ok.sum()), "n_nan_excluded": int((~ok).sum()),
            "n_inf": int(np.isinf(s).sum()),
            "n_positive": int(y.sum()), "n_negative": int((~y).sum()),
            "auc": auc(s, y),
            "conventional": [at_threshold(s, y, t) for t in CONVENTIONAL[m]],
            "sweep": [at_threshold(s, y, t) for t in q],
            "roc": thin(roc_points(s, y), N_ROC_POINTS),
        }
    rules = {r: {"rule": text, **confusion(frame[r].to_numpy(), labels)}
             for r, text in FIXED_RULES.items()}
    # scripts/evidence_grade.py 의 grade() 가 신호로 보는 조건. 라벨 기재 쌍이 이것을 못 넘으면 D 다
    # (라벨이 인과 미확립을 명시한 쌍은 C 지만 SIDER 에는 그 정보가 없다).
    rules["grade_signal"] = {"rule": "evans_signal OR ror_signal (scripts/evidence_grade.py grade())",
                             **confusion((frame["evans_signal"] | frame["ror_signal"]).to_numpy(), labels)}

    null_sets = null_positive_masks(frame, seed, repeats)
    null = {"repeats": repeats, "seed_first": seed, "seed_last": seed + repeats - 1,
            "n_null_positive": summarise([n["n_null_positive"] for n in null_sets]),
            "overlap_with_label_positive": summarise([n["overlap_with_positive"] for n in null_sets]),
            "rounds": summarise([n["rounds"] for n in null_sets]),
            "auc": {}}
    for m in METRICS:
        v = frame[m].to_numpy(dtype=float)
        ok = ~np.isnan(v)
        null["auc"][m] = summarise([auc(v[ok], n["mask"][ok]) for n in null_sets])

    per_drug: dict[str, dict] = {m: {"by_drug": {}} for m in METRICS}
    for drug, g in frame.groupby("drug", sort=True):
        y = g["label"].to_numpy()
        for m in METRICS:
            v = g[m].to_numpy(dtype=float)
            ok = ~np.isnan(v)
            per_drug[m]["by_drug"][drug] = {"auc": auc(v[ok], y[ok]), "n_positive": int(y[ok].sum()),
                                            "n_negative": int((~y[ok]).sum())}
    for m in METRICS:
        per_drug[m]["summary"] = summarise([d["auc"] for d in per_drug[m]["by_drug"].values()])

    return {"prevalence": prevalence, "n_rows": int(len(frame)),
            "n_positive": int(labels.sum()), "n_negative": int((~labels).sum()),
            "metrics": metrics, "fixed_rules": rules, "null": null, "per_drug_auc": per_drug}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--refset", type=Path, required=True)
    ap.add_argument("--pairs-tsv", type=Path, required=True)
    ap.add_argument("--repeats", type=int, default=None, help="귀무 반복 수. 기본은 참조 세트의 null_repeats.")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)

    raw = args.refset.read_bytes()
    refset = json.loads(gzip.decompress(raw) if args.refset.suffix == ".gz" else raw)
    meta = refset["meta"]
    frame, dropped = load_frame(refset, args.pairs_tsv)
    repeats = args.repeats if args.repeats is not None else meta["null_repeats"]
    result = evaluate(frame, meta["seed"], repeats)
    out_doc = {
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "script": "scripts/metric_validation.py",
        "caveat": "양성은 SIDER 라벨 기재, 음성은 라벨 부재다. 수치는 라벨 기재 예측 성능이며 인과성이 아니다.",
        "refset_path": str(args.refset), "refset_meta": meta,
        "warehouse_asof": meta["warehouse"]["asof"],
        "export_sql": meta["warehouse"]["export_sql"],
        "pairs_tsv": str(args.pairs_tsv),
        "dropped": {**meta["dropped"], **dropped},
        "seed": meta["seed"],
        **result,
    }
    out = args.out or OUT_DIR / f"metric_validation_{date.today().isoformat()}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(json.dumps(finite(out_doc), ensure_ascii=False, indent=1, allow_nan=False) + "\n")
    os.replace(tmp, out)
    print(f"{out}: 행 {result['n_rows']}, 양성 {result['n_positive']}, 유병률 {result['prevalence']:.4f}")
    for m in METRICS:
        print(f"  {m:11s} AUC {result['metrics'][m]['auc']:.3f}  귀무 평균 "
              f"{result['null']['auc'][m]['mean']:.3f}")
    return 0


def finite(x):
    """JSON 에 NaN 과 무한대를 쓰지 않는다. 무한대 문턱(ROC 의 첫 점)은 null 로 적는다."""
    if isinstance(x, dict):
        return {k: finite(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [finite(v) for v in x]
    if isinstance(x, float) and not np.isfinite(x):
        return None
    return x


if __name__ == "__main__":
    raise SystemExit(main())
