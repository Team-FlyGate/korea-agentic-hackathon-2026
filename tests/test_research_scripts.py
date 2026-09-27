"""오늘 만든 조사 스크립트 셋의 계산부 테스트. 네트워크를 부르지 않는다.

실행: .venv/bin/python -m pytest tests/test_research_scripts.py -q

대상은 `scripts/` 의 세 파일이다. 카탈로그 표를 뜯는 부분, FAERS 질의를 만드는 부분,
OpenFold3 요청 본문을 만드는 부분을 본다. 실제 호출은 각 스크립트를 직접 돌려 확인하고
그 결과는 `eval/results/` 에 남는다. 여기서는 호출 없이 도는 부분만 고정한다.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def load(name: str):
    """scripts/ 의 파일을 모듈로 읽는다. 패키지가 아니라 경로로 불러온다."""
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------------------
# NVIDIA 스킬 카탈로그
# --------------------------------------------------------------------------------------
CATALOG_SAMPLE = """
| Product | Description | Skills |
|---------|-------------|--------|
| **BioNeMo NIMs** | OpenFold2 predicts single-chain protein structures. | [`bionemo-msa-structure-prediction-pipeline`](skills/a), [`bionemo-openfold2-nim`](skills/b) |
| **DOCA** | BlueField DPUs and ConnectX NICs. | [`doca-flow`](skills/c) |
| **RAG Blueprint** | Deploy and configure RAG. | [`rag-blueprint`](skills/d) |
평범한 줄은 무시한다
"""


def test_catalog_table_is_parsed_into_products_and_skills():
    m = load("fetch_nvidia_skills")
    rows = m.parse_catalog(CATALOG_SAMPLE)
    assert [r["product"] for r in rows] == ["BioNeMo NIMs", "DOCA", "RAG Blueprint"]
    assert rows[0]["skills"] == ["bionemo-msa-structure-prediction-pipeline", "bionemo-openfold2-nim"]
    assert len(rows[1]["skills"]) == 1


def test_network_gear_is_excluded_and_bio_is_kept():
    """카탈로그 380개 가운데 우리와 무관한 쪽을 거르는 것이 이 함수의 역할이다."""
    m = load("fetch_nvidia_skills")
    rel = [r["skill"] for r in m.relevant(m.parse_catalog(CATALOG_SAMPLE))]
    assert "bionemo-openfold2-nim" in rel
    assert "rag-blueprint" in rel
    assert "doca-flow" not in rel, "네트워크 장비 스킬이 후보에 남으면 안 된다"


# --------------------------------------------------------------------------------------
# FAERS 약물 조회
# --------------------------------------------------------------------------------------
def test_faers_scan_counts_on_the_larger_of_generic_and_brand(monkeypatch):
    """성분명과 상품명 가운데 건수가 큰 쪽으로 상위 이상사례를 센다."""
    m = load("faers_drug_scan")
    calls: list[dict] = []

    def fake_get(params):
        calls.append(params)
        if "count" in params:
            return {"results": [{"term": "HEADACHE", "count": 12},
                                {"term": "NAUSEA", "count": 7}]}
        total = 5 if "brand_name" in params["search"] else 1
        return {"meta": {"results": {"total": total}}}

    monkeypatch.setattr(m, "get", fake_get)
    monkeypatch.setattr(m.time, "sleep", lambda *_: None)
    row = m.scan("LEQEMBI", top=2)

    assert row["generic_total"] == 1
    assert row["brand_total"] == 5
    assert row["counted_on"] == "brand_name", "큰 쪽인 상품명으로 세어야 한다"
    assert [r["term"] for r in row["top_reactions"]] == ["HEADACHE", "NAUSEA"]
    assert calls[-1]["count"] == "patient.reaction.reactionmeddrapt.exact"


def test_faers_scan_treats_404_as_zero(monkeypatch):
    """openFDA 는 결과 없음을 404 로 준다. 오류로 세면 약물을 잘못 탈락시킨다."""
    m = load("faers_drug_scan")
    import urllib.error

    def raise_404(*_a, **_k):
        raise urllib.error.HTTPError("u", 404, "Not Found", {}, None)

    monkeypatch.setattr(m.urllib.request, "urlopen", raise_404)
    assert m.get({"search": "x"})["meta"]["results"]["total"] == 0


# --------------------------------------------------------------------------------------
# OpenFold3 요청 본문
# --------------------------------------------------------------------------------------
def test_openfold3_payload_always_carries_a_non_empty_msa(monkeypatch, tmp_path):
    """MSA 필드가 비면 서비스가 422 를 낸다. 히트가 없어도 질의 서열을 넣어야 한다."""
    m = load("openfold3_smoke")
    seen: dict = {}

    def fake_post(url, payload, key, timeout):
        seen["url"] = url
        seen["payload"] = payload
        return 200, {"outputs": [{"structures_with_scores": []}]}, 0.1

    monkeypatch.setattr(m, "post", fake_post)
    monkeypatch.setattr(m, "sequence_from_rcsb", lambda *_: "MKSKLPKP")
    monkeypatch.setattr(m, "OUT_DIR", tmp_path)
    monkeypatch.setenv("NVIDIA_API_KEY", "테스트용")

    monkeypatch.setattr(sys, "argv", ["openfold3_smoke.py", "--no-msa"])
    m.main()

    mol = seen["payload"]["inputs"][0]["molecules"][0]
    assert mol["msa"], "MSA 필드가 비어 있으면 422 가 난다"
    alignment = mol["msa"]["uniref30"]["a3m"]["alignment"]
    assert alignment.startswith(">"), "a3m 은 머리글로 시작한다"
    assert "MKSKLPKP" in alignment
    assert seen["url"].endswith("/openfold3/predict")
