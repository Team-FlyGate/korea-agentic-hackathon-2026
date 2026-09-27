#!/usr/bin/env python
"""Benchmark a per-report triage path and what it costs to run on a frontier model.

Why this exists
---------------
The main pipeline follows one compound in depth, and at that depth a cheap classifier in
front of the expensive model buys nothing: handling a single report is already cheap. The
saving only appears with volume. So this builds the other shape of the problem, many
reports for one compound, judges each one separately, and measures per-report latency and
per-report tokens.

What it measures
----------------
It takes a compound's FAERS adverse events, most-reported first, keeps the top N, attaches
the openFDA 2x2 counts and disproportionality measures to each, and asks a single yes/no
question: should a person look at this one first? Four things are recorded.

- verdict: yes routes to human review, no stays in the automated queue
- latency: milliseconds per report, mean and median
- tokens: prompt and completion counted separately, then divided per report
- agreement with the fixed rules: how often the model matches the rule that runs without an
  LLM at all (an Evans signal or an ROR signal)

That last line is the point of the experiment. It puts the token cost of one yes/no answer
next to how often that answer differs from a rule that costs nothing to run.

Any OpenAI-compatible endpoint can act as the judge. Point ``--base-url``, ``--model`` and
``--api-key-env`` at it. Keys are read only from the environment, so nothing lands in the
repository.

Usage
----
    # Cache-only path with no LLM calls, so it runs to completion without a key
    .venv/bin/python scripts/bench_triage_scale.py --offline --drug niraparib

    # Baseline: ten reports through the NVIDIA model we ship with
    set -a; source .env; set +a
    .venv/bin/python scripts/bench_triage_scale.py --drug niraparib --limit 10

    # Swap in a different judge
    .venv/bin/python scripts/bench_triage_scale.py --label jev \\
        --base-url https://<배포주소>/v1 --model typesafe-ai/jev --api-key-env JEV_API_KEY

주의
----
한 건마다 openFDA 를 네 번, 판정기를 한 번 부른다. ``--limit`` 기본값을 10 으로 작게 둔
이유가 그것이다. 호출이 곧 비용이다. openFDA 원응답은 기존 도구의 파일 캐시에 그대로
쌓이므로 같은 화합물을 다시 돌리면 네트워크로 나가지 않는다.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import statistics
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from harness.tools import pharmasignal_openfda as ofda  # noqa: E402
from harness.tools import jev_client as jev
from harness.tools.pharmasignal_common import ResponseCache, http_get, now_iso  # noqa: E402

DEFAULT_DRUG = "niraparib"
DEFAULT_LIMIT = 10
DEFAULT_OUT_DIR = "eval/results"
DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"
DEFAULT_MODEL = "nvidia/nemotron-3-super-120b-a12b"

# A short, rigidly formatted yes/no question. Asking for a long justification would move what
# we are measuring from "tokens per decision" to "quality of prose", and the prompt is kept in
# Korean because changing its wording would invalidate every number already recorded.
SYSTEM_PROMPT = """당신은 약물감시 1차 선별 담당이다. 이상사례 한 건의 FAERS 집계와 불균형 지표만 보고
그 건을 사람이 먼저 봐야 하는지 판정한다.

출력은 아래 두 줄만 쓴다. 다른 문장, 목록, 해설을 붙이지 않는다.
VERDICT: YES 또는 NO
REASON: 한 문장, 60자 이내

