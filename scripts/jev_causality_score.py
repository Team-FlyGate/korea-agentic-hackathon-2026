#!/usr/bin/env python3
"""Score one adverse-event report with the Korean causality algorithm, using Jev for the items.

Why this exists
---------------
The team decided to put the Korean causality algorithm (version 2.0) into the system. The
algorithm has eight items, each with fixed options and fixed points. Our first Jev call asked
only three questions, and only one of them was an algorithm item, because that call was about
confirming the request format.

This asks all eight at once. Jev reads the shared state once and evaluates every question in
parallel, so eight items cost one call, and since output tokens are free the extra cost is
only the longer prompt.

What the model does and does not decide
---------------------------------------
The model picks an option per item. It does not add the points up: the mapping from option to
score and from total to grade is arithmetic, and arithmetic belongs in code where it can be
checked. That split is the same one the critic uses, deterministic where possible and a model
only where judgment is unavoidable.

The honest part of the output is the count of items the record could not support. Public FAERS
carries no narrative, so past history, non-drug factors and specific tests are usually absent,
and an absent item scores zero rather than being guessed at.

Usage:
  python3 scripts/jev_causality_score.py                       # a cached example report
  python3 scripts/jev_causality_score.py --report-id 13497451
  python3 scripts/jev_causality_score.py --dry-run             # show the request, call nothing
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from harness.tools import jev_client as jev  # noqa: E402

OUT_DIR = ROOT / "eval" / "results"
FAERS = "https://api.fda.gov/drug/event.json"

# The eight items of the Korean causality assessment algorithm, version 2.0, with the options
# and points exactly as the source table prints them. Wording is kept in Korean because these
# are the option names a reviewer would recognise; translating them would invite drift.
ITEMS: dict[str, dict] = {
    "time_order": {
        "label": "시간적 선후관계",
        "question": "약물 투여와 이상사례 발현의 선후 관계에 관한 정보가 있는가?",
        "options": {"선후관계 합당": 3, "선후관계 모순": -3, "정보없음": 0},
    },
    "dechallenge": {
        "label": "감량 또는 중단",
        "question": "감량 또는 중단에 대한 정보가 있는가?",
        "options": {"감량 또는 중단 후 임상적 호전이 관찰됨": 3,
                    "감량 또는 중단과 무관한 임상경과를 보임": -2,
                    "감량 또는 중단을 시행하지 않음": 0, "정보없음": 0},
    },
    "history": {
        "label": "이상사례의 과거력",
        "question": "이전에 동일한 또는 유사한 약물로 이상사례를 경험한 적이 있는가?",
        "options": {"예": 1, "아니오": -1, "정보없음": 0},
    },
    "concomitant": {
        "label": "병용약물",
        "question": "병용약물이 이 이상사례를 설명할 수 있는가?",
        "options": {"병용약물 단독으로 유해사례를 설명할 수 없는 경우": 2,
                    "병용약물 단독으로 유해사례를 설명할 수 있는 경우": -3,
                    "의심약물과 상호작용으로 설명되는 경우": 2,
                    "병용약물에 대한 설명이 없는 경우": 0, "정보없음": 0},
    },
    "non_drug": {
        "label": "비약물요인",
        "question": "비약물요인으로 이 이상사례가 설명되는가?",
        "options": {"비약물요인으로 유해사례가 설명되지 않음": 1,
                    "비약물요인으로 유해사례가 설명됨": -1, "정보없음": 0},
    },
    "known": {
        "label": "약물에 대해 알려진 정보",
        "question": "이 약물과 이상사례의 조합이 허가사항이나 문헌에 알려져 있는가?",
        "options": {"허가사항(label, insert 등)에 반영되어 있음": 3,
                    "허가사항에 반영되어 있지 않으나 증례보고가 있었음": 2,
                    "알려진 바 없음": 0},
    },
    "rechallenge": {
        "label": "재투약",
        "question": "약물 재투여에 관한 정보가 있는가?",
        "options": {"재투약으로 동일한 유해사례가 발생함": 3,
                    "재투약으로 동일한 유해사례가 발생하지 않음": -2,
                    "재투약하지 않음": 0, "정보없음": 0},
    },
    "specific_test": {
        "label": "특이적인 검사",
        "question": "유발검사나 약물농도 검사 같은 특이적인 검사를 시행하였는가?",
        "options": {"양성": 3, "음성": -1, "결과를 알 수 없음": 0, "정보없음": 0},
    },
}

# 12 and above 확실함, 6 to 11 가능성 높음, 2 to 5 가능성 있음, 1 and below 가능성 낮음.
GRADES = ((12, "확실함"), (6, "가능성 높음"), (2, "가능성 있음"))

# Options that mean "the record does not say". Counting these is the point: a score built
# mostly from absences should not be read as a weak causal link, only as a thin report.
ABSENT_OPTIONS = {"정보없음", "결과를 알 수 없음", "병용약물에 대한 설명이 없는 경우"}


def grade_for(total: int) -> str:
    for threshold, name in GRADES:
        if total >= threshold:
            return name
    return "가능성 낮음"


def questions() -> dict[str, dict]:
    """One choice question per algorithm item, criteria naming every option."""
    out: dict[str, dict] = {}
    for key, item in ITEMS.items():
        out[key] = {
            "type": "choice",
            "instructions": (
                f"{item['question']} 판단은 주어진 state 의 값만으로 한다. "
                "state 에 없는 사실을 추측하지 않는다. 정보가 없으면 정보 없음에 해당하는 "
                "선택지를 고른다."),
            "criteria": {name: f"{item['label']} 항목의 선택지" for name in item["options"]},
        }
    return out


def fetch_report(report_id: str) -> dict:
    """Pull one individual case safety report from openFDA and keep the fields the items need."""
    url = f"{FAERS}?{urllib.parse.urlencode({'search': f'safetyreportid:{report_id}', 'limit': 1})}"
    with urllib.request.urlopen(url, timeout=40) as response:  # noqa: S310
        payload = json.loads(response.read().decode())
    record = payload["results"][0]
    drugs = record.get("patient", {}).get("drug", [])
    suspect = [d for d in drugs if str(d.get("drugcharacterization")) == "1"]
    concomitant = [d for d in drugs if str(d.get("drugcharacterization")) == "2"]

    def name(d: dict) -> str:
        return (d.get("medicinalproduct") or "").strip()

    return {
        "report_id": report_id,
        "country": record.get("occurcountry"),
        "serious": record.get("serious"),
        "patient_age_group": record.get("patient", {}).get("patientagegroup"),
        "patient_sex": record.get("patient", {}).get("patientsex"),
        "reactions": [{"term": r.get("reactionmeddrapt"),
                       "outcome": r.get("reactionoutcome")}
                      for r in record.get("patient", {}).get("reaction", [])],
        "suspect_drugs": [{"name": name(d), "start": d.get("drugstartdate"),
                           "end": d.get("drugenddate"),
                           "action_taken": d.get("actiondrug"),
                           "improved_after_withdrawal": d.get("drugadditional"),
                           "recurred_on_rechallenge": d.get("drugrecurreadministration"),
                           "indication": d.get("drugindication")}
                          for d in suspect],
        "concomitant_drugs": [name(d) for d in concomitant],
        "note": ("openFDA 공개본이다. 경과 서술과 인과성 평가 결과가 들어 있지 않다. "
                 "필드가 비어 있으면 그 사실을 아직 모른다는 뜻이다."),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report-id", default="13497451",
                    help="openFDA safetyreportid. 기본은 니라파립 혈소판감소증 사례")
    ap.add_argument("--labeled", default="true", choices=["true", "false", "unknown"],
                    help="그 반응이 허가사항에 기재돼 있는지. state 에 근거로 넣는다")
    ap.add_argument("--model", default=jev.DEFAULT_MODEL)
    ap.add_argument("--timeout", type=float, default=90.0)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    state = fetch_report(a.report_id)
    state["reaction_in_product_label"] = a.labeled
    print(f"사례 {a.report_id}: {state['country']}, 반응 "
          f"{[r['term'] for r in state['reactions']]}, 의심약물 "
          f"{[d['name'] for d in state['suspect_drugs']]}")

    if a.dry_run:
        print(json.dumps({"state": state, "questions": questions()},
                         ensure_ascii=False, indent=1))
        return 0

    result = jev.systemone(state, questions(), model=a.model, timeout=a.timeout)
    if result.get("status") != 200:
        print(f"HTTP {result.get('status')}: {str(result.get('error'))[:400]}", file=sys.stderr)
        return 1

    answers = result["body"]["answers"]
    rows, total, absent = [], 0, 0
    for key, item in ITEMS.items():
        answer = answers.get(key, {})
        choice = answer.get("choice")
        points = item["options"].get(choice)
        if points is None:                      # a choice the service did not return verbatim
            points, choice = 0, f"[읽지 못함: {choice}]"
        total += points
        if choice in ABSENT_OPTIONS:
            absent += 1
        rows.append({"item": item["label"], "choice": choice, "points": points,
                     "confidence": answer.get("confidence")})
        print(f"  {item['label']:<22} {str(choice)[:34]:<36} {points:+d}"
              f"  확신도 {answer.get('confidence')}")

    print(f"\n합계 {total:+d}점 → {grade_for(total)}")
    print(f"정보 없음으로 들어간 항목 {absent}/{len(ITEMS)}개")

    doc = {"ran_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "report_id": a.report_id, "model": result["body"].get("model"),
           "seconds": result.get("seconds"), "usage": result["body"].get("usage"),
           "state": state, "items": rows, "total": total, "grade": grade_for(total),
           "absent_items": absent, "request_id": result.get("request_id")}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"jev_causality_{a.report_id}.json"
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    print(out.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
