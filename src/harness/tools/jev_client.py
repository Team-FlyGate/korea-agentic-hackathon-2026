"""Framework-neutral client for the TypeSafe Jev evaluation model.

Jev answers typed questions with calibrated probabilities instead of prose, which is the
shape a triage gate needs: a number you can threshold, not a sentence you have to parse.
That makes it a candidate for the cheap first stage ahead of the expensive reasoning model.

It is not an OpenAI-compatible chat endpoint, so none of our existing model plumbing applies.
One POST carries the shared `state` and a map of named questions, and the reply carries one
answer per question under the same key.

Question types and the shape of their `criteria`:
  noul    a yes/no probability; criteria is optional, an object with `true` and `false`
  choice  one option out of a set; criteria maps each option name to its description
  score   an ordered scale; criteria is the list of levels, low to high

Sending a choice without `criteria` returns 422 naming the missing field, which is how these
shapes were confirmed against the running service rather than from documentation alone.

Pricing at the time of writing: input only, USD 0.042 per million tokens, output free. Cost
therefore scales with how much state is sent, so pass the numbers a decision needs and not a
whole record.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any

BASE_URL = "https://api.typesafe.ai/v1"
DEFAULT_MODEL = "jev-latest"
API_KEY_ENV = "TYPESAFE_API_KEY"


def systemone(state: Any, questions: dict[str, dict], *, model: str = DEFAULT_MODEL,
              api_key: str | None = None, base_url: str = BASE_URL,
              timeout: float = 60.0) -> dict[str, Any]:
    """Evaluate `state` against `questions` and return status, elapsed time and the reply.

    Errors are returned rather than raised. A refused call is data the caller needs to record,
    and the response body is the part that explains the refusal.
    """
    key = (api_key if api_key is not None else os.environ.get(API_KEY_ENV, "")).strip()
    if not key:
        return {"status": 0, "seconds": 0.0, "error": f"{API_KEY_ENV} 가 비어 있다"}

    payload = {"state": state, "model": model, "questions": questions}
    request = urllib.request.Request(
        f"{base_url}/systemone", data=json.dumps(payload).encode(), method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                 "Accept": "application/json"})

    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            raw = response.read().decode("utf-8", "replace")
            # TypeSafe support asks for this id, and it is the only handle on a call once the
            # process has exited.
            request_id = response.headers.get("x-typesafe-request-id")
            status = response.status
    except urllib.error.HTTPError as exc:
        return {"status": exc.code, "seconds": round(time.perf_counter() - started, 3),
                "error": exc.read().decode("utf-8", "replace")[:500],
                "request_id": exc.headers.get("x-typesafe-request-id")}
    except Exception as exc:  # noqa: BLE001
        return {"status": 0, "seconds": round(time.perf_counter() - started, 3),
                "error": f"{type(exc).__name__}: {exc}"}

    out: dict[str, Any] = {"status": status, "seconds": round(time.perf_counter() - started, 3),
                           "request_id": request_id}
    try:
        out["body"] = json.loads(raw)
    except json.JSONDecodeError:
        out["error"] = f"JSON 이 아닌 응답: {raw[:300]}"
    return out


def noul_probability(result: dict[str, Any], key: str) -> float | None:
    """Read one noul answer's probability, or None when the call or the key failed.

    None is distinct from 0.0 on purpose: a failed call must not read as a confident no.
    """
    answer = ((result.get("body") or {}).get("answers") or {}).get(key)
    if not isinstance(answer, dict):
        return None
    value = answer.get("noul")
    return float(value) if isinstance(value, (int, float)) else None


def usage_tokens(result: dict[str, Any]) -> dict[str, int | None]:
    """Normalise Jev's usage block to the prompt/completion names the benchmarks already use."""
    usage = (result.get("body") or {}).get("usage") or {}
    prompt = usage.get("input_tokens")
    completion = usage.get("output_tokens")
    total = None
    if isinstance(prompt, int) and isinstance(completion, int):
        total = prompt + completion
    return {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": total}
