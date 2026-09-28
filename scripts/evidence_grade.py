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

# Reference pairs chosen to land in different grades. If they ever come out the same, the
# rules have stopped discriminating and need to be looked at.
#
# The last two are the same drug on purpose. Isotretinoin's label carries a causality caveat
# under 5.8 Hearing Impairment and none under 5.10 Inflammatory Bowel Disease, so the pair
# that reads the caveat should be capped at C while the other is free to reach B. Run them
# together and a regression in subsection resolution shows up as both landing on C.
DEFAULT_PAIRS = [
    ("CLOZAPINE", "NEUTROPENIA"),
    ("NIRAPARIB", "THROMBOCYTOPENIA"),
    ("ISOTRETINOIN", "INFLAMMATORY BOWEL DISEASE"),
    ("ISOTRETINOIN", "HEARING IMPAIRMENT"),
]

# Label sections in descending order of regulatory weight.
SECTION_WEIGHT = {"boxed_warning": 3, "warnings_and_precautions": 2, "adverse_reactions": 1}

# A disclaimer counts only when it is about this reaction, and getting that wrong has bitten
# this script twice.
#
# First bite: nearly every US label opens its adverse-reactions section with boilerplate
# saying voluntary reports cannot establish causality. Reading that as reaction-specific
# graded every pair the same, so the boilerplate is excluded by name and the patterns are
# searched only in the warning sections.
#
# Second bite, found by the pharmacist on the team: the warning sections themselves are a
# list of numbered subsections, one reaction each. Searching the whole section meant the
# caveat under isotretinoin's 5.8 Hearing Impairment ("Mechanism(s) and causality for this
# reaction have not been established") was applied to inflammatory bowel disease in 5.10,
# to depression in 5.3, and to every other reaction of that drug. The grade then said more
# about which drug it was than about the pair. So the search now runs inside the subsection
# that actually names the reaction, and nowhere else.
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
    "D": "불충분. 신호가 서지 않는다",
}


# PLR labels number their subsections ("5.10 Inflammatory Bowel Disease"), and each one
# covers a single reaction. Splitting on that numbering is what keeps one reaction's caveat
# from leaking onto its neighbours.
SUBSECTION_HEAD = re.compile(r"(?:(?<=\s)|^)(\d{1,2}\.\d{1,2})\s+(?=[A-Z])")


def split_subsections(text: str) -> list[tuple[str, str]]:
    """Split a label section into (number, body) pairs, in document order.

    Older non-PLR labels have no numbering. They come back as a single unnumbered block so
    that the caller still behaves sensibly, just without subsection resolution.
    """
    marks = list(SUBSECTION_HEAD.finditer(text))
    if not marks:
        return [("", text)]
    parts = []
    for i, mark in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        parts.append((mark.group(1), text[mark.end():end]))
    return parts


def find_disclaimer(text: str, reaction: str) -> tuple[str, str] | None:
    """Find a causality caveat that belongs to this reaction. Returns (subsection, quote).

    The reaction has to be named in the same subsection as the caveat. Matching is the plain
    case-insensitive substring used elsewhere for label mentions, so the two agree on what
    counts as the reaction appearing in a label.
    """
    for number, body in split_subsections(text):
        if reaction.lower() not in body.lower():
            continue
        for pattern in DISCLAIMERS:
            found = re.search(pattern, body)
            if not found:
                continue
            window = body[max(0, found.start() - 200):found.end() + 40]
            if BOILERPLATE.search(window):
                continue      # the generic section preamble, not about this reaction
            return number, " ".join(window[-260:].split())
    return None


def label_evidence(drug: str, reaction: str) -> dict:
    """Read the label for this reaction: which sections mention it, and any causality caveat."""
    mentions = dmed.find_label_mentions(drug, reaction)
    sections = mentions.get("mentioned_sections") or []
    weight = max((SECTION_WEIGHT.get(s, 0) for s in sections), default=0)

    disclaimer = None
    disclaimer_where = None
    setids = [c.get("setid") for c in mentions.get("labels_checked") or [] if c.get("setid")]
    if setids:
        full = dmed.fetch_label_sections(setids[0])
        for name in DISCLAIMER_SECTIONS:
            text = (full.get("sections") or {}).get(name, "")
            if not text:
                continue
            hit = find_disclaimer(text, reaction)
            if hit:
                number, disclaimer = hit
                disclaimer_where = f"{name} {number}".strip()
                break

    return {"labeled": mentions.get("labeled"), "sections": sections,
            "section_weight": weight, "causality_disclaimer": disclaimer,
            "disclaimer_section": disclaimer_where,
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

    if label.get("causality_disclaimer"):
        where = label.get("disclaimer_section") or "라벨"
        reasons.append(f"라벨이 인과 미확립을 명시 ({where})")

    papers = literature.get("total_count")
    if isinstance(papers, int) and papers > 0:
        reasons.append(f"문헌 {papers}편")
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
    return {"grade": letter, "meaning": GRADES[letter], "reasons": reasons, "gaps": gaps}


def assess(drug: str, reaction: str) -> dict:
    signal = ofda.faers_disproportionality(drug, reaction)
    label = label_evidence(drug, reaction)
    literature = pubmed.search_pubmed(drug, reaction, retmax=5)
    verdict = grade(signal, label, literature)
    return {
        "drug": drug, "reaction": reaction,
        "signal": {k: signal.get(k) for k in
                   ("counts", "prr", "prr_ci95", "ror", "ror_ci95", "chi2_yates",
                    "evans_signal", "ror_signal")},
        "label": label,
        "literature": {"total_count": literature.get("total_count"),
                       "pmids": (literature.get("pmids") or [])[:5]},
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