YES 는 사람이 먼저 검토해야 하는 건이다. NO 는 자동 큐에 남겨도 되는 건이다.
판단 근거는 주어진 숫자뿐이다. 주어지지 않은 사실을 끌어오지 않는다."""


# ======================================================================================
# Fetching the adverse-event list
# ======================================================================================

def count_url(drug: str, name_field: str = "generic") -> str:
    """Build the openFDA aggregation query that counts reports per reaction for one drug.

    It borrows the field constants and clause builder from the shipped tool rather than
    restating them, so the benchmark cannot drift away from what the tool actually queries.
    The tool file itself is left untouched.
    """
    clause = ofda._drug_clause(drug, name_field)
    return f"{ofda.BASE}?search={clause}&count={ofda.REACTION_FIELD}"


def count_cache(drug: str, name_field: str = "generic", enabled: bool = True) -> ResponseCache:
    """Cache for reaction-list responses, under its own source so it cannot collide with the
    per-report 2x2 cache."""
    return ResponseCache("triage_events", f"{drug.strip().lower()}|{name_field}", enabled=enabled)


class CacheOnlyGet:
    """Stand-in for ``http_get`` used offline: serve from cache, otherwise report a miss.

    It never reaches the network. A URL that is not cached comes back as
    ``offline_cache_miss`` and that report is counted as a failure rather than quietly
    dropped, so an incomplete cache cannot masquerade as a clean run.
    """

    def __init__(self) -> None:
        self.hits = 0
        self.misses = 0

    def __call__(self, url: str, cache: ResponseCache | None = None, **_kwargs: Any) -> Any:
        hit = None if cache is None else cache.get(url)
        if hit is None:
            self.misses += 1
            return {"__error__": "offline_cache_miss", "body": url}
        self.hits += 1
        cache.hits += 1
        return hit


@contextlib.contextmanager
def cache_only_network(*modules: Any):
    """Swap the tool module's ``http_get`` for the cache-only stand-in, inside this block only.

    ``pharmasignal_openfda.py`` is not edited and the original function is restored on exit.
    The aim is to reuse the tool's real 2x2 counting and its disproportionality formulas in
    offline mode while closing the one door that leads to the network.
    """
    stub = CacheOnlyGet()
    saved = [(m, m.http_get) for m in modules]
    try:
        for m in modules:
            m.http_get = stub
        yield stub
    finally:
        for m, fn in saved:
            m.http_get = fn


def fetch_events(drug: str, *, limit: int = DEFAULT_LIMIT, name_field: str = "generic",
                 use_cache: bool = True, offline: bool = False) -> dict[str, Any]:
    """Fetch a compound's adverse events, most-reported first, keeping the top N.

    Returns drug, name_field, url, events[{term, count}], total_terms, errors and cache.
    An empty result (404 NOT_FOUND) is read as zero events rather than as an error, since
    openFDA uses that status for "nothing matched".
    """
    if name_field not in ofda.NAME_FIELDS:
        raise ValueError(f"name_field 는 {sorted(ofda.NAME_FIELDS)} 중 하나여야 합니다: {name_field!r}")
    cache = count_cache(drug, name_field, enabled=use_cache)
    url = count_url(drug, name_field)
    getter = CacheOnlyGet() if offline else http_get
    payload = getter(url, cache=cache, is_json=True)

    errors: list[str] = []
    results: list[dict[str, Any]] = []
    if isinstance(payload, dict) and "__error__" in payload:
        if payload["__error__"] == 404 and "NOT_FOUND" in str(payload.get("body", "")):
            pass  # no reports at all for this pair
        else:
            errors.append(f"events: http_error:{payload['__error__']}")
    elif isinstance(payload, dict) and isinstance(payload.get("results"), list):
        results = payload["results"]
    else:
        errors.append("events: unexpected_payload")

    events = [{"rank": i, "reaction": str(r.get("term", "")), "faers_count": int(r.get("count", 0))}
              for i, r in enumerate(results[:limit], 1) if r.get("term")]
    return {"drug": drug, "name_field": name_field, "url": url, "events": events,
            "total_terms": len(results), "errors": errors,
            "cache": {"path": str(cache.path), "hits": cache.hits, "misses": cache.misses}}


# ======================================================================================
# Per-report judging
# ======================================================================================

def _fmt(value: Any, digits: int = 2) -> str:
    if value is None:
        return "없음"
    if isinstance(value, bool):
        return "예" if value else "아니오"
    if isinstance(value, (int,)):
        return f"{value:,}"
    return f"{value:,.{digits}f}"


def _fmt_ci(ci: Any) -> str:
    if not ci or len(ci) != 2 or ci[0] is None:
        return "없음"
    return f"{ci[0]:,.2f} 에서 {ci[1]:,.2f}"


def build_evidence(drug: str, event: dict[str, Any], faers: dict[str, Any]) -> str:
    """The evidence handed to the judge: this event's FAERS counts and measures, nothing else."""
    counts = faers.get("counts") or {}
    lines = [
        f"약물: {drug}",
        f"이상사례(MedDRA PT): {event['reaction']}",
        f"이 약물에서의 보고 건수: {_fmt(event.get('faers_count'))}",
        f"2x2 집계: a={_fmt(counts.get('a'))}, b={_fmt(counts.get('b'))}, "
        f"c={_fmt(counts.get('c'))}, d={_fmt(counts.get('d'))}",
        f"PRR: {_fmt(faers.get('prr'))} (95% CI {_fmt_ci(faers.get('prr_ci95'))})",
        f"ROR: {_fmt(faers.get('ror'))} (95% CI {_fmt_ci(faers.get('ror_ci95'))})",
        f"카이제곱(Yates 보정): {_fmt(faers.get('chi2_yates'))}",
        f"Evans 신호: {_fmt(faers.get('evans_signal'))}",
        f"ROR 신호: {_fmt(faers.get('ror_signal'))}",
    ]
    return "\n".join(lines)


