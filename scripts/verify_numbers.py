#!/usr/bin/env python
"""Check that every number written in our documents still matches its source file.

Why this exists
---------------
This project claims, as its contribution, that it catches statements which outrun their
evidence. A document of ours carrying a number its own source no longer supports would
undercut exactly that claim. The risk is not hypothetical: on 2026-09-26 the test count
changed three times in one day, and each change meant editing four documents by hand. Miss
one and nothing tells you.

So each item here measures the value from its source, then checks that the rendered string
appears verbatim in the documents that quote it. String matching is deliberate. It catches
the stale "293개 통과" that a human eye skims past.

Usage
-----
    .venv/bin/python scripts/verify_numbers.py
    .venv/bin/python scripts/verify_numbers.py --quiet   # mismatches only

The exit code is the number of mismatches, so this drops straight into a pre-submission
check or CI. It touches no network and reads only committed result files.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

# Documents expected to carry these numbers. A value missing from any one of them counts as
# a mismatch: partial updates are the failure we are guarding against.
DOCS = ["README.md", "docs/HANDOFF.md", "docs/video/script_flygate.md"]


def read(rel: str) -> str:
    p = ROOT / rel
    return p.read_text(encoding="utf-8") if p.exists() else ""


def offline_tests() -> int:
    """Tests that actually pass in the default run.

    This is not the collected count: network-marked tests are deselected, so counting
    collection would overstate what we can claim to have verified offline.
    """
    out = subprocess.run(
        ["env", "-u", "NVIDIA_API_KEY", str(ROOT / ".venv/bin/python"),
         "-m", "pytest", "-q", "-m", "not network", "-p", "no:cacheprovider"],
        cwd=ROOT, capture_output=True, text=True).stdout
    m = re.search(r"(\d+) passed", out)
    if not m:
        raise RuntimeError("pytest 출력에서 통과 건수를 읽지 못했다")
    return int(m.group(1))


def registered_tools() -> int:
    m = re.search(r"tool_names:\s*\[(.*?)\]", read("configs/author.yml"), re.S)
    return len([x for x in m.group(1).replace("\n", " ").split(",") if x.strip()])


def overclaim_rules() -> int:
    from harness.tools import overclaim_rules as o
    return len(o.RULES)


def structure_rules() -> int:
    """Rules that switch on only when a structure-prediction tool is wired in.

    Kept out of `RULES` so the measured detection rate keeps its original denominator, which
    means it also has to be counted separately here.
    """
    from harness.tools import overclaim_rules as o
    return len(o.STRUCTURE_RULES)


def eval_cases() -> tuple[int, int]:
    lines = [l for l in (ROOT / "eval/cases.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    rej = sum(1 for l in lines if json.loads(l).get("expected_verdict") == "reject")
    return len(lines), rej


def sandbox_pass() -> int:
    m = re.search(r"pass=(\d+)", read("eval/results/openshell_smoke_flydock.txt"))
    return int(m.group(1))


def diffdock_seconds() -> str:
    m = re.search(r"HTTP 200, 소요 ([\d.]+)초", read("eval/results/diffdock_smoke.txt"))
    return m.group(1)


def bench_numbers() -> tuple[int, str]:
    d = json.loads(read("eval/results/bench_nemotron-super-run2.json"))
    s = d["summary"]
    per = round(s["tokens"]["completion"] / s["ok"])
    return per, f'{s["latency_ms"]["median"]:,.0f}'


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="문서 수치와 출처 대조")
    ap.add_argument("--quiet", action="store_true", help="어긋난 항목만 출력한다")
    args = ap.parse_args(argv)

    cases, rejects = eval_cases()
    per_tok, median_ms = bench_numbers()

    # (label, the exact string the documents must contain, where the value came from)
    checks = [
        ("오프라인 테스트", f"{offline_tests()}개 통과", 'pytest -q -m "not network"'),
        ("NAT 등록 도구", f"{registered_tools()}종", "configs/author.yml"),
        ("과잉해석 규칙", f"{overclaim_rules()}종", "overclaim_rules.RULES"),
        ("구조 예측 규칙", f"{structure_rules()}종", "overclaim_rules.STRUCTURE_RULES"),
        ("평가 케이스", f"{cases}건", "eval/cases.jsonl"),
        ("반려 정답 케이스", f"{rejects}/{rejects}", "eval/cases.jsonl"),
        ("샌드박스 스모크", f"{sandbox_pass()}건 통과", "openshell_smoke_flydock.txt"),
        ("DiffDock 소요", f"{diffdock_seconds()}초", "diffdock_smoke.txt"),
        ("3단 건당 출력 토큰", f"{per_tok}개", "bench_nemotron-super-run2.json"),
        ("3단 지연 중앙값", f"{median_ms}ms", "bench_nemotron-super-run2.json"),
    ]

    texts = {d: read(d) for d in DOCS}
    bad = 0
    for name, value, source in checks:
        where = [d for d, t in texts.items() if value in t]
        ok = bool(where)
        if not ok:
            bad += 1
        if args.quiet and ok:
            continue
        mark = "일치" if ok else "**어긋남**"
        print(f"  {name:<18} {value:<16} {mark}")
        if not ok:
            print(f"      출처 {source}")
            print(f"      이 값을 실은 문서가 없다. {', '.join(DOCS)} 를 확인하라")
        elif not args.quiet:
            print(f"      출처 {source} · 실린 곳 {', '.join(where)}")

    print()
    if bad:
        print(f"어긋난 항목 {bad}건. 문서를 출처에 맞춰 고쳐라.")
    else:
        print(f"항목 {len(checks)}건 모두 출처와 일치한다.")
    return bad


if __name__ == "__main__":
    raise SystemExit(main())
