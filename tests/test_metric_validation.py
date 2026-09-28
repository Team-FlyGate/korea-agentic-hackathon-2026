"""지표 성능 스크립트의 테스트. 네트워크가 없고 numpy 와 pandas 만 쓴다.

실행: .venv/bin/python -m pytest tests/test_metric_validation.py -q

손으로 셀 수 있는 작은 경우로 AUC(완전 분리, 뒤집힘, 동점), 문턱 한 곳의 민감도와 특이도, ROC
끝점, NaN 행 처리, 귀무 뒤섞기의 시드 재현성을 본다.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str):
    """스크립트는 패키지가 아니므로 파일 경로로 불러온다."""
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


mv = _load("metric_validation")


def test_auc_perfect_reversed_and_ties():
    y = np.array([1, 1, 0, 0])
    assert mv.auc(np.array([4.0, 3.0, 2.0, 1.0]), y) == 1.0
    assert mv.auc(np.array([1.0, 2.0, 3.0, 4.0]), y) == 0.0
    assert mv.auc(np.array([5.0, 5.0, 5.0, 5.0]), y) == 0.5
    # 양성 3,2 대 음성 2,1: 네 비교 중 셋은 이기고 하나는 동점이라 3.5/4.
    assert mv.auc(np.array([3.0, 2.0, 2.0, 1.0]), y) == 0.875
    assert mv.auc(np.array([1.0, 2.0]), np.array([1, 1])) is None


def test_sensitivity_specificity_at_threshold():
    scores = np.array([3.0, 2.0, 0.5, 2.5, 1.0, 0.1])
    y = np.array([1, 1, 1, 0, 0, 0])
    r = mv.at_threshold(scores, y, 2.0)
    assert (r["tp"], r["fn"], r["fp"], r["tn"]) == (2, 1, 1, 2)
    assert r["sensitivity"] == 2 / 3
    assert r["specificity"] == 2 / 3
    assert r["ppv"] == 2 / 3


def test_roc_points_endpoints_and_ties():
    pts = mv.roc_points(np.array([2.0, 2.0, 1.0]), np.array([1, 0, 0]))
    assert pts[0][:2] == [0.0, 0.0]
    assert pts[-1][:2] == [1.0, 1.0]
    # 동점 2.0 은 한 점으로 묶인다: (1-특이도 0.5, 민감도 1.0).
    assert pts[1][:2] == [0.5, 1.0]
    assert len(pts) == 3


def _frame() -> pd.DataFrame:
    rows = []
    rng = np.random.default_rng(0)
    for d in ["A", "B", "C", "D"]:
        for k in range(8):
            label = k < 3
            rows.append({"drug": d, "pt": f"pt{k}", "class": "positive" if label else "negative",
                         "label": label, "a": 3 + k})
    f = pd.DataFrame(rows)
    for m in mv.METRICS:
        f[m] = rng.normal(size=len(f)) + f["label"] * 1.0
    f.loc[0, "ic"] = np.nan
    for r in mv.FIXED_RULES:
        f[r] = f["prr"] > 0.5
    return f


def test_null_shuffle_reproducible_and_positive_sized():
    f = _frame()
    first = mv.null_positive_masks(f, seed=11, repeats=3)
    again = mv.null_positive_masks(f, seed=11, repeats=3)
    for x, y in zip(first, again):
        assert np.array_equal(x["mask"], y["mask"])
        assert x["n_null_positive"] == int(f["label"].sum())
    assert [x["seed"] for x in first] == [11, 12, 13]


def test_evaluate_counts_nan_and_fixed_rules():
    f = _frame()
    res = mv.evaluate(f, seed=5, repeats=4)
    assert res["metrics"]["ic"]["n_nan_excluded"] == 1
    assert res["metrics"]["ic"]["n_used"] == len(f) - 1
    assert res["metrics"]["prr"]["n_nan_excluded"] == 0
    rule = res["fixed_rules"]["evans_signal"]
    expected = mv.confusion((f["prr"] > 0.5).to_numpy(), f["label"].to_numpy())
    assert rule["sensitivity"] == expected["sensitivity"]
    assert res["fixed_rules"]["grade_signal"]["sensitivity"] == expected["sensitivity"]
    assert res["null"]["auc"]["prr"]["n"] == 4
    assert set(res["per_drug_auc"]["prr"]["by_drug"]) == {"A", "B", "C", "D"}