def parse_verdict(text: str) -> tuple[str | None, str]:
    """Pull the verdict and short reason out of a reply. A malformed reply yields None, which
    is counted as a failure rather than guessed at."""
    verdict = None
    match = re.search(r"VERDICT\s*[:：]\s*\**\s*(YES|NO)", text or "", re.IGNORECASE)
    if match is None:
        match = re.search(r"\b(YES|NO)\b", text or "", re.IGNORECASE)
    if match:
        verdict = match.group(1).lower()
    reason = ""
    rmatch = re.search(r"REASON\s*[:：]\s*(.+)", text or "")
    if rmatch:
        reason = rmatch.group(1).strip()
    return verdict, reason[:200]


def rule_verdict(faers: dict[str, Any]) -> str | None:
    """The fixed rule, running without an LLM: an Evans or ROR signal sends the case to a human.

    A report whose counts could not be fetched returns None instead of a verdict. Guessing
    there would quietly turn a data gap into a decision.
    """
    if not faers.get("counts"):
        return None
    return "yes" if (faers.get("evans_signal") or faers.get("ror_signal")) else "no"


def rule_one(faers: dict[str, Any]) -> dict[str, Any]:
    """One fixed-rule decision. No LLM call, so there are no tokens and the latency is only
    the arithmetic."""
    started = time.perf_counter()
    verdict = rule_verdict(faers)
    latency_ms = round((time.perf_counter() - started) * 1000, 3)
    if verdict is None:
        return {"ok": False, "error": "FAERS 집계를 받지 못해 판정하지 않았다",
                "verdict": None, "reason": "", "latency_ms": latency_ms,
                "usage": None, "judge": "rule"}
    return {"ok": True, "error": None, "verdict": verdict,
            "reason": "Evans 또는 ROR 신호" if verdict == "yes" else "신호 기준 미달",
            "latency_ms": latency_ms, "usage": None, "judge": "rule"}


# --------------------------------------------------------------------------------------
# Jev judge
#
# Jev answers a typed question with a calibrated probability, so the triage decision becomes a
# threshold rather than a parse. That removes the failure mode the LLM path has to guard
# against, where a reply arrives without the VERDICT line and the report cannot be scored.
#
# The state carries the drug name, the reaction name, the counts and the measures. Keeping the
# names in means the model can also draw on what it already knows about that drug, and the
# first run showed it doing exactly that: thrombocytopenia, which niraparib's label describes,
# scored lower than off-label use despite a PRR of 38 against 2.5. That is defensible triage,
# but it is not a judgment from the numbers alone, so the measurement must be read as "numbers
# plus priors". An ablation with the names redacted would separate the two.
# --------------------------------------------------------------------------------------
JEV_QUESTION_KEY = "needs_human"

JEV_QUESTIONS = {
    JEV_QUESTION_KEY: {
        "type": "noul",
        "instructions": (
            "Should a pharmacovigilance reviewer read this case before it goes to the "
            "automated queue? Judge only from the counts, the measures and which record "
            "fields are present."),
        "criteria": {
            "true": "A reviewer should read this case first, because the evidence or the "
                    "missing fields could change what the report means.",
            "false": "The case can wait in the automated queue without a reviewer looking "
                     "at it first.",
        },
    },
}


