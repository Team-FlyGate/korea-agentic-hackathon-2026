"""근거 등급 스크립트의 라벨 단서 탐색과 D 등급 문구 테스트. 네트워크를 부르지 않는다.

실행: .venv/bin/python -m pytest tests/test_evidence_grade.py -q

2026-09-28 약사 검토에서 재현된 버그를 고정한다. 인과 미확립 문구는 청력 손상 소항목에 있는데,
수정 전 스크립트는 반응명과 상관없이 그 문구를 찾아 염증성장질환에도 붙였다. 합성 경고 절은
fetch_label_sections 가 내놓는 모양을 따라 공백이 한 칸으로 접힌 한 줄로 적는다.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str):
    """스크립트는 패키지가 아니므로 파일 경로로 불러온다(test_literature_read 와 같은 방식)."""
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


eg = _load("evidence_grade")

WARNINGS = (
    "5 WARNINGS AND PRECAUTIONS 5.9 Hearing Impairment Impaired hearing has been reported in "
    "patients taking isotretinoin. Mechanism(s) and causality for this reaction have not been "
    "established. Discontinue treatment if tinnitus or hearing impairment occurs "
    "[see Adverse Reactions (6.1)]. 5.10 Inflammatory Bowel Disease Isotretinoin has been "
    "associated with inflammatory bowel disease (including regional ileitis) in patients without "
    "a prior history of intestinal disorders. Discontinue isotretinoin immediately if abdominal "
    "pain, rectal bleeding or severe diarrhea occur [see Warnings and Precautions (5.9)]."
)


def test_disclaimer_stays_in_its_own_subsection():
    sections = {"warnings_and_precautions": WARNINGS}
    assert eg.find_disclaimer(sections, "INFLAMMATORY BOWEL DISEASE") is None
    assert eg.find_disclaimer(sections, "MYOCARDIAL INFARCTION") is None

    hit = eg.find_disclaimer(sections, "HEARING IMPAIRMENT")
    assert hit is not None
    assert hit["section"] == "warnings_and_precautions"
    assert hit["subsection"].startswith("5.9 Hearing Impairment")
    assert "causality for this reaction have not been established" in hit["text"]


def test_unnumbered_section_falls_back_to_paragraphs():
    sections = {"boxed_warning": (
        "Hearing impairment has been reported. Mechanism(s) and causality for this reaction "
        "have not been established.\nInflammatory bowel disease has been reported.")}
    assert eg.find_disclaimer(sections, "INFLAMMATORY BOWEL DISEASE") is None
    assert eg.find_disclaimer(sections, "HEARING IMPAIRMENT")["subsection"].startswith("Hearing")


def test_boilerplate_is_still_ignored():
    sections = {"warnings_and_precautions": (
        "5.1 Rash Rash was reported voluntarily from a population of uncertain size, and a causal "
        "relationship to drug exposure has not been established.")}
    assert eg.find_disclaimer(sections, "RASH") is None


def test_label_listed_pair_without_signal_is_not_called_insufficient():
    out = eg.grade({"evans_signal": False, "ror_signal": False, "prr": 1.1, "counts": {"a": 3}},
                   {"section_weight": 2}, {"total_count": 4})
    assert out["grade"] == "D"
    assert out["label_listed_no_signal"] is True
    assert "허가사항 기재(임상 근거 있음), 신호 해당 없음" in out["reasons"]
    assert "신호 미성립" in out["meaning"] and "불충분" not in out["meaning"]

    unlisted = eg.grade({"evans_signal": False, "prr": 1.1}, {"section_weight": 0}, {})
    assert unlisted["grade"] == "D" and unlisted["label_listed_no_signal"] is False
