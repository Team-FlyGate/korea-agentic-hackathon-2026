#!/usr/bin/env python3
"""Run the FlyGate agent once through its subcommands and collect the NVIDIA call log.

Why this exists
---------------
The hackathon organiser asked entrants to show "what NVIDIA technology was used and how",
and named call logs as acceptable evidence. The demo repository already records one: set
FV_CALL_LOG to a path and api/_fv/calllog.py appends a JSON line per NVIDIA request. What was
missing is a repeatable way to produce a log worth attaching, so this script drives the
subcommands that exercise different services, then summarises what came back.

It deliberately makes real calls. A log with no traffic in it proves nothing, and the point
of the attachment is that each line carries an NVCF request id the vendor can look up.

What it does not do
-------------------
No live docking. `flygate discover --live` submits a DiffDock job that takes minutes and
spends credits, so the default sweep stays on the cheap paths. Pass --with-discover when a
docking call belongs in the evidence.

The log holds metadata only: timestamps, endpoints, models, status, latency, token counts,
request ids. Keys, headers, prompts and response bodies never enter it. This script checks
that before writing, because the file is meant to be committed to a public repository.

Usage:
  python scripts/collect_call_log.py --repo ~/src/Project-FlyGate
  python scripts/collect_call_log.py --repo ../Project-FlyGate --out eval/results/calls.jsonl
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

# Each entry is (label, argv for flygate). Chosen to hit a different service or model:
# grade reaches the retrieval NIM for literature reranking, kr-causality runs Nemotron Super
# over a Korean report, triage exercises the reflex path, and critic fans out to the safety
# models one claim at a time.
SWEEP = [
    ("근거 등급", ["grade", "NIRAPARIB", "thrombocytopenia"]),
    ("국내 인과성", ["kr-causality", "agent/examples/kr_report.txt", "--route"]),
    ("사례 분류", ["triage", "--demo", "1"]),
    ("크리틱", ["critic", "agent/examples/claims_niraparib.json"]),
]
DISCOVER = ("도킹 (실호출)", ["discover", "parp1", "--live"])

# Anything matching these must never appear in a log we are about to publish. The bearer
# pattern is deliberately loose: it is cheaper to investigate a false positive than to push
# a key to a public repository.
SECRET_PATTERNS = (
    re.compile(r"\bnvapi-[A-Za-z0-9_\-]{10,}"),
    re.compile(r"\bapikey_[A-Za-z0-9_]{10,}"),
    re.compile(r"(?i)\bauthorization\b"),
    re.compile(r"(?i)\bbearer\s+\S{10,}"),
)


def run_sweep(repo: Path, log_path: Path, python_bin: Path | None,
              with_discover: bool) -> list[tuple[str, int]]:
    """Run each subcommand with FV_CALL_LOG pointing at log_path. Returns (label, rc) pairs."""
    env = dict(os.environ)
    env["FV_CALL_LOG"] = str(log_path)
    if python_bin:
        # flygate is a shell wrapper that calls whatever `python3` it finds first, and it
        # runs with cwd set to the repo, so the directory has to be absolute here.
        env["PATH"] = f"{python_bin.parent.resolve()}{os.pathsep}{env.get('PATH', '')}"

    results = []
    steps = SWEEP + ([DISCOVER] if with_discover else [])
    for label, argv in steps:
        print(f"  {label}: flygate {' '.join(argv)}", file=sys.stderr)
        proc = subprocess.run(["bash", "agent/bin/flygate", *argv], cwd=repo, env=env,
                              capture_output=True, text=True)
        if proc.returncode != 0:
            # Report and continue. One failing subcommand should not throw away the calls
            # the others already recorded.
            tail = (proc.stderr or proc.stdout).strip().splitlines()[-3:]
            print(f"    실패 (rc={proc.returncode}): {' / '.join(tail)}", file=sys.stderr)
        results.append((label, proc.returncode))
    return results


def scan_for_secrets(text: str) -> list[str]:
    """Return the patterns that matched, so the caller can refuse to publish the file."""
    return [p.pattern for p in SECRET_PATTERNS if p.search(text)]


# The log records each call's purpose in English, as the demo code writes it. Readers of the
# summary are Korean, so the known purposes get a gloss; anything new falls through unchanged
# rather than being dropped.
PURPOSE_KO = {
    "literature rerank: PubMed top-20 -> read 6": "문헌 재정렬. PubMed 상위 20편에서 읽을 6편을 고른다",
    "KR report intake: structure a Korean AE form (JSON)": "국내 보고 구조화. 한국어 이상사례 보고를 항목으로 나눈다",
    "PV policy guard: BYO custom_policy per claim": "약물감시 정책 가드. 주장마다 자체 정책으로 검사한다",
    "Safety Guard: per-claim treatment-advice check (R11)": "안전 가드. 주장마다 개별 치료 조언인지 본다 (R11)",
}


def summarise(rows: list[dict]) -> str:
    """Build the one-page Korean summary that goes beside the JSONL."""
    by_model = collections.Counter((r.get("service", ""), r.get("model") or "") for r in rows)
    ok = sum(1 for r in rows if r.get("http_status") == 200)
    tokens = sum((r.get("usage") or {}).get("total_tokens", 0) for r in rows)
    latency = sum(r.get("latency_ms", 0) for r in rows) / 1000

    out = [f"# FlyGate NVIDIA 호출 기록 ({date.today().isoformat()})", "",
           f"`agent/bin/flygate` 를 한 바퀴 돌려 남긴 실제 호출 {len(rows)}건이다. "
           "`api/_fv/calllog.py` 가 호출마다 한 줄씩 기록했다.", "",
           "**키와 요청 본문과 응답 본문은 들어 있지 않다.** 남는 것은 시각과 엔드포인트, 모델, "
           "상태, 지연, 요청 ID, 토큰 수뿐이라 그대로 공개해도 된다.", "",
           "## 무엇을 불렀나", "",
           "| 건수 | 서비스 | 모델 |", "|---|---|---|"]
    for (service, model), n in by_model.most_common():
        out.append(f"| {n} | {service} | `{model}` |")

    failed = len(rows) - ok
    status = f"{len(rows)}건 모두 200" if not failed else f"{len(rows)}건 중 {ok}건이 200, {failed}건이 실패"
    out += ["", f"응답은 {status}이다. 주고받은 토큰은 모두 {tokens:,}개이고 "
                f"지연을 다 더하면 {latency:.1f}초다.", "",
            "## 어디에 썼나", ""]
    for purpose in sorted({r.get("purpose", "") for r in rows if r.get("purpose")}):
        out.append(f"- {PURPOSE_KO.get(purpose, purpose)}")
    out += ["", "## 대조하는 법", "",
            "호출마다 NVCF 요청 ID(`nvcf_reqid`)가 붙어 있다. NVIDIA 쪽 기록과 한 건씩 맞춰 볼 수 있다.",
            "다시 만들려면 `scripts/collect_call_log.py --repo <Project-FlyGate 경로>` 를 돌린다.", ""]
    return "\n".join(out)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True,
                        help="Project-FlyGate 저장소 경로")
    parser.add_argument("--out", type=Path,
                        help="JSONL 저장 위치 (기본: <repo>/eval/call_log_<날짜>.jsonl)")
    parser.add_argument("--python", type=Path,
                        help="flygate 가 쓸 python3 이 있는 경로 (httpx 가 설치된 것)")
    parser.add_argument("--with-discover", action="store_true",
                        help="DiffDock 실호출까지 포함한다. 몇 분 걸리고 크레딧을 쓴다")
    parser.add_argument("--keep-existing", action="store_true",
                        help="기존 로그 파일에 덧붙인다 (기본은 새로 시작)")
    args = parser.parse_args()

    repo = args.repo.expanduser().resolve()
    python_bin = args.python.expanduser().resolve() if args.python else None
    if python_bin and not python_bin.exists():
        sys.exit(f"python 을 찾지 못했다: {python_bin}")
    if not (repo / "agent" / "bin" / "flygate").exists():
        sys.exit(f"flygate 를 찾지 못했다: {repo}")

    out = (args.out or repo / "eval" / f"call_log_{date.today().isoformat()}.jsonl").expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists() and not args.keep_existing:
        out.unlink()

    print(f"호출 수집 시작 → {out}", file=sys.stderr)
    results = run_sweep(repo, out, python_bin, args.with_discover)

    if not out.exists():
        sys.exit("호출이 한 건도 기록되지 않았다. 키와 네트워크를 확인한다.")

    text = out.read_text(encoding="utf-8")
    leaked = scan_for_secrets(text)
    if leaked:
        sys.exit(f"로그에 비밀값으로 보이는 것이 있다. 공개하지 말고 확인한다: {leaked}")

    rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    summary_path = out.with_suffix(".md")
    summary = summarise(rows)
    summary_path.write_text(summary, encoding="utf-8")

    failed = [label for label, rc in results if rc != 0]
    print(f"\n{summary}", file=sys.stderr)
    print(f"저장: {out} ({len(rows)}건)", file=sys.stderr)
    print(f"저장: {summary_path}", file=sys.stderr)
    if failed:
        print(f"실패한 명령: {', '.join(failed)}", file=sys.stderr)


if __name__ == "__main__":
    main()