def jev_state(drug: str, event: dict[str, Any], faers: dict[str, Any]) -> dict[str, Any]:
    """Build the state sent to Jev for one report."""
    counts = faers.get("counts") or {}
    return {
        "drug": drug,
        "reaction": event["reaction"],
        "reports_with_this_reaction": event.get("faers_count"),
        "contingency_2x2": {k: counts.get(k) for k in ("a", "b", "c", "d")},
        "measures": {"PRR": faers.get("prr"), "ROR": faers.get("ror"),
                     "chi_square_yates": faers.get("chi2_yates")},
        "note": "Public FAERS carries no causality assessment and no narrative.",
    }


def jev_one(drug: str, event: dict[str, Any], faers: dict[str, Any], *, model: str,
            threshold: float, timeout: float) -> dict[str, Any]:
    """Judge one report with Jev and turn its probability into the same row shape."""
    result = jev.systemone(jev_state(drug, event, faers), JEV_QUESTIONS,
                           model=model, timeout=timeout)
    latency_ms = round(result.get("seconds", 0.0) * 1000, 1)
    usage = jev.usage_tokens(result)
    probability = jev.noul_probability(result, JEV_QUESTION_KEY)

    if probability is None:
        return {"ok": False, "error": result.get("error") or "noul 값을 읽지 못했다",
                "verdict": None, "reason": "", "latency_ms": latency_ms,
                "usage": usage, "judge": "jev", "jev_probability": None,
                "jev_request_id": result.get("request_id"), "http_status": result.get("status")}

    verdict = "yes" if probability >= threshold else "no"
    return {"ok": True, "error": None, "verdict": verdict,
            "reason": f"noul {probability:.2f} (임계값 {threshold:.2f})",
            "latency_ms": latency_ms, "usage": usage, "judge": "jev",
            "jev_probability": probability, "jev_request_id": result.get("request_id"),
            "http_status": result.get("status")}


def judge_one(client: Any, model: str, evidence: str, *, timeout: float,
              max_tokens: int) -> dict[str, Any]:
    """Judge one report with a single call and return verdict, latency and token usage."""
    started = time.perf_counter()
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": SYSTEM_PROMPT},
                      {"role": "user", "content": evidence}],
            temperature=0.2, top_p=0.95, max_tokens=max_tokens,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}})
    except Exception as exc:  # noqa: BLE001  실패도 그대로 기록한다
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}",
                "verdict": None, "reason": "",
                "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                "usage": None, "judge": "llm"}
    latency_ms = round((time.perf_counter() - started) * 1000, 1)

    text = (response.choices[0].message.content or "") if response.choices else ""
    usage = getattr(response, "usage", None)
    usage_d = {"prompt_tokens": getattr(usage, "prompt_tokens", None),
               "completion_tokens": getattr(usage, "completion_tokens", None),
               "total_tokens": getattr(usage, "total_tokens", None)} if usage else None

    verdict, reason = parse_verdict(text)
    if verdict is None:
        return {"ok": False, "error": "응답에서 VERDICT 를 읽지 못했다",
                "verdict": None, "reason": "", "latency_ms": latency_ms,
                "usage": usage_d, "judge": "llm", "raw_response": text[:400]}
    return {"ok": True, "error": None, "verdict": verdict, "reason": reason,
            "latency_ms": latency_ms, "usage": usage_d, "judge": "llm"}


def faers_for_event(drug: str, reaction: str, *, name_field: str = "generic",
                    use_cache: bool = True) -> dict[str, Any]:
    """Call the shipped tool to get one report's 2x2 table and disproportionality measures.

    오프라인에서는 호출부가 ``cache_only_network`` 로 감싸므로 이 함수는 그대로 두고도
    네트워크로 나가지 않는다.
    """
    return ofda.faers_disproportionality(drug, reaction, name_field=name_field,
                                         use_cache=use_cache)


