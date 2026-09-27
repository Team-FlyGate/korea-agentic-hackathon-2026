#!/usr/bin/env python3
"""NVIDIA 스킬 카탈로그를 조회해 우리에게 쓸 만한 것을 골라낸다.

카탈로그는 `github.com/NVIDIA/skills` 이고 제품별 표가 README 에 있다. 스킬 하나하나의
용도와 호출 규격은 그 폴더의 `SKILL.md` 앞머리(YAML frontmatter)에 적혀 있다.

카톡으로 주고받은 조사 결과를 문서로 옮기려면 원문을 직접 읽어야 하므로, 이 스크립트가
목록과 앞머리를 받아 JSON 으로 남긴다. 같은 명령을 다시 돌리면 그때의 카탈로그를 다시 잰다.

사용:
  python3 scripts/fetch_nvidia_skills.py                    # 목록과 관련 스킬 후보
  python3 scripts/fetch_nvidia_skills.py --detail <스킬명> ...  # 지정한 스킬의 앞머리까지
  python3 scripts/fetch_nvidia_skills.py --detail-relevant   # 관련 후보 전부의 앞머리
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = "https://raw.githubusercontent.com/NVIDIA/skills/main"
OUT = ROOT / "eval" / "results" / f"nvidia_skills_{date.today().isoformat()}.json"

# 우리 파이프라인과 닿을 수 있는 낱말. 이름과 제품 설명 양쪽에서 찾는다.
KEYWORDS = (
    "bionemo", "openfold", "msa", "molkit", "mol", "protein", "drug", "chem",
    "medtech", "digital-health", "clinical", "evidence",
    "aiq", "research", "retriever", "rag", "rerank", "embed",
    "nemotron", "guardrail", "guard", "safety", "eval", "judge",
    "skill-card", "skill-finder", "nat", "agent-toolkit", "agent-intelligence",
)
# 낱말이 걸리지만 우리와 무관한 것. 기계 비전, 네트워크 장비, 로봇 쪽이다.
EXCLUDE = ("doca", "deepstream", "holohub", "earth2", "cuopt", "dali", "jetson",
           "tao-", "physical-ai", "foundationpose", "rtvi", "amc-", "vss-", "nv-generate",
           "cudaq", "cudf", "dynamo", "isaac", "omniverse", "warp", "modulus")


def fetch(url: str) -> str:
    with urllib.request.urlopen(url, timeout=30) as r:  # noqa: S310
        return r.read().decode("utf-8", "replace")


def parse_catalog(readme: str) -> list[dict]:
    """README 의 제품별 표를 뜯는다. 행 하나가 제품 하나이고 스킬 여러 개를 담는다."""
    rows = []
    for line in readme.splitlines():
        if not line.startswith("| **"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 3:
            continue
        product = cells[0].strip("* ")
        skills = re.findall(r"\[`([^`]+)`\]", cells[2])
        rows.append({"product": product, "description": cells[1], "skills": skills})
    return rows


def relevant(rows: list[dict]) -> list[dict]:
    out = []
    for row in rows:
        hay = (row["product"] + " " + row["description"]).lower()
        for name in row["skills"]:
            blob = (name + " " + hay).lower()
            if any(x in name.lower() for x in EXCLUDE):
                continue
            if any(k in blob for k in KEYWORDS):
                out.append({"skill": name, "product": row["product"],
                            "product_description": row["description"]})
    return out


def frontmatter(name: str) -> dict:
    """SKILL.md 앞머리와 본문에서 쓸 값만 뽑는다."""
    try:
        text = fetch(f"{RAW}/skills/{name}/SKILL.md")
    except Exception as exc:  # noqa: BLE001
        return {"skill": name, "error": str(exc)}
    head = text.split("---", 2)[1] if text.startswith("---") else ""
    def field(key: str) -> str:
        m = re.search(rf"^{key}:\s*(>-?|\|)?\s*(.*?)(?=^\w+:|\Z)", head, re.S | re.M)
        if not m:
            return ""
        return " ".join(m.group(2).split())
    endpoints = sorted(set(re.findall(r"https://[a-z0-9.\-]*api\.nvidia\.com/[^\s\"')]+", text)))
    return {"skill": name,
            "declared_name": field("name"),
            "description": field("description"),
            "license": field("license"),
            "allowed_tools": field("allowed-tools"),
            "endpoints": endpoints,
            "lines": len(text.splitlines()),
            "references": sorted(set(re.findall(r"references/([a-z0-9_\-]+\.md)", text)))}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--detail", nargs="*", default=None, help="앞머리까지 받을 스킬 이름")
    ap.add_argument("--detail-relevant", action="store_true", help="관련 후보 전부의 앞머리")
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args()

    readme = fetch(f"{RAW}/README.md")
    rows = parse_catalog(readme)
    all_skills = [s for r in rows for s in r["skills"]]
    rel = relevant(rows)
    print(f"제품 {len(rows)}개, 스킬 {len(all_skills)}개, 관련 후보 {len(rel)}개")

    wanted = list(a.detail or [])
    if a.detail_relevant:
        wanted += [r["skill"] for r in rel]
    details = []
    if wanted:
        seen: list[str] = []
        for w in wanted:
            if w not in seen:
                seen.append(w)
        with ThreadPoolExecutor(max_workers=8) as ex:
            details = list(ex.map(frontmatter, seen))
        for d in details:
            if d.get("error"):
                print(f"  실패 {d['skill']}: {d['error']}")
            else:
                print(f"  {d['skill']:<46} {d['lines']:>4}줄 "
                      f"엔드포인트 {len(d['endpoints'])} 참조 {len(d['references'])}")

    doc = {"fetched_at": date.today().isoformat(), "source": "github.com/NVIDIA/skills",
           "product_count": len(rows), "skill_count": len(all_skills),
           "products": rows, "relevant": rel, "details": details}
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n{a.out.relative_to(ROOT)} ({a.out.stat().st_size // 1024}KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
