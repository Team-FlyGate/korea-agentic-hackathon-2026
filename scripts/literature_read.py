#!/usr/bin/env python3
"""PubMed 초록을 쌍마다 20편 받아 Jev 로 판독하고, 설계별 편수를 근거 등급에 넘긴다.

왜 있는가
---------
근거 등급(`scripts/evidence_grade.py`)의 문헌 입력은 지금 편수 하나다. 증례보고 90편과 무작위시험
2편이 같은 "문헌 92편" 으로 읽힌다. 설계(`.scratch/metric-validation/issues/07-literature-design.md`)
는 초록마다 Jev 에 다섯 질문을 한 번에 던져, 설계와 dechallenge, rechallenge, 저자의 인과 결론
강도를 받기로 했다. 이 스크립트가 그 구현이다.

무엇을 하고 무엇을 하지 않는가
-----------------------------
- PMID 와 총 편수는 `harness.tools.pharmasignal_pubmed.search_pubmed` 에서 받는다. 등급 스크립트가
  쓰는 검색과 같은 검색이다.
- 초록 전문은 E-utilities efetch XML 로 받아 PMID 하나당 파일 하나로 캐시한다. 캐시는 저장소 밖
  `$METRIC_VALIDATION_DIR/pubmed_cache/` 이고(없으면 `eval/results/pubmed_cache/`, gitignore 대상),
  `LITERATURE_PUBMED_CACHE_DIR` 로 바꾼다. 호출 간격은 검색 도구의 초당 3회 제한을 같이 쓴다.
- Jev 는 초록 하나에 한 호출, 질문 다섯 개. `relevant` 가 0.5 미만이면 나머지 답은 저장만 하고
  집계에서 뺀다. 실패한 호출은 None 으로 남기고 0 으로 채우지 않는다.
- 판독 결과는 검증 전이다. 약사 대조(티켓 10)가 끝나기 전에는 "판독 결과" 로만 읽는다.
- 등급 규칙은 바꾸지 않는다. 설계별 편수는 등급 스크립트의 근거 줄에 표시만 된다.

사용법:
  python3 scripts/literature_read.py --dry-run               # 기본 세 쌍, Jev 없이 수집만
  python3 scripts/literature_read.py --pair NIRAPARIB THROMBOCYTOPENIA
  python3 scripts/literature_read.py --jev-cache eval/results/literature_jev_cache.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from harness.tools import jev_client as jev  # noqa: E402
from harness.tools import pharmasignal_pubmed as pubmed  # noqa: E402
from harness.tools.pharmasignal_common import http_get, is_error  # noqa: E402

import evidence_grade  # noqa: E402

OUT_DIR = ROOT / "eval" / "results"
CACHE_ENV = "LITERATURE_PUBMED_CACHE_DIR"
SCRATCH_ENV = "METRIC_VALIDATION_DIR"   # 소속 스토리지 아래 스크래치. 계정 경로를 코드에 박지 않는다
DEFAULT_CACHE_DIR = (Path(os.environ[SCRATCH_ENV]) / "pubmed_cache" if os.environ.get(SCRATCH_ENV)
                     else ROOT / "eval" / "results" / "pubmed_cache")
RETMAX = 20
RELEVANT_THRESHOLD = 0.5
EFETCH_BATCH = 50

# 설계 선택지. 키는 Jev 에 보내는 영문 이름이고 값은 근거 줄에 찍는 한국어 이름이다. 순서는
# 07 티켓의 순서를 따른다. 등급 스크립트가 같은 표로 근거 줄을 만들므로 그쪽에 한 번만 둔다.
DESIGN_LABELS = evidence_grade.DESIGN_NAMES

# 저자 인과 결론 강도의 다섯 단계. 낮은 쪽부터. Jev score 는 0 부터 4 까지의 실수로 온다.
CONCLUSION_LEVELS = ["not_stated", "suspected", "possible", "probable", "established"]

# 질문 문장과 criteria 의 원문. 07 티켓 "## Answer" 의 질문 세트를 영어로 옮긴 것이다.
# 이 저장소의 Jev 질문은 영어로 쓴다(scripts/bench_triage_scale.py 의 JEV_QUESTION_SETS).
QUESTIONS: dict[str, dict] = {
    "relevant": {
        "type": "noul",
        "instructions": (
            "Does this abstract report on or study the named drug together with the named "
            "adverse reaction? Judge only from the title and abstract given."),
        "criteria": {
            "true": "The abstract discusses the named drug in connection with the named "
                    "reaction, as an adverse effect, a case, a risk estimate or a mechanism.",
            "false": "The abstract mentions the drug or the reaction only in passing, or "
                     "is about something else.",
        },
    },
    "design": {
        "type": "choice",
        "instructions": (
            "What is the study design of this paper? Judge from the abstract and the "
            "publication types given. Choose other when none fits."),
        "criteria": {
            "case_report": "A report of one patient, or of two or three patients described "
                           "individually.",
            "case_series": "A series of several patients with the reaction, described "
                           "together without a comparison group.",
            "observational": "A cohort, case-control, cross-sectional or pharmacovigilance "
                             "database study with a comparison or a denominator.",
            "randomized_trial": "A randomized controlled trial, or a pooled analysis of "
                                "randomized trials.",
            "meta_analysis_or_review": "A systematic review, meta-analysis or narrative "
                                       "review.",
            "mechanistic_or_preclinical": "A laboratory, animal or in vitro study, or a "
                                          "study of mechanism without patients.",
            "other": "None of the above, for example a guideline, an editorial or a letter "
                     "without a case.",
        },
    },
    "dechallenge": {
        "type": "noul",
        "instructions": (
            "Does the abstract report what happened to the reaction after the drug was "
            "stopped or its dose reduced?"),
        "criteria": {
            "true": "The abstract states the course of the reaction after the drug was "
                    "withdrawn or reduced, whether it improved or not.",
            "false": "The abstract does not say what happened after withdrawal or dose "
                     "reduction, or the drug was not withdrawn.",
        },
    },
    "rechallenge": {
        "type": "noul",
        "instructions": (
            "Does the abstract report that the drug was given again after the reaction, "
            "and what happened?"),
        "criteria": {
            "true": "The abstract states that the drug was restarted or given again and "
                    "reports the outcome.",
            "false": "The abstract does not report a re-administration of the drug.",
        },
    },
    "causal_conclusion": {
        "type": "score",
        "instructions": (
            "How strongly do the authors conclude that the drug causes the reaction? Rate "
            "the authors' own conclusion, not your view of the evidence."),
        "criteria": [
            "The authors state no conclusion about causation.",
            "The authors suspect a link but call it uncertain.",
            "The authors call a causal link possible.",
            "The authors call a causal link probable or likely.",
            "The authors call the causal link established or definite.",
        ],
    },
}


def cache_dir() -> Path:
    d = Path(os.environ.get(CACHE_ENV) or DEFAULT_CACHE_DIR)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _text(el: ET.Element | None) -> str:
    return " ".join("".join(el.itertext()).split()) if el is not None else ""


def parse_article(xml_text: str) -> dict[str, Any] | None:
    """PubmedArticle XML 하나(또는 그것을 담은 PubmedArticleSet)에서 판독에 쓸 필드를 읽는다.

    검색 도구의 파서는 초록을 300자로 자르므로 전문을 읽는 파서를 따로 둔다. 구획 이름
    (BACKGROUND 등)이 있으면 앞에 붙인다.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None
    art = root if root.tag == "PubmedArticle" else root.find(".//PubmedArticle")
    if art is None:
        return None
    pmid = _text(art.find("./MedlineCitation/PMID"))
    article = art.find("./MedlineCitation/Article")
    if not pmid or article is None:
        return None
    parts = []
    for block in article.findall("Abstract/AbstractText"):
        label = block.get("Label")
        body = _text(block)
        parts.append(f"{label}: {body}" if label else body)
    abstract = " ".join(p for p in parts if p)
    year_el = article.find("Journal/JournalIssue/PubDate/Year")
    year_text = _text(year_el) or _text(article.find("Journal/JournalIssue/PubDate/MedlineDate"))
    year_match = re.search(r"(19|20)\d{2}", year_text)
    return {
        "pmid": pmid,
        "title": _text(article.find("ArticleTitle")),
        "journal": _text(article.find("Journal/Title")),
        "year": int(year_match.group(0)) if year_match else None,
        "publication_types": [_text(p) for p in article.findall("PublicationTypeList/PublicationType")],
        "abstract": abstract,
    }