def run_rows(drug: str, events: list[dict[str, Any]], *, name_field: str = "generic",
             use_cache: bool = True, client: Any = None, model: str = "",
             timeout: float = 120.0, max_tokens: int = 256, sleep: float = 0.0,
             progress: bool = True, judge: str = "llm",
             jev_threshold: float = 0.5) -> list[dict[str, Any]]:
    """Walk the event list one report at a time. Without a ``client`` the fixed rules decide."""
    rows: list[dict[str, Any]] = []
    total = len(events)
    for i, event in enumerate(events, 1):
        faers = faers_for_event(drug, event["reaction"], name_field=name_field,
                                use_cache=use_cache)
        evidence = build_evidence(drug, event, faers)
        if judge == "jev":
            result = jev_one(drug, event, faers, model=model, threshold=jev_threshold,
                             timeout=timeout)
        elif client is None:
            result = rule_one(faers)
        else:
            result = judge_one(client, model, evidence, timeout=timeout, max_tokens=max_tokens)
        counts = faers.get("counts") or {}
        row = {
            "rank": event.get("rank", i),
            "reaction": event["reaction"],
            "faers_count": event.get("faers_count"),
            "counts_a": counts.get("a"),
            "prr": faers.get("prr"),
            "ror": faers.get("ror"),
            "chi2_yates": faers.get("chi2_yates"),
            "evans_signal": faers.get("evans_signal"),
            "ror_signal": faers.get("ror_signal"),
            "rule_verdict": rule_verdict(faers),
            "faers_errors": faers.get("errors", []),
            "evidence_chars": len(evidence),
        }
        row.update(result)
        rows.append(row)
        if progress:
            tok = (row.get("usage") or {}).get("total_tokens")
            print(f"  [{i:2d}/{total}] {row['reaction'][:34]:<34} "
                  f"판정 {str(row['verdict']):<4} 규칙 {str(row['rule_verdict']):<4} "
                  f"{row['latency_ms']:>9.1f} ms  토큰 {str(tok):>6}  "
                  f"{row['error'] or ''}", flush=True)
        if sleep and i < total:
            time.sleep(sleep)
    return rows


# ======================================================================================
# Summary
# ======================================================================================

def summarize(rows: list[dict[str, Any]], *, price_in: float | None = None,
              price_out: float | None = None, wall_clock_s: float | None = None) -> dict[str, Any]:
    """Count reports, tokens per report and latency. Failed calls are excluded from the
    aggregates and counted on their own, so an outage cannot look like a slow model."""
    ok = [r for r in rows if r.get("ok")]
    failed = [r for r in rows if not r.get("ok")]
    n = len(ok)

    lat = [r["latency_ms"] for r in ok if r.get("latency_ms") is not None]
    p_tok = sum((r.get("usage") or {}).get("prompt_tokens") or 0 for r in ok)
    c_tok = sum((r.get("usage") or {}).get("completion_tokens") or 0 for r in ok)
    t_tok = p_tok + c_tok

    cost = None
    if price_in is not None and price_out is not None:
        cost = round(p_tok / 1_000_000 * price_in + c_tok / 1_000_000 * price_out, 6)

    verdicts = {"yes": sum(1 for r in ok if r.get("verdict") == "yes"),
                "no": sum(1 for r in ok if r.get("verdict") == "no")}

    comparable = [r for r in ok if r.get("rule_verdict") in ("yes", "no")]
    match = sum(1 for r in comparable if r.get("verdict") == r.get("rule_verdict"))

    per_event = {
        "prompt_tokens": round(p_tok / n, 1) if n else None,
        "completion_tokens": round(c_tok / n, 1) if n else None,
        "total_tokens": round(t_tok / n, 1) if n else None,
        "latency_ms_mean": round(statistics.fmean(lat), 1) if lat else None,
        "latency_ms_median": round(statistics.median(lat), 1) if lat else None,
        "cost_usd": round(cost / n, 8) if (cost is not None and n) else None,
    }

    # Straight-line projection to larger volumes: the per-report value multiplied out. It
    # ignores parallelism and cache hits, so treat it as an upper bound, not a forecast.
    projected = None
    if n:
        projected = {
            "basis": "건당 평균을 1,000건에 그대로 곱한 추정값이다. 실측이 아니다.",
            "events": 1000,
            "total_tokens": int(round(t_tok / n * 1000)),
            "hours_sequential": (round(per_event["latency_ms_mean"] * 1000 / 3_600_000, 2)
                                 if per_event["latency_ms_mean"] is not None else None),
            "cost_usd": round(cost / n * 1000, 4) if cost is not None else None,
        }

    return {
        "events": len(rows), "ok": n, "failed": len(failed),
        "failed_reactions": [r.get("reaction") for r in failed],
        "verdicts": verdicts,
        "rule_agreement": {"match": match, "of": len(comparable),
                           "rate": round(match / len(comparable), 4) if comparable else None},
        "latency_ms": {"mean": round(statistics.fmean(lat), 1) if lat else None,
                       "median": round(statistics.median(lat), 1) if lat else None,
                       "min": min(lat) if lat else None, "max": max(lat) if lat else None,
                       "total": round(sum(lat), 1) if lat else None},
        "tokens": {"prompt": p_tok, "completion": c_tok, "total": t_tok},
        "per_event": per_event,
        "cost_usd": cost,
        "wall_clock_s": None if wall_clock_s is None else round(wall_clock_s, 2),
        "projected_estimate": projected,
    }


