#!/usr/bin/env python3
"""Ask Jev to triage one FAERS adverse-event report, through either route that serves it.

Why this exists
---------------
The meeting settled on two things that meet here: a three-stage triage where a cheap judge
runs ahead of the expensive model, and the Korean causality algorithm as the scoring logic.
Jev is the candidate for that first stage because it answers typed questions with
probabilities instead of prose, which is exactly the shape a triage gate needs.

Before designing around it we need one real call on our own data, so this sends a single
report and asks three questions at once:

  needs_human   noul   should a safety reviewer look at this case before the queue does
  time_order    choice does the record support the algorithm's time-order item
  signal_score  score  how strong the disproportionality looks, on a 0-100 scale

Two routes, same question
-------------------------
  direct   POST https://api.typesafe.ai/v1/systemone   with TYPESAFE_API_KEY
  gateway  POST https://ai-gateway.vercel.sh/v1/systemone with AI_GATEWAY_API_KEY

The direct route needs a TypeSafe console account, and at the time of writing the site
offers only "Sign in" and "Contact sales", so self-serve signup appears closed. The gateway
route matters because Vercel lists `typesafe-ai/jev` among its public models, which makes it
reachable with a Vercel account alone. Whether the gateway accepts the native `systemone`
body is exactly what this script finds out: the model is typed "evaluation" rather than
"chat", so the usual chat-completions shape does not apply to it.

Both routes are probed and both outcomes recorded, including failures. A 401 is a useful
result: it says the request shape got far enough to be rejected for credentials alone.

Usage:
  python3 scripts/jev_triage_probe.py                 # probe every route with a key present
  python3 scripts/jev_triage_probe.py --route gateway
  python3 scripts/jev_triage_probe.py --dry-run       # print the request body, call nothing
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "eval" / "results"

ROUTES = {
    # name: (endpoint, environment variable holding the key, model id for that route)
    "direct": ("https://api.typesafe.ai/v1/systemone", "TYPESAFE_API_KEY", "jev-latest"),
    "gateway": ("https://ai-gateway.vercel.sh/v1/systemone", "AI_GATEWAY_API_KEY",
                "typesafe-ai/jev"),
}

# One real pair from our case work: niraparib and thrombocytopenia. The counts come from
# openFDA and the measures from our own oracle, so the state carries no free text a model
# could lean on. That is deliberate. We want to see the judgment made from the numbers the
# pipeline actually holds, not from a narrative we wrote for it.
STATE = {
    "drug": "NIRAPARIB",
    "reaction": "THROMBOCYTOPENIA",
    "faers_reports_for_drug": 22116,
    "reports_with_this_reaction": 4132,
    "contingency_2x2": {"a": 4132, "b": 17984, "c": 120000, "d": 20550574},
    "measures": {"PRR": 9.13, "ROR": 9.96, "chi_square": 1861.0},
    "label_status": "thrombocytopenia appears in the approved label",
    "record_fields_present": {
        "drug_start_date": True,
        "improved_after_withdrawal": True,
        "recurred_on_rechallenge": False,
    },
    "note": "Public FAERS carries no causality assessment and no narrative.",
}

QUESTIONS = {
    "needs_human": {
        "type": "noul",
        "instructions": (
            "Should a pharmacovigilance reviewer read this case before it goes to the "
            "automated queue? Judge only from the counts, the measures and which record "
            "fields exist."),
    },
    "time_order": {
        "type": "choice",
        "instructions": (
            "The Korean causality algorithm scores a time-order item: +3 when the record "
            "supports the order of dosing then reaction, -3 when it contradicts it, 0 when "
            "the information is absent. Which applies here?"),
        "choices": ["supports", "contradicts", "absent"],
    },
    "signal_score": {
        "type": "score",
        "instructions": (
            "Rate how strong the disproportionality evidence is on a 0 to 100 scale. A "
            "disproportionality measure is a screening signal, not a causal claim, so a "
            "labelled and expected reaction should not score high merely for being frequent."),
        "min": 0,
        "max": 100,
    },
}


def call(url: str, body: dict, key: str, timeout: float) -> dict:
    """POST the evaluation body and return status, elapsed time and the parsed reply.

    Errors are returned rather than raised: a refused call is data for the probe, and
    losing the response body would throw away the one thing that explains the refusal.
    """
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                 "Accept": "application/json"})
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
            raw = r.read().decode("utf-8", "replace")
            # The request id is worth keeping: TypeSafe support asks for it, and it is the
            # only handle on a call once the process has exited.
            request_id = r.headers.get("x-typesafe-request-id")
            status = r.status
    except urllib.error.HTTPError as exc:
        return {"status": exc.code, "seconds": round(time.time() - started, 2),
                "error": exc.read().decode("utf-8", "replace")[:500],
                "request_id": exc.headers.get("x-typesafe-request-id")}
    except Exception as exc:  # noqa: BLE001
        return {"status": 0, "seconds": round(time.time() - started, 2),
                "error": f"{type(exc).__name__}: {exc}"}
    out = {"status": status, "seconds": round(time.time() - started, 2),
           "request_id": request_id}
    try:
        out["body"] = json.loads(raw)
    except json.JSONDecodeError:
        out["body_text"] = raw[:500]
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--route", choices=[*ROUTES, "all"], default="all")
    ap.add_argument("--timeout", type=float, default=60.0)
    ap.add_argument("--dry-run", action="store_true",
                    help="요청 본문만 찍고 호출하지 않는다")
    a = ap.parse_args()

    names = list(ROUTES) if a.route == "all" else [a.route]
    doc: dict = {"ran_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 "state": STATE, "questions": QUESTIONS, "routes": {}}

    if a.dry_run:
        url, env, model = ROUTES[names[0]]
        print(json.dumps({"state": STATE, "model": model, "questions": QUESTIONS},
                         ensure_ascii=False, indent=1))
        return 0

    for name in names:
        url, env, model = ROUTES[name]
        key = (os.environ.get(env) or "").strip()
        if not key:
            print(f"{name:<8} 건너뜀. 환경변수 {env} 가 비어 있다")
            doc["routes"][name] = {"skipped": f"{env} 없음", "endpoint": url}
            continue
        result = call(url, {"state": STATE, "model": model, "questions": QUESTIONS},
                      key, a.timeout)
        doc["routes"][name] = {"endpoint": url, "model": model, **result}
        print(f"{name:<8} HTTP {result['status']} {result['seconds']}초")
        answers = (result.get("body") or {}).get("answers")
        if answers:
            for qk, ans in answers.items():
                print(f"    {qk:<13} {json.dumps(ans, ensure_ascii=False)[:120]}")
        elif result.get("error"):
            print(f"    {result['error'][:200]}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "jev_triage_probe.json"
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n{out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