def fetch_abstracts(pmids: list[str]) -> tuple[dict[str, dict], list[str]]:
    """PMID 목록의 초록을 캐시에서 읽고, 없는 것만 efetch 로 받아 PMID 하나당 파일로 남긴다."""
    folder = cache_dir()
    out: dict[str, dict] = {}
    errors: list[str] = []
    missing = [p for p in pmids if not (folder / f"{p}.xml").exists()]
    for start in range(0, len(missing), EFETCH_BATCH):
        batch = missing[start:start + EFETCH_BATCH]
        url = (f"{pubmed.EUTILS}/efetch.fcgi?db=pubmed&rettype=abstract&retmode=xml"
               f"&id={','.join(batch)}&{pubmed._auth_params()}")
        # 검색 도구와 같은 간격 기록을 공유해 두 경로를 합쳐도 초당 3회를 넘지 않는다.
        payload = http_get(url, is_json=False, min_interval=pubmed.MIN_INTERVAL,
                           _last_call=pubmed._LAST_CALL)
        if is_error(payload) or not isinstance(payload, str):
            errors.append(f"efetch_error:{batch}")
            continue
        try:
            root = ET.fromstring(payload)
        except ET.ParseError:
            errors.append(f"efetch_parse_error:{batch}")
            continue
        for art in root.iter("PubmedArticle"):
            pmid = _text(art.find("./MedlineCitation/PMID"))
            if pmid:
                target = folder / f"{pmid}.xml"
                tmp = target.with_suffix(".xml.tmp")
                tmp.write_text(ET.tostring(art, encoding="unicode"), encoding="utf-8")
                tmp.replace(target)
    for pmid in pmids:
        path = folder / f"{pmid}.xml"
        parsed = parse_article(path.read_text(encoding="utf-8")) if path.exists() else None
        if parsed is None:
            errors.append(f"abstract_missing:{pmid}")
            continue
        out[pmid] = parsed
    return out, errors