def print_summary(label: str, s: dict[str, Any]) -> None:
    lat, pe, agr = s["latency_ms"], s["per_event"], s["rule_agreement"]
    print(f"\n[{label}]")
    print(f"  건수          {s['events']}건 (성공 {s['ok']}, 실패 {s['failed']})")
    print(f"  판정          yes {s['verdicts']['yes']} / no {s['verdicts']['no']}")
    print(f"  규칙과 일치    {agr['match']}/{agr['of']}")
    print(f"  지연 중앙값    {lat['median']} ms (평균 {lat['mean']} ms)")
    print(f"  건당 토큰      입력 {pe['prompt_tokens']} / 출력 {pe['completion_tokens']} "
          f"/ 합계 {pe['total_tokens']}")
    print(f"  토큰 합계      입력 {s['tokens']['prompt']:,} / 출력 {s['tokens']['completion']:,}")
    if s["cost_usd"] is not None:
        print(f"  비용          ${s['cost_usd']} (건당 ${pe['cost_usd']})")
    if s["wall_clock_s"] is not None:
        print(f"  벽시계        {s['wall_clock_s']} 초 (대기 시간 포함)")
    if s["projected_estimate"]:
        p = s["projected_estimate"]
        print(f"  1,000건 추정   토큰 {p['total_tokens']:,}, 순차 {p['hours_sequential']} 시간"
              + (f", ${p['cost_usd']}" if p["cost_usd"] is not None else ""))
    if s["failed_reactions"]:
        print(f"  실패한 건      {', '.join(str(x) for x in s['failed_reactions'])}")


