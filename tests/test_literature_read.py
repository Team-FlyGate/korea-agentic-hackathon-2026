"""문헌 판독 스크립트의 파싱과 집계 테스트. 네트워크와 Jev 를 부르지 않는다.

실행: .venv/bin/python -m pytest tests/test_literature_read.py -q

efetch XML 두 편을 캐시 디렉터리에 심고, 검색 도구와 Jev 호출은 가짜로 바꿔 끼운다.
보는 것은 넷이다. XML 에서 초록 전문을 읽는지, relevant 가 0.5 미만인 초록이 집계에서 빠지는지,
dry-run 산출 JSON 의 모양, 판독 파일이 있을 때 등급 근거 줄이 붙고 등급 글자는 그대로인지.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str):
    """스크립트는 패키지가 아니므로 파일 경로로 불러온다(test_bench_triage_scale 과 같은 방식)."""
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


lr = _load("literature_read")
eg = sys.modules["evidence_grade"]      # literature_read 가 불러 둔 같은 모듈

ARTICLE_CASE = """<?xml version="1.0"?>
<PubmedArticleSet>
 <PubmedArticle><MedlineCitation Status="MEDLINE" Owner="NLM"><PMID Version="1">30000001</PMID>
  <Article PubModel="Print"><Journal><Title>Journal of oncology pharmacy practice</Title>
   <JournalIssue><PubDate><Year>2021</Year><Month>Mar</Month></PubDate></JournalIssue></Journal>
   <ArticleTitle>Severe thrombocytopenia after starting a PARP inhibitor: a case report.</ArticleTitle>
   <Abstract>
    <AbstractText Label="INTRODUCTION">We describe one patient.</AbstractText>
    <AbstractText Label="CASE REPORT">Platelets recovered after the drug was held.</AbstractText>
   </Abstract>
   <PublicationTypeList><PublicationType>Case Reports</PublicationType></PublicationTypeList>
  </Article></MedlineCitation></PubmedArticle>
</PubmedArticleSet>"""

ARTICLE_COHORT = """<?xml version="1.0"?>
<PubmedArticleSet>
 <PubmedArticle><MedlineCitation Status="MEDLINE" Owner="NLM"><PMID Version="1">30000002</PMID>
  <Article PubModel="Print-Electronic"><Journal><Title>Gynecologic oncology</Title>
   <JournalIssue><PubDate><MedlineDate>2019 Nov-Dec</MedlineDate></PubDate></JournalIssue></Journal>
   <ArticleTitle>Hematologic toxicity in a retrospective cohort.</ArticleTitle>
   <Abstract><AbstractText>A cohort of treated patients with platelet counts.</AbstractText></Abstract>
   <PublicationTypeList><PublicationType>Journal Article</PublicationType></PublicationTypeList>
  </Article></MedlineCitation></PubmedArticle>