def jev_state(drug: str, reaction: str, article: dict) -> dict[str, Any]:
    return {
        "drug": drug, "reaction": reaction,
        "pmid": article["pmid"], "title": article["title"],
        "publication_types": article["publication_types"],
        "abstract": article["abstract"],
        "note": "Only the PubMed title and abstract are given, not the full text.",
    }


def read_answers(result: dict[str, Any]) -> dict[str, Any]:
    """Jev 응답에서 다섯 답과 확신도를 읽는다. 읽지 못한 답은 None 이다."""
    answers_raw = (result.get("body") or {}).get("answers") or {}
    answers: dict[str, Any] = {}
    confidences: dict[str, Any] = {}
    for key, spec in QUESTIONS.items():
        raw = answers_raw.get(key)
        value = None
        if isinstance(raw, dict):
            if spec["type"] == "noul" and isinstance(raw.get("noul"), (int, float)):
                value = float(raw["noul"])
            elif spec["type"] == "choice" and raw.get("choice") in spec["criteria"]:
                value = raw["choice"]
            elif spec["type"] == "score" and isinstance(raw.get("score"), (int, float)):
                value = float(raw["score"])
        answers[key] = value
        confidences[key] = raw.get("confidence") if isinstance(raw, dict) else None
    return {"answers": answers, "confidences": confidences}


def jev_row(result: dict[str, Any]) -> dict[str, Any]:
    body = result.get("body") or {}
    return {**read_answers(result), "request_id": result.get("request_id"),
            "model": body.get("model"), "seconds": result.get("seconds"),
            "status": result.get("status"), "error": result.get("error"),
            "usage": body.get("usage")}


def conclusion_level(score: float | None) -> str | None:
    if score is None:
        return None
    index = min(max(int(round(score)), 0), len(CONCLUSION_LEVELS) - 1)
    return CONCLUSION_LEVELS[index]


def aggregate(rows: list[dict]) -> dict[str, Any]:
    """초록별 행을 쌍 수준 집계로 줄인다.

    read_n 은 relevant 답을 받은 초록 수다. relevant 가 0.5 미만인 초록은 나머지 답이 있어도
    설계와 dechallenge 집계에 넣지 않는다. 답이 None 인 항목은 어느 칸에도 세지 않는다.
    """
    read = [r["jev"] for r in rows if r.get("jev") and r["jev"]["answers"].get("relevant") is not None]
    kept = [j["answers"] for j in read if j["answers"]["relevant"] >= RELEVANT_THRESHOLD]
    by_design = Counter(a["design"] for a in kept if a.get("design"))
    conclusions = Counter(conclusion_level(a.get("causal_conclusion")) for a in kept)
    conclusions.pop(None, None)
    return {
        "fetched_n": sum(1 for r in rows if r.get("has_abstract")),
        "read_n": len(read),
        "relevant_n": len(kept),
        "by_design": {k: by_design[k] for k in DESIGN_LABELS if by_design[k]},
        "dechallenge_n": sum(1 for a in kept if (a.get("dechallenge") or 0) >= 0.5),
        "rechallenge_n": sum(1 for a in kept if (a.get("rechallenge") or 0) >= 0.5),
        "conclusion_hist": {k: conclusions[k] for k in CONCLUSION_LEVELS if conclusions[k]},
    }