# ======================================================================================
# Entry point
# ======================================================================================

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="이상사례 건별 선별 경로의 시간과 비용 측정")
    ap.add_argument("--drug", default=DEFAULT_DRUG, help=f"화합물 이름. 기본 {DEFAULT_DRUG}")
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                    help=f"돌릴 이상사례 건수. 기본 {DEFAULT_LIMIT}. 호출이 곧 비용이다")
    ap.add_argument("--label", default=None, help="이 실행의 이름. 산출 파일명에 들어간다")
    ap.add_argument("--name-field", default="generic",
                    help="약물명 매칭 필드. generic, brand, medicinalproduct 중 하나")
    ap.add_argument("--base-url", default=None, help=f"기본 {DEFAULT_BASE_URL}")
    ap.add_argument("--model", default=None, help=f"기본 {DEFAULT_MODEL}")
    ap.add_argument("--api-key-env", default="NVIDIA_API_KEY",
                    help="키를 담은 환경변수 이름. 기본 NVIDIA_API_KEY")
    ap.add_argument("--sleep", type=float, default=1.0, help="호출 사이 대기 초. 기본 1.0")
    ap.add_argument("--judge", choices=["llm", "jev"], default="llm",
                    help="판정기. jev 는 TypeSafe Jev 를 쓰고 TYPESAFE_API_KEY 를 읽는다")
    ap.add_argument("--jev-threshold", type=float, default=0.5,
                    help="Jev noul 확률이 이 값 이상이면 사람에게 넘긴다. 기본 0.5")
    ap.add_argument("--offline", action="store_true",
                    help="캐시만 쓰고 LLM 을 부르지 않는다. 판정은 고정 규칙이 낸다")
    ap.add_argument("--out", default=DEFAULT_OUT_DIR, help=f"산출 디렉터리. 기본 {DEFAULT_OUT_DIR}")
    ap.add_argument("--timeout", type=float, default=120.0)
    ap.add_argument("--max-tokens", type=int, default=256)
    ap.add_argument("--price-in", type=float, default=None,
                    help="입력 백만 토큰당 달러. 주면 비용을 환산한다")
    ap.add_argument("--price-out", type=float, default=None, help="출력 백만 토큰당 달러")
    args = ap.parse_args(argv)

    if args.name_field not in ofda.NAME_FIELDS:
        print(f"--name-field 는 {sorted(ofda.NAME_FIELDS)} 중 하나여야 한다.", file=sys.stderr)
        return 2

    use_jev = args.judge == "jev" and not args.offline
    if use_jev:
        model = args.model or jev.DEFAULT_MODEL
        base_url = jev.BASE_URL
    else:
        model = "" if args.offline else (args.model or os.environ.get("MODEL_PLANNER") or DEFAULT_MODEL)
        base_url = "" if args.offline else (args.base_url or os.environ.get("NVIDIA_BASE_URL") or DEFAULT_BASE_URL)
    label = args.label or ("offline" if args.offline else model.split("/")[-1])
    mode = "rule-offline" if args.offline else ("jev" if use_jev else "llm")

    client = None
    if use_jev:
        # Jev speaks its own protocol, so there is no OpenAI client to build here. Check the
        # key early rather than after the first openFDA fetch has already spent time.
        if not (os.environ.get(jev.API_KEY_ENV) or "").strip():
            print(f"환경변수 {jev.API_KEY_ENV} 가 비어 있다.", file=sys.stderr)
            return 2
    elif not args.offline:
        key = (os.environ.get(args.api_key_env) or "").strip()
        if not key:
            print(f"환경변수 {args.api_key_env} 가 비어 있다. 키를 넣거나 --offline 으로 돌려라.",
                  file=sys.stderr)
            return 2
        try:
            from openai import OpenAI
        except ImportError:
            print("openai 패키지가 없다.", file=sys.stderr)
            return 2
        client = OpenAI(base_url=base_url, api_key=key, timeout=args.timeout)

    started_all = time.perf_counter()
    if args.offline:
        with cache_only_network(ofda):
            listing = fetch_events(args.drug, limit=args.limit, name_field=args.name_field,
                                   offline=True)
            print(f"{args.drug} 이상사례 {len(listing['events'])}건 (캐시 전용, 고정 규칙 판정)")
            if listing["errors"]:
                print(f"  목록 오류: {'; '.join(listing['errors'])}")
            rows = run_rows(args.drug, listing["events"], name_field=args.name_field,
                            client=None, sleep=0.0)
    else:
        listing = fetch_events(args.drug, limit=args.limit, name_field=args.name_field)
        print(f"{args.drug} 이상사례 {len(listing['events'])}건, 모델 {model}, 엔드포인트 {base_url}")
        if listing["errors"]:
            print(f"  목록 오류: {'; '.join(listing['errors'])}")
        rows = run_rows(args.drug, listing["events"], name_field=args.name_field,
                        client=client, model=model, timeout=args.timeout,
                        max_tokens=args.max_tokens, sleep=args.sleep,
                        judge=args.judge, jev_threshold=args.jev_threshold)
    wall_clock_s = time.perf_counter() - started_all

    summary = summarize(rows, price_in=args.price_in, price_out=args.price_out,
                        wall_clock_s=wall_clock_s)
    print_summary(label, summary)

    doc = {
        "label": label, "drug": args.drug, "mode": mode, "model": model, "base_url": base_url,
        "limit": args.limit, "name_field": args.name_field,
        "events_url": listing["url"], "events_total_terms": listing["total_terms"],
        "events_errors": listing["errors"],
        "prompt": SYSTEM_PROMPT if mode == "llm" else None,
        "jev_questions": JEV_QUESTIONS if mode == "jev" else None,
        "jev_threshold": args.jev_threshold if mode == "jev" else None,
        "ran_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "retrieved_at": now_iso(),
        "summary": summary, "rows": rows,
    }
    out_dir = Path(args.out)
    if not out_dir.is_absolute():
        out_dir = REPO_ROOT / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"triage_scale_{label}.json"
    tmp = out_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(out_path)
    print(f"\n  {out_path} ({out_path.stat().st_size:,}바이트)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
