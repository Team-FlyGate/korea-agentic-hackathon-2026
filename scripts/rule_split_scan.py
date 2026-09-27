#!/usr/bin/env python3
"""Find a drug whose top reactions make the fixed triage rule disagree with itself.

Why this exists
---------------
Measuring a judge against the fixed rule only means something when the rule itself splits the
cases. Our first run used niraparib, where all ten top reactions clear the Evans thresholds,
so the rule said "send to a human" ten times out of ten. Against a constant, an agreement rate
is just a restatement of the judge's yes-rate.

This scans candidate drugs, applies the same rule the benchmark uses, and reports how each
one splits. A drug near an even split is the one worth spending model calls on.

It costs nothing but openFDA requests, which need no key and allow 240 per minute, so it is
cheap to run over many candidates before committing to one.

Usage:
  python3 scripts/rule_split_scan.py METFORMIN SEMAGLUTIDE AMOXICILLIN
  python3 scripts/rule_split_scan.py --limit 8 --min-split 3 WARFARIN ATORVASTATIN
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))


def load_bench():
    """Load the benchmark module by path so its fetching and rule logic is reused verbatim.

    Re-implementing the rule here would let the scan drift away from what the benchmark
    actually applies, and then the drug we pick would be chosen on the wrong criterion.
    """
    spec = importlib.util.spec_from_file_location("bench_triage_scale",
                                                  ROOT / "scripts" / "bench_triage_scale.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["bench_triage_scale"] = module
    spec.loader.exec_module(module)
    return module


def common_reactions(limit: int) -> list[dict]:
    """The most-reported reaction terms across all of FAERS, ignoring which drug was involved.

    Scanning a drug's own top reactions was the wrong frame: the reactions reported most often
    for a drug are the ones it actually causes, so the rule said yes to nearly all of them.
    Testing one drug against reactions that are common in general mixes real signals with
    background noise, which is what makes the rule split.
    """
    import json as _json
    import urllib.parse
    import urllib.request
    url = ("https://api.fda.gov/drug/event.json?count="
           "patient.reaction.reactionmeddrapt.exact&limit=" + str(limit))
    with urllib.request.urlopen(url, timeout=40) as r:  # noqa: S310
        payload = _json.loads(r.read().decode())
    return [{"reaction": row["term"], "faers_count": row["count"]}
            for row in payload.get("results", [])]


def scan_drug(bench, drug: str, limit: int, name_field: str,
              against_common: bool = False) -> dict:
    if against_common:
        listing = {"events": common_reactions(limit), "errors": []}
    else:
        listing = bench.fetch_events(drug, limit=limit, name_field=name_field)
    rows = []
    for event in listing["events"]:
        faers = bench.faers_for_event(drug, event["reaction"], name_field=name_field)
        rows.append({"reaction": event["reaction"],
                     "faers_count": event.get("faers_count"),
                     "prr": faers.get("prr"), "ror": faers.get("ror"),
                     "chi2_yates": faers.get("chi2_yates"),
                     "rule_verdict": bench.rule_verdict(faers)})
    yes = sum(1 for r in rows if r["rule_verdict"] == "yes")
    no = sum(1 for r in rows if r["rule_verdict"] == "no")
    unknown = len(rows) - yes - no
    return {"drug": drug, "events": len(rows), "yes": yes, "no": no, "unknown": unknown,
            "split": min(yes, no), "rows": rows, "errors": listing["errors"]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("drugs", nargs="+")
    ap.add_argument("--limit", type=int, default=10, help="약물당 볼 상위 이상사례 수")
    ap.add_argument("--name-field", default="generic")
    ap.add_argument("--against-common", action="store_true",
                    help="약물의 상위 반응 대신 FAERS 전체에서 흔한 반응과 짝지어 본다")
    ap.add_argument("--min-split", type=int, default=3,
                    help="적은 쪽이 이 수 이상이면 쓸 만한 후보로 본다")
    ap.add_argument("--out", type=Path,
                    default=ROOT / "eval" / "results" / f"rule_split_scan_{date.today().isoformat()}.json")
    a = ap.parse_args()

    bench = load_bench()
    results = []
    for drug in a.drugs:
        try:
            result = scan_drug(bench, drug.upper(), a.limit, a.name_field,
                               against_common=a.against_common)
        except Exception as exc:  # noqa: BLE001
            print(f"{drug.upper():<16} 실패 {type(exc).__name__}: {exc}")
            continue
        results.append(result)
        mark = "후보" if result["split"] >= a.min_split else ""
        print(f"{result['drug']:<16} yes {result['yes']:>2} / no {result['no']:>2}"
              f" / 미상 {result['unknown']:>2}   적은 쪽 {result['split']:>2}  {mark}")
        for row in result["rows"]:
            if row["rule_verdict"] == "no":
                print(f"    no  {row['reaction'][:34]:<34} PRR {row['prr']}")

    results.sort(key=lambda r: r["split"], reverse=True)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps({"fetched_at": date.today().isoformat(),
                                 "limit": a.limit, "results": results},
                                ensure_ascii=False, indent=1), encoding="utf-8")
    if results:
        best = results[0]
        print(f"\n가장 고르게 갈린 약물: {best['drug']} (yes {best['yes']} / no {best['no']})")
    print(a.out.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