def load_jev_cache(path: Path | None) -> dict[str, dict]:
    if path is None or not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def read_pair(drug: str, reaction: str, *, dry_run: bool, jev_cache: dict[str, dict],
              model: str, timeout: float) -> dict[str, Any]:
    search = pubmed.search_pubmed(drug, reaction, retmax=RETMAX)
    pmids = search.get("pmids") or []
    articles, errors = fetch_abstracts(pmids)
    rows = []
    for pmid in pmids:
        article = articles.get(pmid)
        row: dict[str, Any] = {"pmid": pmid, "title": article["title"] if article else None,
                               "year": article["year"] if article else None,
                               "publication_types": article["publication_types"] if article else [],
                               "has_abstract": bool(article and article["abstract"]),
                               "jev": None}
        key = f"{drug}|{reaction}|{pmid}"
        if not row["has_abstract"] or (dry_run and key not in jev_cache):
            rows.append(row)
            continue
        result = jev_cache.get(key)
        if result is None:
            result = jev.systemone(jev_state(drug, reaction, article), QUESTIONS,
                                   model=model, timeout=timeout)
            if result.get("status") == 200:
                jev_cache[key] = result
        row["jev"] = jev_row(result)
        rows.append(row)
    return {"drug": drug, "reaction": reaction, "term": search.get("term"),
            "total_count": search.get("total_count"), "pmids": pmids,
            **aggregate(rows), "errors": (search.get("errors") or []) + errors,
            "abstracts": rows}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pair", nargs=2, action="append", metavar=("DRUG", "REACTION"))
    ap.add_argument("--dry-run", action="store_true",
                    help="Jev 를 부르지 않는다. 캐시에 있는 응답만 쓴다")
    ap.add_argument("--jev-cache", type=Path,
                    help="Jev 원응답 JSON. 있으면 재생하고, 새로 받은 성공 응답을 덧붙인다")
    ap.add_argument("--model", default=jev.DEFAULT_MODEL)
    ap.add_argument("--timeout", type=float, default=90.0)
    ap.add_argument("--out", type=Path,
                    default=OUT_DIR / f"literature_read_{date.today().isoformat()}.json")
    a = ap.parse_args()

    pairs = [(d.upper(), r.upper()) for d, r in (a.pair or evidence_grade.DEFAULT_PAIRS)]
    jev_cache = load_jev_cache(a.jev_cache)
    before = len(jev_cache)
    results = []
    for drug, reaction in pairs:
        pair = read_pair(drug, reaction, dry_run=a.dry_run, jev_cache=jev_cache,
                         model=a.model, timeout=a.timeout)
        results.append(pair)
        designs = ", ".join(f"{DESIGN_LABELS[k]} {n}" for k, n in pair["by_design"].items())
        print(f"{drug} + {reaction}: 문헌 {pair['total_count']}편, 초록 {pair['fetched_n']}편, "
              f"판독 {pair['read_n']}편, 관련 {pair['relevant_n']}편"
              + (f" ({designs})" if designs else ""))
        for err in pair["errors"]:
            print(f"    오류  {err}")

    if a.jev_cache and len(jev_cache) > before:
        tmp = a.jev_cache.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(jev_cache, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(a.jev_cache)

    doc = {"ran_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "dry_run": a.dry_run, "model": None if a.dry_run else a.model,
           "retmax": RETMAX, "relevant_threshold": RELEVANT_THRESHOLD,
           "cache_dir": str(cache_dir()), "questions": QUESTIONS,
           "design_labels": DESIGN_LABELS, "conclusion_levels": CONCLUSION_LEVELS,
           "note": "판독 결과는 약사 대조 전이다(티켓 10).", "pairs": results}
    a.out.parent.mkdir(parents=True, exist_ok=True)
    tmp = a.out.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(a.out)
    print(a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
