"""참조 세트 생성 스크립트의 테스트. 네트워크와 DuckDB 를 쓰지 않는다.

실행: .venv/bin/python -m pytest tests/test_build_refset.py -q

작은 합성 SIDER 파일 둘(`drug_names.tsv`, `meddra_all_se.tsv.gz`)과 웨어하우스 추출 TSV 둘을
tmp_path 에 만든다. 보는 것은 넷이다. 이름 규칙이 공유 이름만 빼고 첫 글자 대문자 이름은 남기는지
(그 제외 규칙은 2026-09-28 에 없앴다), 양성과 음성이 규격대로 만들어지는지, 버린 수가 메타에
남는지, `.gz` 로 쓴 참조 세트를 지표 스크립트가 읽는지.
"""

from __future__ import annotations

import gzip
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str):
    """스크립트는 패키지가 아니므로 파일 경로로 불러온다."""
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


br = _load("build_refset")

NAMES = [
    ("CID1", "aspirin"), ("CID2", "Lantus"), ("CID3", "sodium"), ("CID4", "sodium"),
    ("CID5", "warfarin"), ("CID6", "Fe"), ("CID7", "fe"), ("CID8", "zzzdrug"),
]
# (flat, 유형, 용어). 빈 유형 행과 LLT 행은 버려져야 한다.
SE = [
    ("CID1", "PT", "Nausea"), ("CID1", "PT", "Headache"), ("CID1", "LLT", "Sick"),
    ("CID1", "", "Blood and lymphatic system disorders"),
    ("CID5", "PT", "Haemorrhage"), ("CID5", "PT", "Nausea"), ("CID5", "PT", "Rash"),
    ("CID3", "PT", "Dizziness"),
]
PAIRS = [
    ("ASPIRIN", "nausea", 10), ("ASPIRIN", "headache", 5), ("ASPIRIN", "dizziness", 4),
    ("ASPIRIN", "off label use", 50),
    ("WARFARIN", "haemorrhage", 30), ("WARFARIN", "nausea", 3), ("WARFARIN", "headache", 3),
    ("WARFARIN", "dizziness", 6), ("WARFARIN", "death", 9),
    ("SODIUM", "nausea", 7),
]
DRUG_N = [("SODIUM", 500), ("LANTUS", 20), ("ASPIRIN", 100), ("WARFARIN", 50), ("FE", 10)]


def _write_inputs(tmp_path: Path) -> dict[str, Path]:
    sider = tmp_path / "sider"
    sider.mkdir()
    (sider / "drug_names.tsv").write_text("".join(f"{a}\t{b}\n" for a, b in NAMES))
    with gzip.open(sider / "meddra_all_se.tsv.gz", "wt") as fh:
        for flat, kind, term in SE:
            fh.write(f"{flat}\tCID0{flat[3:]}\tC000\t{kind}\t{'C001' if kind else ''}\t{term}\n")
    pairs = tmp_path / "pairs.tsv.gz"
    with gzip.open(pairs, "wt") as fh:
        fh.write("drug\tpt\ta\n")
        fh.writelines(f"{d}\t{p}\t{a}\n" for d, p, a in PAIRS)
    drug_n = tmp_path / "drug_n.tsv.gz"
    with gzip.open(drug_n, "wt") as fh:
        fh.write("drug\tn_drug\n")
        fh.writelines(f"{d}\t{n}\n" for d, n in DRUG_N)
    meta = tmp_path / "export.meta.json"
    meta.write_text(json.dumps({"asof": "2026Q2", "warehouse_path": "/x/faers.duckdb",
                                "sql": {"pairs": "SELECT ..."}}))
    return {"sider": sider, "pairs": pairs, "drug_n": drug_n, "meta": meta}


