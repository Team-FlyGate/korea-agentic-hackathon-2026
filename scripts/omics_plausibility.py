#!/usr/bin/env python3
"""약 → 작용기전 표적 → 표적-질환 연관을 Open Targets 에서 조회해 쌍마다 기전 타당성 근거를 만든다.

왜 있는가
---------
불균형 지표와 라벨과 문헌은 "보고되었는가" 를 본다. 이 스크립트는 "생물학적으로 그럴 수 있는가" 를
조회한 데이터베이스 근거로 매긴다. 설계는 `.scratch/omics-plausibility/issues/03-source-choice.md`
의 "## Answer" 이고, 이 파일이 04 티켓의 세 쌍 시제품이다. `evidence_grade.py` 와는 아직 잇지 않는다.

무엇을 하고 무엇을 하지 않는가
-----------------------------
- 소스는 Open Targets Platform GraphQL 하나다(`POST https://api.platform.opentargets.org/api/v4/graphql`).
  실행마다 `meta { apiVersion dataVersion }` 를 한 번 받아 근거 ID 의 버전으로 쓴다.
- 약 이름은 `search(entityNames:["drug"])` 결과 가운데 이름이 입력과 대소문자 무시로 정확히 같은 hit 만
  쓴다. 없으면 ChEMBL REST `molecule.json?pref_name__iexact=` 로 한 번 더 찾고, 그래도 없으면 표적 없음으로
  적는다. 표적은 `drug.mechanismsOfAction` 의 행에 나온 유전자를 모두 쓴다.
- PT 는 `search(entityNames:["disease"])` 의 첫 결과만 보고, 그 이름이 PT 와 대소문자 무시로 정확히 같을
  때만 매핑으로 인정한다(match "label"). 아니면 매핑 없음이고, 첫 결과는 참고로만 남긴다(match "none").
  동의어 일치와 상위·하위 용어 확장은 하지 않는다.
- 연관은 표적마다 `associatedDiseases(Bs:[질환 ID])` 로 받는다. 같은 요청 안에서 별칭 두 개로 기본
  점수와 literature 제외 점수를 함께 받는다. literature 제외는 API 인자 `datasources` 에 Europe PMC
  (`europepmc`) 의 가중치를 0 으로 준 것이다. Open Targets 문서(`platform-docs.opentargets.org/evidence`)
  는 Literature 데이터 유형의 소스로 Europe PMC 하나만 적고, 연관 응답의 `datasourceScores` 에서 그 ID 가
  `europepmc` 로 온다. 이 인자를 주면 다른 소스만 있는 연관의 점수도 조금 바뀌는 것을 관찰했으므로
  (가중치 정규화로 보이며 확인하지 않음), 로컬 규칙으로 계산한 점수도 함께 남긴다. 로컬 규칙은
  "literature 가 아닌 데이터 유형 점수(`datatypeScores`)의 최댓값, 없으면 0" 이다.
- 모든 HTTP 응답은 요청 본문의 sha1 을 이름으로 한 파일로 `$OMICS_DIR/opentargets/`(ChEMBL 은
  `$OMICS_DIR/chembl/`) 아래에 캐시한다. `OMICS_DIR` 이 없으면 `eval/results/omics_cache/`(gitignore
  대상)를 쓴다. 성공한 응답만 캐시하고, 캐시가 있으면 다시 호출하지 않는다. 단, 버전 기록용 `meta` 는
  실시간 실행에서 매번 새로 받고 캐시된 값과 다르면 경고를 남긴다.
- 실패한 호출은 점수를 None 으로 두고 오류를 적는다. 0 으로 채우지 않는다. 호출은 성공했지만 연관 행이
  없으면(count 0) 점수는 0.0 이고 `row_found` 가 False 다.
- 근거는 라벨 기재 예측용 기전 근거다. 개별 사례의 인과를 말하지 않는다.

참조 세트 묶음 모드(`--refset`, 05 티켓)
---------------------------------------
쌍마다 조회하면 호출이 수만 회가 되므로 묶는다. 약 해석은 쌍 모드와 같다. PT 는 한 요청에 `search`
별칭 50개씩, 연관은 표적마다 `associatedDiseases(page:{size:3000})` 목록 전체를 쪽 넘김으로 받고 같은
요청의 literature 제외 별칭과 질환 ID 로 맞춘 뒤, 로컬에서 행마다 조인한다. 평가는 약이 해석되고 표적이
있고 PT 가 매핑된 행에서 `metric_validation.py` 의 AUC, 문턱 스윕, 귀무(시드 seed+i) 함수를 불러 세
점수와 지표 넷(prr, ror_lo, ic025, chi2_yates, 같은 행)에 적용한다. 지표 값은 참조 세트 옆
`<이름>_pairs.tsv.gz` 에서 읽는다. 출력은 `eval/results/omics_plausibility_refset_<date>.json` 이다.

날짜와 시간대
-------------
출력 파일 이름의 날짜는 `date.today()` 이므로 프로세스 시간대를 따른다. 팀 기준(KST)으로 맞추려면
`TZ=Asia/Seoul` 을 주고 실행한다.

사용법:
  TZ=Asia/Seoul OMICS_DIR=<캐시 폴더> python3 scripts/omics_plausibility.py        # 기본 세 쌍
  python3 scripts/omics_plausibility.py --pair NIRAPARIB THROMBOCYTOPENIA --dry-run
  TZ=Asia/Seoul OMICS_DIR=<캐시 폴더> .venv/bin/python scripts/omics_plausibility.py \\
      --refset eval/refsets/pilot_sider_2026-09-28.json.gz --sleep 0.2
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import evidence_grade  # noqa: E402

OT_URL = "https://api.platform.opentargets.org/api/v4/graphql"
CHEMBL_URL = "https://www.ebi.ac.uk/chembl/api/data"
USER_AGENT = "FlyGate-omics-plausibility/0.1 (Korea Agentic AI Hackathon; stdlib urllib)"
CACHE_ENV = "OMICS_DIR"
OUT_DIR = ROOT / "eval" / "results"
DEFAULT_CACHE_ROOT = ROOT / "eval" / "results" / "omics_cache"
TIMEOUT = 60

LITERATURE_DATATYPE = "literature"
# literature 제외 점수에 쓰는 API 인자. 근거는 모듈 설명의 문서와 응답을 볼 것.
NO_LITERATURE_DATASOURCES = [{"id": "europepmc", "weight": 0.0, "propagate": True}]
NO_LITERATURE_RULE_LOCAL = "max(datatypeScores[id != 'literature'].score), 행이나 해당 유형이 없으면 0.0"
CAVEAT = ("라벨 기재 예측용 기전 근거이며, 개별 사례에서 약이 반응을 일으켰다는 인과 근거가 아니다.")

Q_META = "query Meta { meta { apiVersion { x y z } dataVersion { year month iteration } } }"
Q_DRUG_SEARCH = ("query DrugSearch($q: String!) { search(queryString: $q, entityNames: [\"drug\"], "
                 "page: {index: 0, size: 10}) { hits { id name entity } } }")
Q_DRUG_MOA = ("query DrugMechanisms($id: String!) { drug(chemblId: $id) { id name mechanismsOfAction "
              "{ rows { actionType mechanismOfAction targets { id approvedSymbol } } } } }")
Q_DISEASE_SEARCH = ("query DiseaseSearch($q: String!) { search(queryString: $q, entityNames: "
                    "[\"disease\"], page: {index: 0, size: 5}) { hits { id name entity score } } }")
Q_ASSOC = ("query TargetDiseaseAssociation($id: String!, $bs: [String!], "
           "$noLit: [DatasourceSettingsInput!]) { target(ensemblId: $id) { id approvedSymbol "
           "all: associatedDiseases(Bs: $bs) { count rows { disease { id name } score "
           "datatypeScores { id score } datasourceScores { id score } } } "
           "noLiterature: associatedDiseases(Bs: $bs, datasources: $noLit) { count rows "
           "{ disease { id } score } } } }")


def cache_root() -> Path:
    return Path(os.environ[CACHE_ENV]) if os.environ.get(CACHE_ENV) else DEFAULT_CACHE_ROOT


def _send(url: str, body: bytes | None) -> Any:
    """HTTP 요청 한 번. body 가 있으면 JSON POST, 없으면 GET. 파싱한 JSON 을 돌려주고 실패는 예외.

    테스트는 이 함수를 바꿔 끼워 네트워크 없이 돈다.
    """
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers,
                                 method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc


class Fetcher:
    """캐시를 거쳐 요청을 보내고, 요청마다 기록을 남긴다.

    기록은 연산 이름, 변수(또는 URL), 캐시 파일 경로, 캐시 적중 여부, 오류다. 성공한 응답만 캐시에
    쓴다. dry_run 이면 캐시에 없는 요청은 보내지 않고 오류 "not_cached" 로 적는다.
    """

    def __init__(self, *, dry_run: bool, sleep: float, root: Path | None = None):
        self.dry_run = dry_run
        self.sleep = sleep
        self.root = root or cache_root()
        self.http_calls = 0
        self.cache_hits = 0
        self.failures = 0
        self._last = 0.0

    def _call(self, source: str, key_text: str, url: str, body: bytes | None,
              record: dict, *, refresh: bool = False) -> Any:
        folder = self.root / source
        path = folder / f"{hashlib.sha1(key_text.encode('utf-8')).hexdigest()}.json"
        record["cache_path"] = str(path)
        record["from_cache"] = False
        record["error"] = None
        cached = None
        if path.exists():
            cached = json.loads(path.read_text(encoding="utf-8"))
            if not refresh or self.dry_run:
                self.cache_hits += 1
                record["from_cache"] = True
                return cached["response"]
        if self.dry_run:
            record["error"] = "not_cached"
            self.failures += 1
            return None
        wait = self.sleep - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self.http_calls += 1
        self._last = time.monotonic()
        try:
            payload = _send(url, body)
        except Exception as exc:  # 네트워크, HTTP, 파싱 오류를 모두 기록하고 None 으로 둔다
            record["error"] = f"{type(exc).__name__}: {exc}"
            self.failures += 1
            return None
        if isinstance(payload, dict) and payload.get("errors"):
            record["error"] = "graphql_errors: " + json.dumps(payload["errors"])[:500]
            self.failures += 1
            return None
        if cached is not None and cached.get("response") != payload:
            record["cache_changed"] = True
        folder.mkdir(parents=True, exist_ok=True)
        entry = {"url": url, "request": key_text,
                 "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 "response": payload}
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(entry, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
        return payload

    def graphql(self, operation: str, query: str, variables: dict | None,
                *, refresh: bool = False) -> tuple[dict | None, dict]:
        body = json.dumps({"operationName": operation, "query": query, "variables": variables},
                          sort_keys=True, ensure_ascii=False)
        record: dict[str, Any] = {"source": "opentargets", "operation": operation,
                                  "variables": variables}
        payload = self._call("opentargets", body, OT_URL, body.encode("utf-8"), record,
                             refresh=refresh)
        data = payload.get("data") if isinstance(payload, dict) else None
        if payload is not None and data is None and record["error"] is None:
            record["error"] = "no_data"
        return data, record

    def rest(self, operation: str, url: str) -> tuple[Any, dict]:
        record: dict[str, Any] = {"source": "chembl", "operation": operation, "url": url}
        payload = self._call("chembl", url, url, None, record)
        return payload, record


def data_version_string(meta: dict) -> str | None:
    dv = (meta or {}).get("dataVersion") or {}
    if not dv.get("year") or not dv.get("month"):
        return None
    parts = [dv["year"], dv["month"]] + ([str(dv["iteration"])] if dv.get("iteration") else [])
    return ".".join(parts)


def api_version_string(meta: dict) -> str | None:
    av = (meta or {}).get("apiVersion") or {}
    return ".".join(av[k] for k in ("x", "y", "z")) if all(av.get(k) for k in ("x", "y", "z")) else None


def fetch_meta(f: Fetcher) -> dict[str, Any]:
    """실행마다 한 번 버전을 받는다. 실시간 실행에서는 캐시를 새로 고치고 바뀌었으면 표시한다."""
    data, rec = f.graphql("Meta", Q_META, None, refresh=True)
    meta = (data or {}).get("meta")
    return {"api_version": api_version_string(meta), "data_version": data_version_string(meta),
            "raw": meta, "request": rec,
            "cache_changed": bool(rec.get("cache_changed"))}


def chembl_version(f: Fetcher, requests: list[dict]) -> str | None:
    payload, rec = f.rest("ChemblStatus", f"{CHEMBL_URL}/status.json")
    requests.append(rec)
    return payload.get("chembl_db_version") if isinstance(payload, dict) else None


def resolve_drug(f: Fetcher, drug: str) -> dict[str, Any]:
    """약 이름을 ChEMBL ID 로 풀고 작용기전 표적을 받는다."""
    requests: list[dict] = []
    out: dict[str, Any] = {"query": drug, "id": None, "name": None, "match": None,
                           "chembl_version": None, "targets": None, "error": None,
                           "requests": requests}
    data, rec = f.graphql("DrugSearch", Q_DRUG_SEARCH, {"q": drug})
    requests.append(rec)
    if rec["error"]:
        out["error"] = f"DrugSearch: {rec['error']}"
        return out
    hits = ((data.get("search") or {}).get("hits")) or []
    exact = [h for h in hits if (h.get("name") or "").casefold() == drug.casefold()]
    if exact:
        out.update(id=exact[0]["id"], name=exact[0]["name"], match="name_exact")
    else:
        url = (f"{CHEMBL_URL}/molecule.json?pref_name__iexact={urllib.parse.quote(drug)}"
               "&only=molecule_chembl_id,pref_name")
        payload, crec = f.rest("ChemblMoleculeByName", url)
        requests.append(crec)
        if crec["error"]:
            out["error"] = f"ChemblMoleculeByName: {crec['error']}"
            return out
        mols = [m for m in (payload or {}).get("molecules") or []
                if (m.get("pref_name") or "").casefold() == drug.casefold()]
        if not mols:
            out["match"] = "none"
            out["targets"] = []
            return out
        out.update(id=mols[0]["molecule_chembl_id"], name=mols[0]["pref_name"],
                   match="chembl_pref_name", chembl_version=chembl_version(f, requests))
    data, rec = f.graphql("DrugMechanisms", Q_DRUG_MOA, {"id": out["id"]})
    requests.append(rec)
    if rec["error"]:
        out["error"] = f"DrugMechanisms: {rec['error']}"
        return out
    drug_node = data.get("drug")
    if drug_node is None:
        out["targets"] = []
        out["error"] = "drug_not_in_opentargets"
        return out
    targets: dict[str, dict] = {}
    for row in ((drug_node.get("mechanismsOfAction") or {}).get("rows")) or []:
        for t in row.get("targets") or []:
            entry = targets.setdefault(t["id"], {"symbol": t.get("approvedSymbol"), "ensembl": t["id"],
                                                 "actionType": [], "mechanism": []})
            for key, value in (("actionType", row.get("actionType")),
                               ("mechanism", row.get("mechanismOfAction"))):
                if value and value not in entry[key]:
                    entry[key].append(value)
    out["targets"] = [{**t, "actionType": "; ".join(t["actionType"]) or None,
                       "mechanism": "; ".join(t["mechanism"]) or None} for t in targets.values()]
    return out


def resolve_pt(f: Fetcher, pt: str) -> dict[str, Any]:
    """PT 를 Open Targets 질환 ID 로 매핑한다. 첫 결과의 이름이 정확히 같을 때만 인정한다."""
    data, rec = f.graphql("DiseaseSearch", Q_DISEASE_SEARCH, {"q": pt})
    out: dict[str, Any] = {"query": pt, "mapped": None, "first_hit": None, "error": None,
                           "requests": [rec]}
    if rec["error"]:
        out["error"] = f"DiseaseSearch: {rec['error']}"
        return out
    hits = ((data.get("search") or {}).get("hits")) or []
    if not hits:
        return out
    first = hits[0]
    label = (first.get("name") or "").casefold() == pt.casefold()
    out["first_hit"] = {"id": first.get("id"), "name": first.get("name"),
                        "score": first.get("score"), "match": "label" if label else "none"}
    if label:
        out["mapped"] = {"id": first["id"], "name": first["name"], "match": "label"}
    return out


def no_literature_local(datatype_scores: list[dict]) -> float:
    rest = [d["score"] for d in datatype_scores or [] if d.get("id") != LITERATURE_DATATYPE]
    return float(max(rest)) if rest else 0.0


def _float(x: Any) -> float | None:
    """API 가 점수 0 을 정수로 주는 경우가 있어 실수로 맞춘다. None 은 그대로 둔다."""
    return None if x is None else float(x)


def fetch_association(f: Fetcher, target: dict, disease_id: str) -> dict[str, Any]:
    variables = {"id": target["ensembl"], "bs": [disease_id], "noLit": NO_LITERATURE_DATASOURCES}
    data, rec = f.graphql("TargetDiseaseAssociation", Q_ASSOC, variables)
    out: dict[str, Any] = {"symbol": target["symbol"], "ensembl": target["ensembl"],
                           "disease_id": disease_id, "count": None, "row_found": None,
                           "score": None, "score_no_literature": None,
                           "score_no_literature_local": None, "datatypeScores": None,
                           "datasourceScores": None, "error": None, "request": rec}
    if rec["error"]:
        out["error"] = rec["error"]
        return out
    node = data.get("target")
    if node is None:
        out["error"] = "target_not_in_opentargets"
        return out
    allrows = node.get("all") or {}
    row = next((r for r in allrows.get("rows") or [] if (r.get("disease") or {}).get("id") == disease_id),
               None)
    nolit = next((r for r in (node.get("noLiterature") or {}).get("rows") or []
                  if (r.get("disease") or {}).get("id") == disease_id), None)
    out["count"] = allrows.get("count")
    out["row_found"] = row is not None
    if row is None:
        out.update(score=0.0, score_no_literature=0.0, score_no_literature_local=0.0,
                   datatypeScores=[], datasourceScores=[])
        return out
    nolit_score = nolit.get("score") if nolit else 0.0
    out.update(score=_float(row.get("score")), datatypeScores=row.get("datatypeScores") or [],
               datasourceScores=row.get("datasourceScores") or [],
               score_no_literature=_float(nolit_score),
               score_no_literature_local=no_literature_local(row.get("datatypeScores")))
    return out


def _max(values: list[float | None]) -> float | None:
    known = [v for v in values if v is not None]
    return max(known) if known else None


def aggregate(drug: dict, term: dict, associations: list[dict]) -> dict[str, Any]:
    """쌍 수준 집계. 계산하지 못한 값은 None 이다.

    표적이 없으면 has_association 은 False, 점수는 None(평가하지 않음)이다. PT 가 매핑되지 않으면
    has_association 과 점수 모두 None 이다. 표적 일부의 호출이 실패하면 나머지로 최댓값을 내고
    incomplete 를 True 로 둔다.
    """
    targets = drug.get("targets")
    has_target = None if targets is None else bool(targets)
    agg: dict[str, Any] = {"has_target": has_target,
                           "n_targets": None if targets is None else len(targets),
                           "has_association": None, "max_score": None,
                           "max_score_no_literature": None, "max_score_no_literature_local": None,
                           "incomplete": False}
    if has_target is False:
        agg["has_association"] = False
        return agg
    if not has_target or term.get("mapped") is None or not associations:
        return agg
    agg["incomplete"] = any(a["score"] is None for a in associations)
    agg["max_score"] = _max([a["score"] for a in associations])
    agg["max_score_no_literature"] = _max([a["score_no_literature"] for a in associations])
    agg["max_score_no_literature_local"] = _max([a["score_no_literature_local"] for a in associations])
    if agg["max_score"] is not None:
        agg["has_association"] = agg["max_score"] > 0
    return agg


def evidence_id(data_version: str | None, drug: str, pt: str) -> str:
    return f"omics:opentargets@{data_version or 'unknown'}:{drug.upper()}:{pt.lower()}"


def _fmt(x: float | None) -> str:
    return "없음(조회 실패)" if x is None else f"{x:.4g}"


def what_text(drug: dict, term: dict, associations: list[dict], agg: dict,
              data_version: str | None, day: str) -> str:
    """근거 ID 에 붙는 설명 문장. 표적, 용어, 점수, 조회 날짜, 해석 한계를 담는다."""
    src = f"Open Targets Platform {data_version or '버전 미상'}"
    targets = drug.get("targets")
    if targets is None:
        t_text = f"약 {drug['query']}의 표적을 조회하지 못했다({drug.get('error')})."
    elif not targets:
        t_text = f"약 {drug['query']}의 작용기전 표적을 {src}에서 찾지 못했다."
    else:
        syms = ", ".join(t["symbol"] or t["ensembl"] for t in targets)
        t_text = f"약 {drug['query']}({drug['id']})의 작용기전 표적은 {syms}이다."
    mapped = term.get("mapped")
    if mapped:
        m_text = f"반응 {term['query']}의 매핑 용어는 {mapped['id']}({mapped['name']}, 라벨 일치)이다."
    elif term.get("error"):
        m_text = f"반응 {term['query']}의 용어 매핑 조회가 실패했다({term['error']})."
    else:
        m_text = f"반응 {term['query']}에 대해 라벨이 정확히 같은 질환 용어가 없어 매핑하지 않았다."
    if associations:
        per = "; ".join(f"{a['symbol']} {_fmt(a['score'])}(literature 제외 {_fmt(a['score_no_literature'])})"
                        for a in associations)
        s_text = (f"표적별 연관 점수는 {per}이고, 최댓값은 {_fmt(agg['max_score'])}, literature 제외 "
                  f"최댓값은 {_fmt(agg['max_score_no_literature'])}이다.")
    else:
        s_text = "연관 점수는 조회하지 않았다."
    return f"{t_text} {m_text} {s_text} 조회일 {day}, 소스 {src}. {CAVEAT}"


def assess_pair(f: Fetcher, drug_name: str, pt: str, *, data_version: str | None, day: str,
                drug_memo: dict, pt_memo: dict) -> dict[str, Any]:
    if drug_name not in drug_memo:
        drug_memo[drug_name] = resolve_drug(f, drug_name)
    if pt not in pt_memo:
        pt_memo[pt] = resolve_pt(f, pt)
    drug, term = drug_memo[drug_name], pt_memo[pt]
    associations = []
    if drug.get("targets") and term.get("mapped"):
        associations = [fetch_association(f, t, term["mapped"]["id"]) for t in drug["targets"]]
    agg = aggregate(drug, term, associations)
    requests = drug["requests"] + term["requests"] + [a["request"] for a in associations]
    return {
        "drug": drug_name, "pt": pt,
        "drug_resolution": {k: drug[k] for k in ("query", "id", "name", "match", "chembl_version", "error")},
        "targets": drug.get("targets"),
        "term": {k: term[k] for k in ("query", "mapped", "first_hit", "error")},
        "associations": [{k: v for k, v in a.items() if k != "request"} for a in associations],
        **agg,
        "evidence": {"id": evidence_id(data_version, drug_name, pt),
                     "what": what_text(drug, term, associations, agg, data_version, day)},
        "requests": requests,
    }


def load_refset_pairs(path: Path) -> list[tuple[str, str]]:
    raw = path.read_bytes()
    refset = json.loads(gzip.decompress(raw) if path.suffix == ".gz" else raw)
    seen: dict[tuple[str, str], None] = {}
    for row in refset["rows"]:
        seen.setdefault((row["drug"], row["pt"]), None)
    return list(seen)


def run(pairs: list[tuple[str, str]], f: Fetcher, day: str) -> dict[str, Any]:
    meta = fetch_meta(f)
    drug_memo: dict[str, dict] = {}
    pt_memo: dict[str, dict] = {}
    rows = [assess_pair(f, d, p, data_version=meta["data_version"], day=day,
                        drug_memo=drug_memo, pt_memo=pt_memo) for d, p in pairs]
    return {"meta": meta, "pairs": rows}


# ---------------------------------------------------------------------------
# 참조 세트 묶음 모드(05 티켓)
#
# 쌍마다 조회하면 호출이 수만 회가 되므로 세 단계로 묶는다. PT 는 한 요청에 search 별칭 PT_BATCH 개,
# 연관은 표적마다 associatedDiseases 목록 전체를 쪽 넘김으로 받아 로컬에서 질환 ID 로 조인한다. 해석
# 규칙(약 이름 정확 일치, PT 첫 결과 라벨 일치, 행 없음은 0.0, 실패는 None)은 쌍 모드와 같다.
# ---------------------------------------------------------------------------

PT_BATCH = 50
ASSOC_PAGE_SIZE = 3000          # Open Targets Pagination 의 최대 size(04 인트로스펙션)
SCORE_KEYS = ["max_score", "max_score_no_literature_api", "max_score_no_literature_local"]
SAME_ROW_METRICS = ["prr", "ror_lo", "ic025", "chi2_yates"]

Q_ASSOC_DUMP = ("query TargetAssociationDump($id: String!, $page: Pagination!, "
                "$noLit: [DatasourceSettingsInput!]) { target(ensemblId: $id) { id approvedSymbol "
                "all: associatedDiseases(page: $page) { count rows { disease { id } score "
                "datatypeScores { id score } } } "
                "noLiterature: associatedDiseases(page: $page, datasources: $noLit) { count rows "
                "{ disease { id } score } } } }")


def pt_batch_query(pts: list[str]) -> tuple[str, dict[str, str]]:
    """PT 목록 하나를 별칭 search 여러 개로 묶은 질의와 변수를 만든다. 별칭은 q0, q1, ... 순서다."""
    decl = ", ".join(f"$q{i}: String!" for i in range(len(pts)))
    parts = " ".join(f"q{i}: search(queryString: $q{i}, entityNames: [\"disease\"], "
                     "page: {index: 0, size: 5}) { hits { id name entity score } }"
                     for i in range(len(pts)))
    return f"query DiseaseSearchBatch({decl}) {{ {parts} }}", {f"q{i}": p for i, p in enumerate(pts)}


def term_from_hits(pt: str, hits: list[dict] | None, error: str | None) -> dict[str, Any]:
    """검색 결과 하나를 resolve_pt 와 같은 모양의 용어 기록으로 바꾼다.

    match 는 label(첫 결과 라벨 일치, 매핑), none(첫 결과가 다름), no_hits(결과 없음),
    error(호출 실패) 가운데 하나다.
    """
    out: dict[str, Any] = {"query": pt, "mapped": None, "first_hit": None, "error": error,
                           "match": "error" if error else "no_hits"}
    if error or not hits:
        return out
    first = hits[0]
    label = (first.get("name") or "").casefold() == pt.casefold()
    out["first_hit"] = {"id": first.get("id"), "name": first.get("name"),
                        "score": first.get("score"), "match": "label" if label else "none"}
    out["match"] = "label" if label else "none"
    if label:
        out["mapped"] = {"id": first["id"], "name": first["name"], "match": "label"}
    return out


def parse_pt_batch(data: dict | None, pts: list[str], error: str | None) -> dict[str, dict]:
    """묶음 응답을 PT 별 용어 기록으로 푼다. 별칭이 빠진 PT 는 호출 실패로 적는다."""
    out = {}
    for i, pt in enumerate(pts):
        node = (data or {}).get(f"q{i}") if not error else None
        if error:
            out[pt] = term_from_hits(pt, None, error)
        elif node is None:
            out[pt] = term_from_hits(pt, None, "alias_missing")
        else:
            out[pt] = term_from_hits(pt, node.get("hits") or [], None)
    return out


def resolve_pts_batched(f: Fetcher, pts: list[str], batch: int = PT_BATCH) -> dict[str, dict]:
    terms: dict[str, dict] = {}
    for start in range(0, len(pts), batch):
        chunk = pts[start:start + batch]
        query, variables = pt_batch_query(chunk)
        data, rec = f.graphql("DiseaseSearchBatch", query, variables)
        terms.update(parse_pt_batch(data, chunk, rec["error"]))
    return terms


def merge_dump_pages(pages: list[dict]) -> dict[str, Any]:
    """표적 한 개의 쪽별 응답(target 노드)을 질환 ID 기준 점수 표로 합친다.

    두 별칭은 쪽마다 순서가 다르므로 행 순서가 아니라 질환 ID 로 맞춘다. noLiterature 쪽에 없는
    질환의 API 점수는 0.0 이다. 로컬 점수는 all 쪽의 datatypeScores 에서 literature 를 뺀 최댓값이다.
    """
    by: dict[str, dict] = {}
    nolit: dict[str, float] = {}
    count_all = count_nolit = None
    dt_present = False
    for node in pages:
        a = node.get("all") or {}
        n = node.get("noLiterature") or {}
        count_all, count_nolit = a.get("count"), n.get("count")
        for r in a.get("rows") or []:
            dts = r.get("datatypeScores")
            dt_present = dt_present or dts is not None
            by[r["disease"]["id"]] = {"score": _float(r.get("score")),
                                      "no_literature_local": no_literature_local(dts or [])}
        for r in n.get("rows") or []:
            nolit[r["disease"]["id"]] = _float(r.get("score")) or 0.0
    for did, entry in by.items():
        entry["no_literature_api"] = nolit.get(did, 0.0)
    only_nolit = sorted(set(nolit) - set(by))
    for did in only_nolit:       # all 쪽에 없고 문헌 제외에만 있는 질환. 기본 점수는 0 으로 둔다.
        by[did] = {"score": 0.0, "no_literature_local": 0.0, "no_literature_api": nolit[did]}
    return {"count_all": count_all, "count_no_literature": count_nolit, "n_rows": len(by),
            "n_only_in_no_literature": len(only_nolit), "datatype_scores_in_dump": dt_present,
            "by_disease": by}


def fetch_target_dump(f: Fetcher, target: dict, page_size: int = ASSOC_PAGE_SIZE) -> dict[str, Any]:
    """표적 한 개의 연관 목록 전체를 쪽 넘김으로 받는다. 실패하면 by_disease 가 None 이다."""
    pages: list[dict] = []
    index = 0
    out: dict[str, Any] = {"symbol": target["symbol"], "ensembl": target["ensembl"], "pages": 0,
                           "error": None}
    while True:
        variables = {"id": target["ensembl"], "page": {"index": index, "size": page_size},
                     "noLit": NO_LITERATURE_DATASOURCES}
        data, rec = f.graphql("TargetAssociationDump", Q_ASSOC_DUMP, variables)
        if rec["error"]:
            out.update(error=f"page {index}: {rec['error']}", by_disease=None)
            return out
        node = data.get("target")
        if node is None:
            out.update(error="target_not_in_opentargets", by_disease=None)
            return out
        pages.append(node)
        total = max((node.get("all") or {}).get("count") or 0,
                    (node.get("noLiterature") or {}).get("count") or 0)
        index += 1
        if index * page_size >= total:
            break
    out["pages"] = len(pages)
    out.update(merge_dump_pages(pages))
    out["pagination_ok"] = out["n_rows"] - out["n_only_in_no_literature"] == out["count_all"]
    return out


def aggregate_refset_row(drug: dict, term: dict, dumps: dict[str, dict]) -> dict[str, Any]:
    """참조 세트 한 행의 집계. 규칙은 aggregate() 와 같고 점수 출처만 표적별 연관 목록이다.

    표적의 목록 조회가 실패하면 그 표적 점수는 None 이고 incomplete 가 True 다. 목록에 매핑 질환이
    없으면 0.0 이다.
    """
    targets = drug.get("targets")
    has_target = None if targets is None else bool(targets)
    mapped = (term.get("mapped") or {}).get("id")
    agg: dict[str, Any] = {"has_target": has_target,
                           "n_targets": None if targets is None else len(targets),
                           "mapped": mapped, "has_association": None,
                           **{k: None for k in SCORE_KEYS}, "incomplete": False}
    if has_target is False:
        agg["has_association"] = False
        return agg
    if not has_target or mapped is None:
        return agg
    per = []
    for t in targets:
        by = (dumps.get(t["ensembl"]) or {}).get("by_disease")
        if by is None:
            per.append((None, None, None))
            continue
        e = by.get(mapped)
        per.append((0.0, 0.0, 0.0) if e is None
                   else (e["score"], e["no_literature_api"], e["no_literature_local"]))
    agg["incomplete"] = any(p[0] is None for p in per)
    for i, key in enumerate(SCORE_KEYS):
        agg[key] = _max([p[i] for p in per])
    if agg["max_score"] is not None:
        agg["has_association"] = agg["max_score"] > 0
    return agg


def score_block(values: np.ndarray, labels: np.ndarray, null_sets: list[dict]) -> dict[str, Any]:
    """점수 하나의 AUC, 문턱 스윕, ROC, 귀무 AUC. 계산은 metric_validation 의 함수를 그대로 쓴다."""
    mv = _metric_validation()
    ok = ~np.isnan(values)
    s, y = values[ok], labels[ok]
    q = np.unique(np.quantile(s, np.linspace(0, 1, mv.N_SWEEP))) if len(s) else []
    return {"n_used": int(ok.sum()), "n_nan_excluded": int((~ok).sum()),
            "n_positive": int(y.sum()), "n_negative": int((~y).sum()),
            "auc": mv.auc(s, y),
            "sweep": [mv.at_threshold(s, y, t) for t in q],
            "roc": mv.thin(mv.roc_points(s, y), mv.N_ROC_POINTS),
            "null_auc": mv.summarise([mv.auc(s, n["mask"][ok]) for n in null_sets])}


def _metric_validation():
    import metric_validation  # 판다스와 넘파이가 필요해 참조 세트 모드에서만 불러온다
    return metric_validation


def evaluate_refset(rows: list[dict], refset_meta: dict, pairs_tsv: Path,
                    repeats: int) -> dict[str, Any]:
    """약이 해석되고 PT 가 매핑되고 표적이 있는 행에서 세 점수와 지표 넷의 AUC 와 귀무를 잰다."""
    import pandas as pd
    mv = _metric_validation()
    reasons = {"drug_unresolved": 0, "drug_no_targets": 0, "pt_unmapped": 0}
    kept = []
    for r in rows:
        if r["has_target"] is None:
            reasons["drug_unresolved"] += 1
        elif r["has_target"] is False:
            reasons["drug_no_targets"] += 1
        elif r["mapped"] is None:
            reasons["pt_unmapped"] += 1
        else:
            kept.append(r)
    frame, dropped = mv.load_frame({"rows": [{k: r[k] for k in ("drug", "pt", "class")} for r in kept]},
                                   pairs_tsv)
    scores = pd.DataFrame([{k: r[k] for k in ["drug", "pt"] + SCORE_KEYS} for r in kept])
    frame = frame.merge(scores, on=["drug", "pt"], how="left")
    labels = frame["label"].to_numpy()
    null_sets = mv.null_positive_masks(frame, refset_meta["seed"], repeats)
    blocks = {k: score_block(frame[k].to_numpy(dtype=float), labels, null_sets) for k in SCORE_KEYS}
    metric_blocks = {m: score_block(frame[m].to_numpy(dtype=float), labels, null_sets)
                     for m in SAME_ROW_METRICS}
    return {"n_rows_total": len(rows), "n_rows_evaluated": int(len(frame)),
            "excluded": reasons, "excluded_rule": "순서대로 판정: 약 미해석(표적 조회 불가) → 약 표적 "
            "없음 → PT 매핑 없음. 한 행은 첫 사유 하나로만 센다.",
            "pairs_tsv": str(pairs_tsv), "pairs_tsv_unmatched": dropped,
            "n_positive": int(labels.sum()), "n_negative": int((~labels).sum()),
            "null": {"repeats": repeats, "seed_first": refset_meta["seed"],
                     "seed_last": refset_meta["seed"] + repeats - 1,
                     "rule": "metric_validation.null_positive_masks 를 평가 행에 적용",
                     "n_null_positive": mv.summarise([n["n_null_positive"] for n in null_sets])},
            "scores": blocks, "metrics_same_rows": metric_blocks}


def share_by_class(rows: list[dict]) -> dict[str, Any]:
    """클래스별로 연관이 있는 행과 문헌 제외 점수가 0 보다 큰 행의 비율. 분모는 점수가 있는 행이다."""
    out = {}
    for cls in sorted({r["class"] for r in rows}):
        scored = [r for r in rows if r["class"] == cls and r["max_score"] is not None]
        n = len(scored)

        def frac(pred):
            k = sum(1 for r in scored if pred(r))
            return {"n": k, "share": k / n if n else None}
        out[cls] = {"n_scored": n,
                    "has_association": frac(lambda r: r["has_association"]),
                    "no_literature_api_gt0": frac(lambda r: (r["max_score_no_literature_api"] or 0) > 0),
                    "no_literature_local_gt0": frac(lambda r: (r["max_score_no_literature_local"] or 0) > 0)}
    return out


def _round(x: float | None) -> float | None:
    return None if x is None else float(f"{x:.6g}")


def run_refset(refset_path: Path, f: Fetcher, day: str, pairs_tsv: Path,
               repeats: int | None) -> dict[str, Any]:
    raw = refset_path.read_bytes()
    refset = json.loads(gzip.decompress(raw) if refset_path.suffix == ".gz" else raw)
    meta = fetch_meta(f)
    dv = meta["data_version"]
    calls: dict[str, dict] = {}

    def stage(name, fn):
        before = (f.http_calls, f.cache_hits, f.failures)
        t0 = time.monotonic()
        value = fn()
        calls[name] = {"http": f.http_calls - before[0], "cache_hits": f.cache_hits - before[1],
                       "failures": f.failures - before[2], "seconds": round(time.monotonic() - t0, 1)}
        print(f"[{name}] {calls[name]}", flush=True)
        return value

    drugs = sorted({r["drug"] for r in refset["rows"]})
    pts = sorted({r["pt"] for r in refset["rows"]})
    drug_memo = stage("drugs", lambda: {d: resolve_drug(f, d) for d in drugs})
    terms = stage("pts", lambda: resolve_pts_batched(f, pts))
    targets: dict[str, dict] = {}
    for d in drug_memo.values():
        for t in d.get("targets") or []:
            targets.setdefault(t["ensembl"], t)
    dumps = stage("associations", lambda: {e: fetch_target_dump(f, t) for e, t in sorted(targets.items())})

    rows = []
    for r in refset["rows"]:
        agg = aggregate_refset_row(drug_memo[r["drug"]], terms[r["pt"]], dumps)
        rows.append({"drug": r["drug"], "pt": r["pt"], "class": r["class"], **agg,
                     "evidence_id": evidence_id(dv, r["drug"], r["pt"])})
    n_repeats = repeats if repeats is not None else refset["meta"]["null_repeats"]
    evaluation = evaluate_refset(rows, refset["meta"], pairs_tsv, n_repeats)

    match_counts: dict[str, int] = {}
    for t in terms.values():
        match_counts[t["match"]] = match_counts.get(t["match"], 0) + 1
    drug_table = {d: {k: v[k] for k in ("id", "name", "match", "chembl_version", "error")}
                  | {"targets": [t["symbol"] or t["ensembl"] for t in v.get("targets") or []]}
                  for d, v in drug_memo.items()}
    target_table = {e: {k: v.get(k) for k in ("symbol", "pages", "count_all", "count_no_literature",
                                                  "n_rows", "n_only_in_no_literature",
                                                  "datatype_scores_in_dump", "pagination_ok", "error")}
                    for e, v in dumps.items()}
    columns = ["drug", "pt", "class", "n_targets", "mapped", "has_association"] + SCORE_KEYS + [
        "incomplete"]
    return {
        "meta": meta, "calls": calls,
        "counts": {
            "rows": len(rows), "drugs": len(drugs),
            "drugs_resolved": sum(1 for v in drug_memo.values() if v["targets"] is not None),
            "drugs_with_targets": sum(1 for v in drug_memo.values() if v["targets"]),
            "drugs_unresolved": sorted(d for d, v in drug_memo.items() if v["targets"] is None),
            "drugs_without_targets": sorted(d for d, v in drug_memo.items() if v["targets"] == []),
            "pts": len(pts), "pts_mapped": match_counts.get("label", 0),
            "pts_unmapped": len(pts) - match_counts.get("label", 0), "pt_match_types": match_counts,
            "targets": len(targets),
            "targets_failed": sorted(e for e, v in dumps.items() if v["by_disease"] is None),
            "targets_pagination_mismatch": sorted(e for e, v in dumps.items()
                                                  if v.get("pagination_ok") is False),
            "datatype_scores_in_dump": all(v.get("datatype_scores_in_dump", True)
                                           for v in dumps.values() if v["by_disease"] is not None),
            "rows_incomplete": sum(1 for r in rows if r["incomplete"]),
        },
        "share_by_class": share_by_class(rows),
        "evaluation": evaluation,
        "drugs": drug_table,
        "targets": target_table,
        "pt_terms": {p: {"match": t["match"], "mapped": (t["mapped"] or {}).get("id"),
                         "first_hit": t["first_hit"], "error": t["error"]} for p, t in terms.items()},
        "rows": {"columns": columns,
                 "values": [[_round(r[c]) if c in SCORE_KEYS else r[c] for c in columns] for r in rows]},
        "evidence_id_rule": f"omics:opentargets@{dv}:<DRUG 대문자>:<pt 소문자>",
    }


def refset_main(a: argparse.Namespace) -> int:
    f = Fetcher(dry_run=a.dry_run, sleep=a.sleep)
    day = date.today().isoformat()
    pairs_tsv = a.pairs_tsv or a.refset.with_name(a.refset.name.replace(".json.gz", "_pairs.tsv.gz"))
    result = run_refset(a.refset, f, day, pairs_tsv, a.repeats)
    meta = result.pop("meta")
    out = a.out or OUT_DIR / f"omics_plausibility_refset_{day}.json"
    ev = result["evaluation"]
    print(f"HTTP 호출 {f.http_calls}회, 캐시 적중 {f.cache_hits}회, 실패 {f.failures}회")
    print(f"평가 행 {ev['n_rows_evaluated']} / {ev['n_rows_total']}, 제외 {ev['excluded']}")
    for k, b in {**ev["scores"], **ev["metrics_same_rows"]}.items():
        print(f"  {k:32s} AUC {b['auc'] if b['auc'] is None else round(b['auc'], 4)}  "
              f"귀무 평균 {b['null_auc'].get('mean')}  n {b['n_used']}")
    doc = {
        "ran_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "date": day, "tz": os.environ.get("TZ"), "dry_run": a.dry_run,
        "script": "scripts/omics_plausibility.py --refset", "refset": str(a.refset),
        "caveat": "양성은 SIDER 라벨 기재, 음성은 라벨 부재다. 수치는 라벨 기재 예측 성능이며 인과성이 아니다. "
                  + CAVEAT,
        "source": {"name": "Open Targets Platform GraphQL", "url": OT_URL,
                   "api_version": meta["api_version"], "data_version": meta["data_version"],
                   "meta_request": meta["request"], "meta_cache_changed": meta["cache_changed"],
                   "licence": "CC0 1.0 (https://platform-docs.opentargets.org/licence)"},
        "rules": {
            "drug": "search(entityNames:[drug]) hit 의 name 이 입력과 대소문자 무시로 같을 때만. "
                    "없으면 ChEMBL molecule.json?pref_name__iexact=, 그래도 없으면 표적 없음.",
            "pt": f"search(entityNames:[disease]) 를 한 요청에 {PT_BATCH}개씩 별칭으로 묶고, 첫 hit 의 "
                  "name 이 PT 와 대소문자 무시로 같을 때만(label).",
            "association": f"target.associatedDiseases(page:{{size:{ASSOC_PAGE_SIZE}}}) 전체 목록을 쪽 넘김으로 "
                           "받아 질환 ID 로 조인. 목록에 없으면 0.0.",
            "no_literature_api": {"argument": "datasources", "value": NO_LITERATURE_DATASOURCES,
                                  "note": "같은 요청의 noLiterature 별칭. 질환 ID 로 all 별칭과 맞춘다."},
            "no_literature_local": NO_LITERATURE_RULE_LOCAL,
            "aggregate": "표적별 점수의 최댓값, has_association = max_score > 0.",
            "caveat": CAVEAT,
        },
        "http": {"calls": f.http_calls, "cache_hits": f.cache_hits, "failures": f.failures,
                 "sleep": a.sleep, "by_stage": result.pop("calls")},
        "cache_dir": str(f.root),
        **result,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(_metric_validation().finite(doc), ensure_ascii=False, allow_nan=False),
                   encoding="utf-8")
    tmp.replace(out)
    print(out)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--pair", nargs=2, action="append", metavar=("DRUG", "REACTION"))
    ap.add_argument("--refset", type=Path,
                    help="참조 세트 json(.gz). 주면 묶음 모드로 모든 행에 점수를 붙이고 ROC 를 잰다")
    ap.add_argument("--dry-run", action="store_true", help="캐시에 있는 응답만 쓰고 호출하지 않는다")
    ap.add_argument("--sleep", type=float, default=0.2, help="실제 호출 사이의 최소 간격(초)")
    ap.add_argument("--pairs-tsv", type=Path, default=None,
                    help="--refset 과 함께. 지표 표. 기본은 참조 세트 옆의 <이름>_pairs.tsv.gz")
    ap.add_argument("--repeats", type=int, default=None,
                    help="--refset 과 함께. 귀무 반복 수. 기본은 참조 세트의 null_repeats")
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args(argv)

    if a.refset:
        return refset_main(a)
    a.out = a.out or OUT_DIR / f"omics_plausibility_{date.today().isoformat()}.json"
    pairs = [(d.upper(), r.upper()) for d, r in (a.pair or evidence_grade.DEFAULT_PAIRS)]
    f = Fetcher(dry_run=a.dry_run, sleep=a.sleep)
    day = date.today().isoformat()
    result = run(pairs, f, day)
    meta = result["meta"]

    for row in result["pairs"]:
        syms = ", ".join(t["symbol"] for t in row["targets"] or []) or "-"
        mapped = (row["term"]["mapped"] or {}).get("id") or "매핑 없음"
        print(f"{row['drug']} + {row['pt']}: 표적 [{syms}], 용어 {mapped}, "
              f"max {_fmt(row['max_score'])}, literature 제외 {_fmt(row['max_score_no_literature'])}"
              f" (로컬 {_fmt(row['max_score_no_literature_local'])})  {row['evidence']['id']}")
    print(f"HTTP 호출 {f.http_calls}회, 캐시 적중 {f.cache_hits}회, 실패 {f.failures}회")

    doc = {
        "ran_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "date": day, "tz": os.environ.get("TZ"), "dry_run": a.dry_run,
        "refset": str(a.refset) if a.refset else None,
        "source": {"name": "Open Targets Platform GraphQL", "url": OT_URL,
                   "api_version": meta["api_version"], "data_version": meta["data_version"],
                   "meta_request": meta["request"], "meta_cache_changed": meta["cache_changed"],
                   "licence": "CC0 1.0 (https://platform-docs.opentargets.org/licence)"},
        "rules": {
            "drug": "search(entityNames:[drug]) hit 의 name 이 입력과 대소문자 무시로 같을 때만. "
                    "없으면 ChEMBL molecule.json?pref_name__iexact=, 그래도 없으면 표적 없음.",
            "pt": "search(entityNames:[disease]) 첫 hit 의 name 이 PT 와 대소문자 무시로 같을 때만(label).",
            "association": "target.associatedDiseases(Bs:[질환 ID]) 의 score. 행이 없으면 0.0.",
            "no_literature_api": {"argument": "datasources", "value": NO_LITERATURE_DATASOURCES},
            "no_literature_local": NO_LITERATURE_RULE_LOCAL,
            "aggregate": "max_score = 표적별 score 최댓값, has_association = max_score > 0.",
            "caveat": CAVEAT,
        },
        "http": {"calls": f.http_calls, "cache_hits": f.cache_hits, "failures": f.failures,
                 "sleep": a.sleep},
        "cache_dir": str(f.root),
        "pairs": result["pairs"],
    }
    a.out.parent.mkdir(parents=True, exist_ok=True)
    tmp = a.out.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(a.out)
    print(a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
