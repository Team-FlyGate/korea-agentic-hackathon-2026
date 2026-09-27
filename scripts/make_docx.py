#!/usr/bin/env python3
"""Turn a Markdown file into a docx and a pdf for sharing with the team.

pandoc writes the docx, then LibreOffice converts that docx to pdf. We go through
LibreOffice on purpose: pandoc's own pdf writers need a LaTeX engine (xelatex) plus a
CJK-capable font, and neither is installed here. LibreOffice already ships fonts that
render Korean, so this path works on a plain machine.

Usage:
  python3 scripts/make_docx.py <document.md> [--out <folder>] [--no-pdf]
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

SOFFICE = "/Applications/LibreOffice.app/Contents/MacOS/soffice"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("src", type=Path)
    ap.add_argument("--out", type=Path, default=None, help="기본값은 원본 폴더의 dist/")
    ap.add_argument("--no-pdf", action="store_true")
    a = ap.parse_args()

    if not a.src.exists():
        sys.exit(f"원본이 없다: {a.src}")
    out = a.out or a.src.parent / "dist"
    out.mkdir(parents=True, exist_ok=True)
    docx = out / (a.src.stem + ".docx")

    if not shutil.which("pandoc"):
        sys.exit("pandoc 이 필요하다")
    subprocess.run(["pandoc", str(a.src), "-o", str(docx), "--from",
                    "markdown+pipe_tables", "--toc", "--toc-depth=2"], check=True)
    print(f"docx {docx} {docx.stat().st_size // 1024}KB")

    if not a.no_pdf:
        if not Path(SOFFICE).exists():
            print("pdf 를 건너뛴다: LibreOffice 가 없다")
            return 0
        subprocess.run([SOFFICE, "--headless", "--convert-to", "pdf",
                        "--outdir", str(out), str(docx)], check=True,
                       stdout=subprocess.DEVNULL)
        pdf = out / (a.src.stem + ".pdf")
        if pdf.exists():
            print(f"pdf  {pdf} {pdf.stat().st_size // 1024}KB")
        else:
            print("pdf 변환이 실패했다")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
