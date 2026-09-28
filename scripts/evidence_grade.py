#!/usr/bin/env python3
"""Grade the evidence behind one drug and reaction pair, and say what the grade rests on.

Why this exists
---------------
A reviewer reading our output needs to know how much weight a pair carries. Genetics has
ACMG classes for that; pharmacovigilance has no equivalent a tool can print. The 2026-09-28
meeting assigned the job of inventing one, and this is the first working version.

The grade is assigned by fixed rules, not by a model. Three inputs decide it, each fetched
from a source that can be re-checked:

  disproportionality  openFDA FAERS counts, PRR and ROR with confidence intervals
  label status        DailyMed: whether the reaction appears, and in which section
  literature          PubMed: how many papers pair the drug with the reaction

The label section matters more than its mere presence. A boxed warning is a regulator's
strongest statement, while an entry under adverse reactions can mean only that someone
reported it. Labels say so themselves: isotretinoin's carries "Mechanism(s) and causality for
this reaction have not been established", so this script looks for that disclaimer and refuses
to treat such a pair as established, however strong its numbers.

What the output is not
----------------------
Not a causality verdict for a patient. It grades how well the association is evidenced in
public sources, which is the question a triage queue needs answered. Every grade ships with
the reasons behind it and the gaps that remain, because a grade without its basis is exactly
the kind of claim this project exists to reject.

Usage:
  python3 scripts/evidence_grade.py                      # the three reference pairs
  python3 scripts/evidence_grade.py --pair NIRAPARIB THROMBOCYTOPENIA
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from harness.tools import pharmasignal_dailymed as dmed  # noqa: E402
from harness.tools import pharmasignal_openfda as ofda  # noqa: E402
from harness.tools import pharmasignal_pubmed as pubmed  # noqa: E402

OUT_DIR = ROOT / "eval" / "results"

# Three pairs chosen to land in different grades. If they ever come out the same, the rules
# have stopped discriminating and need to be looked at.
DEFAULT_PAIRS = [
    ("NIRAPARIB", "THROMBOCYTOPENIA"),
    ("CLOZAPINE", "NEUTROPENIA"),
    ("ISOTRETINOIN", "INFLAMMATORY BOWEL DISEASE"),
]

# Label sections in descending order of regulatory weight.
SECTION_WEIGHT = {"boxed_warning": 3, "warnings_and_precautions": 2, "adverse_reactions": 1}

# A disclaimer counts only when it is about this reaction. Nearly every US label opens its
# adverse-reactions section with boilerplate saying voluntary reports cannot establish
# causality, and treating that as reaction-specific graded every pair the same, which is how
# the bug showed itself. So the boilerplate is excluded by name and the remaining patterns are
# searched only in the warning sections, where a caveat attaches to a named reaction.
DISCLAIMERS = (
    r"[Mm]echanism\(?s?\)?[^.]{0,40}causality[^.]{0,60}not been established",
    r"causal (?:relationship|association)[^.]{0,60}(?:has |have )?not been established",
    r"causal (?:relationship|association)[^.]{0,40}not (?:been )?(?:proven|demonstrated)",
)
BOILERPLATE = re.compile(
    r"reported voluntarily from a population of uncertain size|"
    r"not always possible to reliably estimate", re.I)
DISCLAIMER_SECTIONS = ("boxed_warning", "warnings_and_precautions")

GRADES = {
    "A": "확립. 규제기관이 경고로 다룬 조합",
    "B": "개연. 라벨에 기재되고 신호도 선다",
    "C": "관찰. 신호는 있으나 인과가 확립되지 않았다",
    "D": "신호 미성립. 불균형 신호가 서지 않는다. 라벨 기재 여부와는 별개다",
}


# literature_read.py 의 설계 선택지 이름. 근거 줄에 한국어로 찍는다.
DESIGN_NAMES = {"case_report": "증례보고", "case_series": "증례군", "observational": "관찰연구",
                "randomized_trial": "무작위시험", "meta_analysis_or_review": "메타분석·리뷰",
                "mechanistic_or_preclinical": "기전·전임상", "other": "기타"}


def literature_read_for(drug: str, reaction: str) -> dict | None:
    """가장 최근 eval/results/literature_read_*.json 에서 이 쌍의 판독 집계를 찾는다. 없으면 None."""
    for path in sorted(OUT_DIR.glob("literature_read_*.json"), reverse=True):
        doc = json.loads(path.read_text(encoding="utf-8"))
        for pair in doc.get("pairs") or []:
            if (pair.get("drug"), pair.get("reaction")) == (drug, reaction):
                return pair
        return None
    return None


# 번호 붙은 소항목 제목. fetch_label_sections 는 공백을 한 칸으로 접으므로 "5.9 Hearing Impairment"
# 같은 제목이 본문 한가운데 그대로 남는다. "(5.9)" 같은 교차 참조와 "0.5 mg" 같은 용량은 앞 글자와
# 뒤따르는 대문자 조건으로 걸러 낸다. "5.2 iPLEDGE" 처럼 소문자 하나 뒤에 대문자가 오는 제목도 받는다.
SUBSECTION_HEADING = re.compile(r"(?<![\w.(\[])(\d{1,2})\.(\d{1,2})\s+(?=[A-Z]|[a-z][A-Z])")


def split_subsections(text: str) -> list[str]:
    """경고 절 텍스트를 소항목 단위로 나눈다.

    번호 제목(5.9, 5.10 등)이 있으면 그 자리에서 자른다. 절 번호가 섞여 잡히지 않도록 가장 흔한
    큰 번호(보통 5)를 가진 제목만 쓴다. 번호가 없으면 빈 줄이나 줄바꿈을 문단 경계로 삼는다.
    첫 제목 앞의 절 머리말은 따로 한 덩어리로 둔다.
    """
    heads = list(SUBSECTION_HEADING.finditer(text))
    if heads:
        majors = [m.group(1) for m in heads]
        major = max(set(majors), key=majors.count)
        cuts = [m.start() for m in heads if m.group(1) == major]
        bounds = [0, *cuts, len(text)]
        chunks = [text[s:e] for s, e in zip(bounds, bounds[1:])]
    else:
        chunks = re.split(r"\n\s*\n|\n", text)
    return [c.strip() for c in chunks if c.strip()]


def find_disclaimer(sections: dict, reaction: str) -> dict | None:
    """반응명이 나오는 소항목 안에서만 인과 미확립 문구를 찾는다.

    2026-09-28 약사 검토 전에는 경고 절 전체에서 처음 걸린 문구를 반응과 상관없이 붙였다. 그래서
    이소트레티노인 청력 손상 소항목의 단서가 염증성장질환에도, 라벨에 없는 반응에도 붙었다.
    반응명 대조는 find_label_mentions 와 같이 대소문자를 가리지 않는 문자열 검색이다.
    찾으면 {section, subsection(소항목 앞 120자), text(문구 주변)} 을, 못 찾으면 None 을 돌려준다.
    """
    term = re.compile(re.escape(reaction.strip()), re.I)
    for name in DISCLAIMER_SECTIONS:
        text = sections.get(name, "")
        if not text:
            continue
        for sub in split_subsections(text):
            if not term.search(sub):
                continue
            for pattern in DISCLAIMERS:
                found = re.search(pattern, sub)
                if not found:
                    continue
                window = sub[max(0, found.start() - 200):found.end() + 40]
                if BOILERPLATE.search(window):
                    continue          # the generic section preamble, not about this reaction
                return {"section": name, "subsection": " ".join(sub[:120].split()),
                        "text": " ".join(window[-260:].split())}
    return None


def label_evidence(drug: str, reaction: str) -> dict:
    """Read the label for this reaction: which sections mention it, and any causality caveat."""
    mentions = dmed.find_label_mentions(drug, reaction)
    sections = mentions.get("mentioned_sections") or []
    weight = max((SECTION_WEIGHT.get(s, 0) for s in sections), default=0)

    disclaimer = None
    setids = [c.get("setid") for c in mentions.get("labels_checked") or [] if c.get("setid")]
    if setids:
        full = dmed.fetch_label_sections(setids[0])
        disclaimer = find_disclaimer(full.get("sections") or {}, reaction)

    return {"labeled": mentions.get("labeled"), "sections": sections,
            "section_weight": weight,
            "causality_disclaimer": disclaimer["text"] if disclaimer else None,
            "disclaimer_subsection": disclaimer["subsection"] if disclaimer else None,
            "errors": mentions.get("errors", [])}


def grade(signal: dict, label: dict, literature: dict) -> dict:
    """Apply the fixed rules and return the grade with its reasons and its gaps."""
    reasons: list[str] = []
    gaps: list[str] = []

    has_signal = bool(signal.get("evans_signal") or signal.get("ror_signal"))
    counts = signal.get("counts") or {}
    if has_signal:
        reasons.append(f"불균형 신호 성립 (PRR {signal.get('prr')}, 보고 {counts.get('a')}건)")
    else:
        gaps.append(f"불균형 신호 미성립 (PRR {signal.get('prr')})")

    weight = label.get("section_weight", 0)
    if weight == 3:
        reasons.append("라벨 박스 경고에 기재")
    elif weight == 2:
        reasons.append("라벨 경고 및 주의사항에 기재")
    elif weight == 1:
        reasons.append("라벨 이상반응에만 기재")
    else:
        gaps.append("라벨에 기재되지 않음")

    # 라벨에 적힌 반응은 허가 심사에서 임상 근거를 거친 것이다. 신호가 서지 않았다고 해서 근거가
    # 모자라다고 부르면 틀린 말이 된다(2026-09-28 약사 검토).
    label_listed_no_signal = weight >= 1 and not has_signal
    if label_listed_no_signal:
        reasons.append("허가사항 기재(임상 근거 있음), 신호 해당 없음")

    if label.get("causality_disclaimer"):
        reasons.append("라벨이 인과 미확립을 명시")

    papers = literature.get("total_count")
    if isinstance(papers, int) and papers > 0:
        line = f"문헌 {papers}편"
        if literature.get("read_n") is not None:        # scripts/literature_read.py 의 판독 결과
            designs = ", ".join(f"{DESIGN_NAMES.get(k, k)} {n}"
                                for k, n in (literature.get("by_design") or {}).items())
            line += f", 판독 {literature['read_n']}편" + (f": {designs}" if designs else "")
        reasons.append(line)
    else:
        gaps.append("문헌을 찾지 못함")

    # The disclaimer caps the grade at C no matter how strong the numbers are. That cap is the
    # rule that keeps "it is in the label" from being read as "the drug causes it".
    if label.get("causality_disclaimer"):
        letter = "C" if has_signal or weight else "D"
    elif weight == 3 and has_signal:
        letter = "A"
    elif weight >= 1 and has_signal:
        letter = "B"
    elif has_signal:
        letter = "C"
    else:
        letter = "D"

    if letter in ("A", "B"):
        gaps.append("이 등급은 집단 수준 근거다. 개별 환자의 인과를 뜻하지 않는다")
    return {"grade": letter, "meaning": GRADES[letter], "reasons": reasons, "gaps": gaps,
            "label_listed_no_signal": label_listed_no_signal}


def assess(drug: str, reaction: str) -> dict:
    signal = ofda.faers_disproportionality(drug, reaction)
    label = label_evidence(drug, reaction)
    literature = pubmed.search_pubmed(drug, reaction, retmax=5)
    literature_fields = {"total_count": literature.get("total_count"),
                         "pmids": (literature.get("pmids") or [])[:5]}
    read = literature_read_for(drug, reaction)
    if read is not None:
        literature_fields.update(read_n=read.get("read_n"), by_design=read.get("by_design") or {})
    verdict = grade(signal, label, literature_fields)
    return {
        "drug": drug, "reaction": reaction,
        "signal": {k: signal.get(k) for k in
                   ("counts", "prr", "prr_ci95", "ror", "ror_ci95", "chi2_yates",
                    "evans_signal", "ror_signal")},
        "label": label,
        "literature": literature_fields,
        **verdict,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pair", nargs=2, action="append", metavar=("DRUG", "REACTION"))
    ap.add_argument("--out", type=Path,
                    default=OUT_DIR / f"evidence_grade_{date.today().isoformat()}.json")
    a = ap.parse_args()

    pairs = [tuple(p) for p in (a.pair or DEFAULT_PAIRS)]
    rows = []
    for drug, reaction in pairs:
        row = assess(drug.upper(), reaction.upper())
        rows.append(row)
        print(f"\n{row['drug']} + {row['reaction']}  →  등급 {row['grade']}  ({row['meaning']})")
        for reason in row["reasons"]:
            print(f"    근거  {reason}")
        for gap in row["gaps"]:
            print(f"    공백  {gap}")

    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps({"graded_at": date.today().isoformat(), "rules": GRADES,
                                 "pairs": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n{a.out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
