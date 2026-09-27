#!/usr/bin/env python3
"""마크다운 문서를 공유용 docx 와 pdf 로 만든다.

pandoc 으로 docx 를 만들고 LibreOffice 로 pdf 를 뽑는다. 한글 폰트가 필요한
pdf 엔진(xelatex) 없이도 돌아간다.

사용:
  python3 scripts/make_docx.py <문서.md> [--out <폴더>] [--no-pdf]
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