</PubmedArticleSet>"""


def fake_jev(relevant: float, design: str, dechallenge: float, rechallenge: float,
             score: float) -> dict:
    """systemone 의 성공 응답 모양. 필드는 eval/results/jev_triage_probe.json 의 실제 응답을 따랐다."""
    return {"status": 200, "seconds": 0.3, "request_id": "req-test",
            "body": {"model": "jev-test", "usage": {"input_tokens": 900, "output_tokens": 80},
                     "answers": {
                         "relevant": {"type": "noul", "noul": relevant},
                         "design": {"type": "choice", "choice": design, "confidence": 0.8},
                         "dechallenge": {"type": "noul", "noul": dechallenge},
                         "rechallenge": {"type": "noul", "noul": rechallenge},
                         "causal_conclusion": {"type": "score", "score": score,
                                               "confidence": 0.7}}}}


def _seed(tmp_path, monkeypatch):
    monkeypatch.setenv(lr.CACHE_ENV, str(tmp_path / "cache"))
    folder = lr.cache_dir()
    (folder / "30000001.xml").write_text(ARTICLE_CASE, encoding="utf-8")
    (folder / "30000002.xml").write_text(ARTICLE_COHORT, encoding="utf-8")
    monkeypatch.setattr(lr.pubmed, "search_pubmed", lambda d, r, retmax=20: {
        "term": f'{d} AND "{r}"', "total_count": 92, "pmids": ["30000001", "30000002"],
        "errors": []})

    def no_network(*_a, **_k):
        raise AssertionError("캐시에 있는 초록인데 네트워크를 불렀다")
    monkeypatch.setattr(lr, "http_get", no_network)


def test_parse_article_reads_full_abstract_with_section_labels():
    a = lr.parse_article(ARTICLE_CASE)
    assert a["pmid"] == "30000001" and a["year"] == 2021
    assert a["abstract"] == ("INTRODUCTION: We describe one patient. "
                             "CASE REPORT: Platelets recovered after the drug was held.")
    assert a["publication_types"] == ["Case Reports"]
    b = lr.parse_article(ARTICLE_COHORT)
    assert b["year"] == 2019 and b["title"] == "Hematologic toxicity in a retrospective cohort."
    assert lr.parse_article("<not xml") is None


def test_relevant_filter_and_counts(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    replies = {"30000001": fake_jev(0.9, "case_report", 0.8, 0.1, 3.2),
               "30000002": fake_jev(0.2, "observational", 0.9, 0.9, 4.0)}
    monkeypatch.setattr(lr.jev, "systemone",
                        lambda state, q, **k: replies[state["pmid"]])
    pair = lr.read_pair("NIRAPARIB", "THROMBOCYTOPENIA", dry_run=False, jev_cache={},
                        model="jev-test", timeout=1)
    assert pair["total_count"] == 92
    assert pair["read_n"] == 2 and pair["relevant_n"] == 1
    # 관련 없음으로 걸러진 두 번째 초록의 답은 저장은 되지만 어느 집계에도 들어가지 않는다.
    assert pair["by_design"] == {"case_report": 1}
    assert pair["dechallenge_n"] == 1 and pair["rechallenge_n"] == 0
    assert pair["conclusion_hist"] == {"probable": 1}
    stored = pair["abstracts"][1]["jev"]
    assert stored["answers"]["design"] == "observational"
    assert stored["request_id"] == "req-test" and stored["model"] == "jev-test"
    assert stored["confidences"]["design"] == 0.8


def test_failed_call_is_none_not_zero(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    monkeypatch.setattr(lr.jev, "systemone",
                        lambda *a, **k: {"status": 0, "seconds": 0.0, "error": "키 없음"})
    pair = lr.read_pair("NIRAPARIB", "THROMBOCYTOPENIA", dry_run=False, jev_cache={},
                        model="jev-test", timeout=1)
    assert pair["read_n"] == 0 and pair["by_design"] == {}
    assert pair["abstracts"][0]["jev"]["answers"]["relevant"] is None


def test_dry_run_json_shape(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)

    def no_jev(*_a, **_k):
        raise AssertionError("dry-run 인데 Jev 를 불렀다")
    monkeypatch.setattr(lr.jev, "systemone", no_jev)
    out = tmp_path / "literature_read_test.json"
    monkeypatch.setattr(sys, "argv", ["literature_read.py", "--dry-run", "--out", str(out),
                                      "--pair", "niraparib", "thrombocytopenia"])
    assert lr.main() == 0
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["dry_run"] is True and set(doc["questions"]) == {
        "relevant", "design", "dechallenge", "rechallenge", "causal_conclusion"}
    pair = doc["pairs"][0]
    assert (pair["drug"], pair["reaction"]) == ("NIRAPARIB", "THROMBOCYTOPENIA")
    for key in ("total_count", "read_n", "by_design", "dechallenge_n", "rechallenge_n",
                "conclusion_hist", "abstracts"):
        assert key in pair
    assert pair["fetched_n"] == 2 and pair["read_n"] == 0
    assert [r["jev"] for r in pair["abstracts"]] == [None, None]
    assert not out.with_suffix(".json.tmp").exists()


def test_evidence_grade_shows_designs_and_keeps_letter(monkeypatch):
    signal = {"evans_signal": True, "prr": 38.0, "counts": {"a": 400}}
    label = {"section_weight": 2}
    monkeypatch.setattr(eg.ofda, "faers_disproportionality", lambda d, r: signal)
    monkeypatch.setattr(eg, "label_evidence", lambda d, r: label)
    monkeypatch.setattr(eg.pubmed, "search_pubmed",
                        lambda d, r, retmax=5: {"total_count": 92, "pmids": ["1"]})

    monkeypatch.setattr(eg, "literature_read_for", lambda d, r: None)
    plain = eg.assess("NIRAPARIB", "THROMBOCYTOPENIA")
    assert "문헌 92편" in plain["reasons"] and "read_n" not in plain["literature"]

    monkeypatch.setattr(eg, "literature_read_for", lambda d, r: {
        "read_n": 20, "by_design": {"case_report": 12, "observational": 5}})
    read = eg.assess("NIRAPARIB", "THROMBOCYTOPENIA")
    assert "문헌 92편, 판독 20편: 증례보고 12, 관찰연구 5" in read["reasons"]
    assert read["literature"]["by_design"] == {"case_report": 12, "observational": 5}
    assert read["grade"] == plain["grade"] == "B"