def _run(tmp_path: Path, name: str = "refset.json") -> dict:
    p = _write_inputs(tmp_path)
    out = tmp_path / name
    rc = br.main(["--sider-dir", str(p["sider"]), "--pairs-tsv", str(p["pairs"]),
                  "--drug-n-tsv", str(p["drug_n"]), "--export-meta", str(p["meta"]),
                  "--top-n", "1", "--extra-drugs", "WARFARIN", "MISSINGDRUG",
                  "--seed", "7", "--out", str(out)])
    assert rc == 0
    raw = out.read_bytes()
    return json.loads(gzip.decompress(raw) if name.endswith(".gz") else raw)


def test_exclusion_counts(tmp_path):
    meta = _run(tmp_path)["meta"]
    ex = meta["exclusions"]
    assert ex["excluded_shared_names"] == 2          # SODIUM, 그리고 대소문자만 다른 Fe/fe
    assert ex["excluded_shared_flat_ids"] == 4
    assert ex["excluded_capitalized_names"] == 0     # 규칙을 없앴다. Lantus 도 남는다
    assert ex["eligible_names"] == 4                 # aspirin, Lantus, warfarin, zzzdrug
    assert ex["unmatched_in_warehouse"] == 1
    assert ex["matched_drugs"] == 3
    assert meta["extra_drugs_not_matched"] == ["MISSINGDRUG"]


def test_selection_is_top_n_plus_extra(tmp_path):
    meta = _run(tmp_path)["meta"]
    assert [(d["drug"], d["reason"]) for d in meta["drugs"]] == [
        ("ASPIRIN", "top_n"), ("WARFARIN", "extra")]
    assert meta["seed"] == 7 and meta["warehouse"]["asof"] == "2026Q2"


def test_positive_and_negative_rows(tmp_path):
    doc = _run(tmp_path)
    got = {(r["drug"], r["pt"], r["class"]) for r in doc["rows"]}
    assert got == {
        ("ASPIRIN", "nausea", "positive"), ("ASPIRIN", "headache", "positive"),
        ("ASPIRIN", "dizziness", "negative"),
        ("WARFARIN", "haemorrhage", "positive"), ("WARFARIN", "nausea", "positive"),
        ("WARFARIN", "headache", "negative"), ("WARFARIN", "dizziness", "negative"),
    }
    meta = doc["meta"]
    assert meta["dropped"]["sider_positive_without_warehouse_row"] == 1    # WARFARIN rash
    assert meta["dropped"]["warehouse_pairs_outside_sider_universe"] == 2  # off label use, death
    assert meta["dropped"]["outside_universe_examples_by_a"][0] == "off label use"
    assert meta["counts"] == {"positive": 4, "negative": 3, "prevalence": round(4 / 7, 6),
                              "negative_per_positive": 0.75}
    assert meta["sider"]["pt_universe"] == 5     # nausea, headache, haemorrhage, rash, dizziness


def test_gz_output_is_read_by_metric_validation(tmp_path):
    doc = _run(tmp_path, "refset.json.gz")
    assert len(doc["rows"]) == 7
    mv = _load("metric_validation")
    # 지표 스크립트는 지표 열이 모두 있는 추출물을 읽는다. a 를 모든 지표 값으로 쓴다.
    full = tmp_path / "pairs_full.tsv"
    cols = ["drug", "pt", "a"] + mv.METRICS + list(mv.FIXED_RULES)
    lines = ["\t".join(cols)]
    for d, pt, a in PAIRS:
        lines.append("\t".join([d, pt, str(a)] + [str(a)] * len(mv.METRICS) + ["true"] * len(mv.FIXED_RULES)))
    full.write_text("\n".join(lines) + "\n")
    out = tmp_path / "result.json"
    rc = mv.main(["--refset", str(tmp_path / "refset.json.gz"), "--pairs-tsv", str(full),
                  "--repeats", "2", "--out", str(out)])
    assert rc == 0
    res = json.loads(out.read_text())
    assert (res["n_positive"], res["n_negative"]) == (4, 3)
