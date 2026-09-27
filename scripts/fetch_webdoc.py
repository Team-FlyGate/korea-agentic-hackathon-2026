#!/usr/bin/env python3
"""Fetch a web page, reduce it to readable text, and keep it with its fetch date.

Why this exists
---------------
Several claims in our notes came from reading a page once: the DLI lesson that says what
Module 3 requires, the Jev workbook chapter that documents the HTTP shape, a vendor site
that shows whether signup is open. Reading those in a throwaway shell leaves nothing behind,
so the claim cannot be re-checked and the page can change under us without anyone noticing.

This keeps the evidence. Every run records the URL, the HTTP status, the fetch time and the
extracted text, so a note can cite a file instead of a memory. Re-running the same command
refreshes it and any drift shows up as a diff.

It is deliberately dumb about extraction: scripts and styles are dropped, tags become line
breaks, and blank lines collapse. No readability heuristics, because a heuristic that decides
what is "main content" is exactly what silently loses the sentence you needed.

Pages rendered entirely by JavaScript come back nearly empty. That is reported rather than
worked around, since the honest conclusion in that case is "this needs a browser".

Usage:
  python3 scripts/fetch_webdoc.py <url> [<url> ...]
  python3 scripts/fetch_webdoc.py <url> --grep systemone --context 40
  python3 scripts/fetch_webdoc.py <url> --out-dir _local/research/web --name jev-ch18
"""
from __future__ import annotations

import argparse
import html as html_mod
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "_local" / "research" / "web"
UA = "Mozilla/5.0 (compatible; FlyGate-doc-fetch/1.0)"


def slugify(url: str) -> str:
    """Turn a URL into a filename that still says where it came from."""
    s = re.sub(r"^https?://", "", url)
    s = re.sub(r"[^A-Za-z0-9._/-]+", "-", s).strip("-/")
    return re.sub(r"[/]+", "_", s)[:120] or "page"


def to_text(raw: str) -> str:
    """Strip markup down to lines of text, keeping the reading order intact."""
    body = re.sub(r"<(script|style|noscript)[^>]*>.*?</\1>", " ", raw, flags=re.S | re.I)
    body = re.sub(r"<!--.*?-->", " ", body, flags=re.S)
    text = html_mod.unescape(re.sub(r"<[^>]+>", "\n", body))
    lines = [ln.strip() for ln in text.split("\n")]
    out: list[str] = []
    for ln in lines:
        if ln:
            out.append(re.sub(r"[ \t]{2,}", " ", ln))
        elif out and out[-1] != "":
            out.append("")
    return "\n".join(out).strip()


def fetch(url: str, timeout: float) -> tuple[int, str, str]:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
            return r.status, r.read().decode("utf-8", "replace"), ""
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace"), f"HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001
        return 0, "", f"{type(exc).__name__}: {exc}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("urls", nargs="+")
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--name", default=None, help="파일 이름을 직접 정한다(주소 하나일 때만)")
    ap.add_argument("--grep", default=None, help="이 말이 들어간 곳만 찍는다")
    ap.add_argument("--context", type=int, default=12, help="--grep 주변으로 보여 줄 줄 수")
    ap.add_argument("--timeout", type=float, default=40.0)
    a = ap.parse_args()

    if a.name and len(a.urls) > 1:
        print("--name 은 주소가 하나일 때만 쓴다.", file=sys.stderr)
        return 2

    a.out_dir.mkdir(parents=True, exist_ok=True)
    failures = 0
    for url in a.urls:
        status, raw, err = fetch(url, a.timeout)
        text = to_text(raw) if raw else ""
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        header = f"# {url}\n# fetched {stamp}  HTTP {status}  {len(raw)} bytes\n\n"
        path = a.out_dir / f"{a.name or slugify(url)}.txt"
        path.write_text(header + text, encoding="utf-8")

        rel = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path
        print(f"HTTP {status:<4} {len(text):>7}자  {rel}")
        if err:
            print(f"  {err}")
            failures += 1
        # A page that renders in the browser but arrives nearly empty is a JavaScript app.
        elif len(text) < 400:
            print("  본문이 거의 비었다. 자바스크립트로 그리는 페이지이므로 브라우저가 필요하다.")

        if a.grep and text:
            lines = text.split("\n")
            hits = [i for i, ln in enumerate(lines) if a.grep.lower() in ln.lower()]
            print(f"  '{a.grep}' {len(hits)}곳")
            for i in hits[:3]:
                lo, hi = max(0, i - 2), min(len(lines), i + a.context)
                print("  " + "\n  ".join(lines[lo:hi]))
                print("  ---")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
