#!/usr/bin/env python3
"""Survey the NVIDIA agent-skill catalog and shortlist the skills worth using here.

The catalog lives at `github.com/NVIDIA/skills`. Its README carries one table row per
product, each row listing that product's skills, which is why we parse the README instead
of walking 380 directories: one request replaces hundreds. What a single skill does, and
the exact endpoint it calls, sits in the YAML frontmatter and body of its `SKILL.md`, so
`--detail` fetches those files directly.

The point of having this as a script is provenance. Skill recommendations reached us as
chat messages, and chat is not a source we can re-check later. Running this writes the
catalog we actually saw, with a date, into `eval/results/`, so a claim in our docs can be
compared against the file. The catalog changes often, so re-run before quoting it.

Usage:
  python3 scripts/fetch_nvidia_skills.py                      # list, plus relevant candidates
  python3 scripts/fetch_nvidia_skills.py --detail <skill> ...  # add frontmatter for those skills
  python3 scripts/fetch_nvidia_skills.py --detail-relevant     # frontmatter for every candidate
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

# Words that could touch our pipeline. Matched against both the skill name and the
# product description, since a skill name alone is often too terse to judge.
KEYWORDS = (
    "bionemo", "openfold", "msa", "molkit", "mol", "protein", "drug", "chem",
    "medtech", "digital-health", "clinical", "evidence",
    "aiq", "research", "retriever", "rag", "rerank", "embed",
    "nemotron", "guardrail", "guard", "safety", "eval", "judge",
    "skill-card", "skill-finder", "nat", "agent-toolkit", "agent-intelligence",
)
# Families that match a keyword but have nothing to do with us: computer vision, network
# hardware, robotics. Excluding by name prefix is cruder than reading each description, but
# it keeps the shortlist small enough for a person to read, which is the point.
EXCLUDE = ("doca", "deepstream", "holohub", "earth2", "cuopt", "dali", "jetson",
           "tao-", "physical-ai", "foundationpose", "rtvi", "amc-", "vss-", "nv-generate",
           "cudaq", "cudf", "dynamo", "isaac", "omniverse", "warp", "modulus")


def fetch(url: str) -> str:
    with urllib.request.urlopen(url, timeout=30) as r:  # noqa: S310
        return r.read().decode("utf-8", "replace")


def parse_catalog(readme: str) -> list[dict]:
    """Parse the product table out of the README: one row per product, several skills each."""
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
    """Pull the fields we need from a SKILL.md, both its frontmatter and its body.

    Endpoints are scraped from the body rather than the frontmatter because that is where the
    real call sites appear, and knowing which host a skill talks to decides whether it fits
    inside our sandbox policy at all.
    """
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
