"""Jev 비교 팔 스크립트의 테스트. 네트워크를 쓰지 않는다.

실행: .venv/bin/python -m pytest tests/test_metric_validation_jev.py -q

가짜 Jev 호출이 정해 둔 확률을 돌려준다. blind 팔이 이름을 가리는지, dry-run 이 비용 세 줄을 찍고
네트워크로 나가지 않는지, 결과 JSON 의 모양, 실패한 호출이 None 으로 남는지, Jev AUC 가
metric_validation 의 AUC 와 같은지를 본다.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str):
    """스크립트는 패키지가 아니므로 파일 경로로 불러온다."""
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


mj = _load("metric_validation_jev")
mv = mj.mv

HEADER = ["drug", "pt", "a", "b", "c", "d", "expected", "prr", "prr_lo", "prr_hi", "ror", "ror_lo",
          "ror_hi", "chi2_yates", "ic", "ic025", "evans_signal", "ror_signal", "ic_signal"]
# 두 약, 쌍 여덟 개. 양성 넷, 음성 넷. 확률은 양성이 대체로 높게 두되 하나는 뒤집어 AUC 가 1 이 아니게 한다.
PAIRS = [
    ("DRUGX", "nausea", "positive", 0.9), ("DRUGX", "rash", "positive", 0.4),
    ("DRUGX", "fatigue", "negative", 0.5), ("DRUGX", "cough", "negative", 0.2),
    ("DRUGY", "nausea", "negative", 0.3), ("DRUGY", "headache", "positive", 0.8),
    ("DRUGY", "rash", "negative", 0.1), ("DRUGY", "cough", "positive", 0.7),
]
PROB = {(d, p): pr for d, p, _, pr in PAIRS}


def _write_inputs(tmp: Path) -> tuple[Path, Path]:
    refset = {"meta": {"seed": 7, "null_repeats": 3, "warehouse": {"asof": "2026Q2"}},
              "rows": [{"drug": d, "pt": p, "class": c, "source": "t"} for d, p, c, _ in PAIRS]}
    rpath = tmp / "refset.json"
    rpath.write_text(json.dumps(refset))
    lines = ["\t".join(HEADER)]
    for i, (d, p, _, _) in enumerate(PAIRS):
        a = 3 + i
        vals = [d, p, a, 100, 200, 10000, 1.5, 1.0 + i, 0.5, 2.0, 1.1 + i, 0.6, 2.1, 4.0 + i,
                0.1 * i, -0.5 + 0.1 * i, "False", "True" if i % 2 else "False", "False"]
        lines.append("\t".join(str(v) for v in vals))
    ppath = tmp / "pairs.tsv"
    ppath.write_text("\n".join(lines) + "\n")
    return rpath, ppath


def _fake_call(fail: set[tuple[str, str]] | None = None, seen: list | None = None):
    """state 의 이름으로 정해 둔 확률을 돌려주는 가짜 systemone. fail 에 든 쌍은 HTTP 500 을 돌려준다."""
    fail = fail or set()

    def call(state, questions, *, model, timeout):
        if seen is not None:
            seen.append(state)
        key = (state["drug"], state["reaction"])
        if key in fail:
            return {"status": 500, "seconds": 0.1, "error": "boom", "request_id": "r-fail"}
        return {"status": 200, "seconds": 0.2, "request_id": f"r-{key[0]}-{key[1]}",
                "body": {"model": "jev-test-1", "usage": {"input_tokens": 120, "output_tokens": 5},
                         "answers": {"needs_human": {"noul": PROB[key]}}}}
    return call


def test_state_blind_swaps_names_and_keeps_numbers():
    row = {"drug": "DRUGX", "pt": "nausea", "a": 5, "b": 10, "c": 20, "d": 1000,
           "prr": 2.5, "ror": 2.7, "chi2_yates": 9.0}
    named = mj.pair_state(row, blind=False)
    blind = mj.pair_state(row, blind=True)
    assert (named["drug"], named["reaction"]) == ("DRUGX", "nausea")
    assert (blind["drug"], blind["reaction"]) == ("DRUG_A", "REACTION_1")
    assert blind["contingency_2x2"] == {"a": 5, "b": 10, "c": 20, "d": 1000}
    assert blind["measures"] == {"PRR": 2.5, "ROR": 2.7, "chi_square_yates": 9.0}
    assert blind["reports_with_this_reaction"] == 5
    assert "reaction_in_product_label" not in blind      # 라벨 여부는 맞힐 대상이라 보내지 않는다


def test_dry_run_prints_cost_and_never_calls(tmp_path, capsys):
    rpath, ppath = _write_inputs(tmp_path)
    out = tmp_path / "dry.json"

    def boom(*_a, **_k):
        raise AssertionError("dry-run 에서 네트워크를 불렀다")

    rc = mj.main(["--refset", str(rpath), "--pairs-tsv", str(ppath), "--dry-run", "--limit", "5",
                  "--out", str(out)], call=boom)
    assert rc == 0
    text = capsys.readouterr().out
    assert "쌍 수: 5" in text
    assert "예상 입력 토큰:" in text and "/ 4 의 어림" in text
    assert "예상 비용: USD" in text
    doc = json.loads(out.read_text())
    assert doc["dry_run"] is True and doc["n_pairs"] == 5 and doc["jev"] is None
    assert all(p["probability"] is None for p in doc["pairs"])
    assert doc["estimate"]["input_tokens"] > 0


def test_live_run_requires_yes(tmp_path, capsys):
    rpath, ppath = _write_inputs(tmp_path)
    calls: list = []
    rc = mj.main(["--refset", str(rpath), "--pairs-tsv", str(ppath), "--out",
                  str(tmp_path / "x.json")], call=_fake_call(seen=calls))
    assert rc == 2 and not calls
    assert "--yes" in capsys.readouterr().out
    assert not (tmp_path / "x.json").exists()


def test_live_schema_failed_call_none_and_auc_matches(tmp_path, monkeypatch):
    rpath, ppath = _write_inputs(tmp_path)
    monkeypatch.setenv("TYPESAFE_API_KEY", "fake")
    out = tmp_path / "live.json"
    cache = tmp_path / "cache.json"
    failed = ("DRUGY", "rash")
    rc = mj.main(["--refset", str(rpath), "--pairs-tsv", str(ppath), "--yes", "--out", str(out),
                  "--jev-cache", str(cache)], call=_fake_call(fail={failed}))
    assert rc == 0
    doc = json.loads(out.read_text())
    for key in ("arm", "blind", "question", "model_requested", "n_pairs", "n_with_probability",
                "estimate", "seconds", "jev", "pairs", "warehouse_asof", "seed"):
        assert key in doc
    assert doc["arm"] == "novel" and doc["n_pairs"] == 8 and doc["n_with_probability"] == 7
    rows = {(p["drug"], p["pt"]): p for p in doc["pairs"]}
    bad = rows[failed]
    assert bad["probability"] is None and bad["http_status"] == 500 and bad["error"] == "boom"
    good = rows[("DRUGX", "nausea")]
    assert good["probability"] == 0.9 and good["request_id"] == "r-DRUGX-nausea"
    assert good["model"] == "jev-test-1" and good["seconds"] == 0.2 and good["input_tokens"] == 120

    kept = [(d, p, c, pr) for d, p, c, pr in PAIRS if (d, p) != failed]
    s = np.array([pr for *_, pr in kept])
    y = np.array([c == "positive" for _, _, c, _ in kept])
    jev = doc["jev"]
    assert jev["auc"] == mv.auc(s, y)
    assert jev["auc_inverted"] == mv.auc(-s, y)
    assert jev["n_used"] == 7 and jev["n_positive"] == 4 and jev["n_negative"] == 3
    assert jev["null"]["repeats"] == 3 and jev["null"]["auc"]["n"] >= 1
    assert set(jev["metrics_auc_same_rows"]) == set(mv.METRICS)
    assert jev["roc"][0][:2] == [0.0, 0.0] and jev["roc"][-1][:2] == [1.0, 1.0]

    # 성공 응답만 캐시에 남고, 다시 돌리면 캐시에서 재생해 네트워크를 부르지 않는다.
    assert len(json.loads(cache.read_text())) == 7
    calls: list = []
    rc = mj.main(["--refset", str(rpath), "--pairs-tsv", str(ppath), "--dry-run", "--out",
                  str(tmp_path / "replay.json"), "--jev-cache", str(cache)],
                 call=_fake_call(seen=calls))
    assert rc == 0 and not calls
    assert json.loads((tmp_path / "replay.json").read_text())["jev"]["auc"] == jev["auc"]


def test_blind_arm_sends_placeholders(tmp_path, monkeypatch):
    rpath, ppath = _write_inputs(tmp_path)
    monkeypatch.setenv("TYPESAFE_API_KEY", "fake")
    seen: list = []

    def call(state, questions, *, model, timeout):
        seen.append(state)
        return {"status": 200, "seconds": 0.1, "request_id": "r",
                "body": {"answers": {"needs_human": {"noul": 0.5}}}}

    rc = mj.main(["--refset", str(rpath), "--pairs-tsv", str(ppath), "--arm", "blind", "--yes",
                  "--limit", "3", "--out", str(tmp_path / "b.json")], call=call)
    assert rc == 0 and len(seen) == 3
    assert {(s["drug"], s["reaction"]) for s in seen} == {("DRUG_A", "REACTION_1")}
