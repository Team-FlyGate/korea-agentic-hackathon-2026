#!/usr/bin/env python3
"""openFDA FAERS 에서 약물별 보고 건수와 상위 이상사례를 받아 사례 약물을 고른다.

사례 약물을 바꿀지 판단하려면 보고가 실제로 얼마나 쌓였는지 봐야 한다. 후보를 넣고 돌리면
성분명과 상품명 양쪽으로 세어 표로 찍는다. 키가 필요 없고 분당 240회 한도 안에서 돈다.

사용:
  python3 scripts/faers_drug_scan.py NIRAPARIB LECANEMAB DONANEMAB ADUCANUMAB
  python3 scripts/faers_drug_scan.py --top 8 PEMBROLIZUMAB
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = "https://api.fda.gov/drug/event.json"


def get(params: dict) -> dict:
    url = BASE + "?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    try:
        with urllib.request.urlopen(url, timeout=40) as r:  # noqa: S310
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 404:      # openFDA 는 결과 없음을 404 로 준다
            return {"meta": {"results": {"total": 0}}, "results": []}
        return {"error": f"HTTP {e.code}", "body": e.read().decode()[:200]}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}


def scan(drug: str, top: int) -> dict:
    row: dict = {"drug": drug}
    for label, field in (("generic", "patient.drug.openfda.generic_name"),
                         ("brand", "patient.drug.openfda.brand_name")):
        d = get({"search": f'{field}:"{drug}"', "limit": 1})
        row[f"{label}_total"] = d.get("meta", {}).get("results", {}).get("total", 0)
        if d.get("error"):
            row[f"{label}_error"] = d["error"]
        time.sleep(0.3)
    field = ("patient.drug.openfda.generic_name" if row["generic_total"] >= row["brand_total"]
             else "patient.drug.openfda.brand_name")
    row["counted_on"] = field.rsplit(".", 1)[-1]
    d = get({"search": f'{field}:"{drug}"',
             "count": "patient.reaction.reactionmeddrapt.exact", "limit": top})
    row["top_reactions"] = [{"term": x["term"], "count": x["count"]}
                            for x in d.get("results", [])[:top]]
    time.sleep(0.3)
    return row


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("drugs", nargs="+")
    ap.add_argument("--top", type=int, default=6)
    ap.add_argument("--out", type=Path,
                    default=ROOT / "eval" / "results" / f"faers_drug_scan_{date.today().isoformat()}.json")
    a = ap.parse_args()

    rows = []
    for drug in a.drugs:
        row = scan(drug.upper(), a.top)
        rows.append(row)
        tot = max(row["generic_total"], row["brand_total"])
        print(f"{drug.upper():<16} 보고 {tot:>8,}건  ({row['counted_on']} 기준)")
        for r in row["top_reactions"]:
            print(f"    {r['count']:>6,}  {r['term']}")
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps({"fetched_at": date.today().isoformat(), "rows": rows},
                                ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n{a.out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
