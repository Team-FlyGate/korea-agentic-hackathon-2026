#!/usr/bin/env python3
"""Render the team's submission markdown into the single PDF the entry form accepts.

Why a separate script
---------------------
`make_submission_pdf.py` builds our own submission from this repository's result files and
refuses to run when a number is missing. That is the right behaviour for text we wrote. The
final entry, though, is the team's `docs/SUBMISSION.md` in Team-FlyGate/Project-FlyGate, and
that document is already reviewed and already carries its own measured numbers. Re-deriving
them here would only add a way to drift apart, so this script renders the markdown as given
and adds nothing but a cover page.

The form takes one file, so every address the reviewers need goes on that cover.

Rendering goes through headless Chrome, the same path as the other PDF script, because it
is the one renderer on this machine that lays out Korean text with Pretendard correctly.

Usage:
  python scripts/render_submission_md.py --md ../Project-FlyGate/docs/SUBMISSION.md
  python scripts/render_submission_md.py --md <path> --out ~/Downloads/entry.pdf
"""

from __future__ import annotations

import argparse
import html as html_mod
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHROME = os.environ.get("CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
FONT_DIR = ROOT / "assets" / "video" / "fonts"

TEAM_NAME = "FlyGate"
PROJECT_NAME = "FlyGate"
# Pulled from the team repository so the entry looks like the dashboard the judges open.
# Paths are resolved against the markdown's repository root at build time.
# Stored as globs, not fixed names. The team bumps these files by version (v2.0.0 → v2.1.0)
# several times a day, and a hard-coded name would drop the figure without saying so. The
# newest match wins; `resolve_image` reports when nothing matches.
HERO_IMAGE = "docs/images/flygate-hero_v*.png"
ARCH_IMAGE = "docs/images/flygate-architecture_v*.png"
# The team mark. It lives in this repository because it arrived as a chat image, not in the
# demo repo. White background and no alpha, so it only goes on a light page.
LOGO_IMAGE = ROOT / "assets" / "flygate-logo.png"
CALLLOG_IMAGE = "docs/images/nvidia-call-log_v*.png"
# The CLI home screen. It came from the team chat rather than the demo repository, so it is
# kept here; swap in a repo path once the team commits one.
CLI_IMAGE = ROOT / "assets" / "flygate-cli.png"

# Brand colours lifted from the demo's web/src/index.css so print and screen agree.
INK = "#04060c"
INK_2 = "#0b1122"
NVIDIA_GREEN = "#76b900"
CYAN = "#37e6ff"

# The headline numbers. Every one is checked against the source documents before the PDF is
# built: a figure that no longer appears in the team's own text is a figure we must not
# print. `needle` is what has to be found, `value` and `label` are what the reader sees.
HIGHLIGHTS = [
    ("247/250", "가린 조건에서 검토에 닿은 중대 사례", "247/250"),
    ("302 → 138", "사람이 먼저 볼 일의 감소", "138"),
    ("296 ms", "규제 용어 7문항 판단 한 번", "296"),
    ("0.767", "Boltz-2 친화도 순위 상관 (ChEMBL)", "0.767"),
    ("20/20", "OpenShell 샌드박스 스모크 통과", "20/20"),
    ("92.0%", "문헌 설계 판정의 MEDLINE 색인 일치", "92.0%"),
]

COVER_LINKS = [
    ("데모", "https://project-flygate.vercel.app"),
    ("저장소", "https://github.com/Team-FlyGate/Project-FlyGate"),
    ("쇼릴 (4분 10초)", "https://project-flygate.vercel.app/showreel/FlyGate_showreel_v4.3.0.html"),
]


def font_faces() -> str:
    """Embed Pretendard by absolute file URL so the HTML renders the same from any cwd."""
    out = []
    for name, weight in (("Regular", 400), ("Medium", 500), ("SemiBold", 600), ("Bold", 700)):
        for ext in ("otf", "ttf"):
            path = FONT_DIR / f"Pretendard-{name}.{ext}"
            if path.exists():
                out.append(f"@font-face{{font-family:Pretendard;src:url('{path.as_uri()}');"
                           f"font-weight:{weight};font-style:normal;font-display:block}}")
                break
    return "\n".join(out)


# The source document is written to be pasted into the entry form, so it carries scaffolding
# that only matters while writing: the form's field numbering, the character limits, and the
# running character counts. A reviewer reading the PDF gets nothing from those, and leaving
# them in makes the portfolio read like a transcript of the form rather than a document. So
# the headings become plain section names and the counter lines are dropped.
FORM_HEADINGS = {
    "(1) 해결하고자 했던 문제 (Problem Definition, 300자 내외)": "문제 정의",
    "(2) 서비스 소개 및 주요 기능 (Solution, 500자 내외)": "해법과 주요 기능",
    "(3) 활용한 핵심 기술 및 AI 모델 (Tech Stack)": "활용한 핵심 기술과 AI 모델",
}
DROP_LINES = (
    re.compile(r"^<sub>\s*공백 포함"),        # running character count
    re.compile(r"^#\s*해커톤 제출 문구\s*$"),  # the cover already names the document
)


# The source prose carries its evidence inline and unmarked, so a reviewer skimming the page
# sees no difference between a measured result and the sentence around it. These are the
# results the entry stands on; each is emphasised once, at its first appearance, and only
# outside code spans so model identifiers are left alone. Nothing else is touched: the
# wording stays exactly as the team wrote it.
EMPHASISE = (
    "247/250", "302건→138건", "296 ms", "2,286 ms", "20/20", "0.960", "92.0%",
    "0.767", "1.0 Å", "0.71 Å", "0.65 → 0.85", "0.58 → 0.84", "7/7", "8/8",
    "42만 건", "p=0.004",
)


def emphasise_results(md: str) -> str:
    """Bold each headline number once, skipping anything inside backticks.

    A token that no longer appears means the team changed that result. That is not fatal for
    the PDF, but it must not pass unnoticed, so the misses are listed at the end of the run.
    """
    missed = []
    for token in EMPHASISE:
        idx = 0
        while True:
            idx = md.find(token, idx)
            if idx == -1:
                break
            # Skip a hit that sits inside a code span or is already emphasised.
            if md.count("`", 0, idx) % 2 == 1 or md[max(0, idx - 2):idx] == "**":
                idx += len(token)
                continue
            md = md[:idx] + f"**{token}**" + md[idx + len(token):]
            break
        else:
            missed.append(token)
    if missed:
        print("  본문에서 못 찾아 강조하지 못한 수치: " + ", ".join(missed), file=sys.stderr)
        print("  원본이 바뀌었을 수 있다. EMPHASISE 목록을 맞춘다.", file=sys.stderr)
    return md


MALECNS_BULLET = ("- MaleCNS 수컷 초파리 중추신경계 커넥텀(뉴런 약 166,700개, Janelia FlyEM · "
                  "Cambridge Drosophila Connectomics Group 공개, https://male-cns.janelia.org/)")


def add_missing_sources(md: str) -> str:
    """Add the connectome to the data list when the source document leaves it out.

    FlyDiscovery's fly-brain routing is built on MaleCNS, and an entry judged on "which
    technology did you use" should not omit the dataset the first half of the project runs
    on. The team was told to add it to SUBMISSION.md; until that lands, it goes in here so
    the two do not disagree by accident, and the insert is skipped once the source has it.
    """
    if "MaleCNS" in md or "male-cns" in md:
        return md
    anchor = "- Python 3.12, DuckDB"
    idx = md.find(anchor)
    if idx == -1:
        print("  데이터 목록을 찾지 못해 MaleCNS 를 넣지 못했다.", file=sys.stderr)
        return md
    print("  원본에 MaleCNS 가 없어 데이터 목록에 넣었다. SUBMISSION.md 에도 넣어야 한다.",
          file=sys.stderr)
    return md[:idx] + MALECNS_BULLET + "\n" + md[idx:]


def strip_form_scaffolding(md: str) -> str:
    """Turn the form-authoring document into something that reads as a portfolio."""
    out = []
    for line in md.splitlines():
        if any(pat.match(line.strip()) for pat in DROP_LINES):
            continue
        head = re.match(r"^(#{1,6})\s+(.*)$", line.strip())
        if head and head.group(2) in FORM_HEADINGS:
            out.append(f"{head.group(1)} {FORM_HEADINGS[head.group(2)]}")
            continue
        out.append(line)
    return "\n".join(out)


def md_to_html(md: str) -> str:
    """A small markdown subset: headings, tables, lists, bold, code, links.

    A full markdown library is not installed here and the submission document only uses
    these constructs. Anything unrecognised falls through as a paragraph rather than being
    dropped, so nothing in the source silently disappears from the PDF.
    """
    def inline(s: str) -> str:
        s = html_mod.escape(s)
        s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
        s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
        # Markdown links are converted first and parked behind placeholders. Without that,
        # the bare-URL autolinker below would run again over the href it just produced and
        # emit nested anchors, which is how [MaleCNS](https://…) came out mangled.
        parked: list[str] = []

        def park(m: re.Match) -> str:
            parked.append(f'<a href="{m.group(2)}">{m.group(1)}</a>')
            return f"\x00{len(parked) - 1}\x00"

        s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", park, s)
        s = re.sub(r"&lt;sub&gt;(.*?)&lt;/sub&gt;", r'<span class="sub">\1</span>', s)
        s = re.sub(r"(?<!\w)(https?://[^\s<)]+)", r'<a href="\1">\1</a>', s)
        return re.sub(r"\x00(\d+)\x00", lambda m: parked[int(m.group(1))], s)

    out: list[str] = []
    lines = md.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            i += 1
            continue

        heading = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if heading:
            level = len(heading.group(1))
            out.append(f"<h{level}>{inline(heading.group(2))}</h{level}>")
            i += 1
            continue

        # A table is a header row, a separator row of dashes, then body rows.
        if stripped.startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|[\s:|-]+\|\s*$", lines[i + 1]):
            def cells(row: str) -> list[str]:
                return [c.strip() for c in row.strip().strip("|").split("|")]
            head = cells(stripped)
            i += 2
            body = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                body.append(cells(lines[i].strip()))
                i += 1
            th = "".join(f"<th>{inline(c)}</th>" for c in head)
            rows = "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>" for r in body)
            out.append(f"<table><thead><tr>{th}</tr></thead><tbody>{rows}</tbody></table>")
            continue

        if re.match(r"^[-*]\s+", stripped):
            items = []
            while i < len(lines) and re.match(r"^\s*[-*]\s+", lines[i]):
                items.append(f"<li>{inline(re.sub(r'^\s*[-*]\s+', '', lines[i]))}</li>")
                i += 1
            out.append("<ul>" + "".join(items) + "</ul>")
            continue

        if stripped == "---":
            out.append("<hr>")
            i += 1
            continue

        para = [stripped]
        i += 1
        while i < len(lines) and lines[i].strip() and not re.match(r"^(#{1,6}\s|\||[-*]\s|---$)", lines[i].strip()):
            para.append(lines[i].strip())
            i += 1
        text = ' '.join(para)
        # A paragraph that is nothing but one bold run is a group label, not prose.
        klass = ' class="grouplabel"' if re.fullmatch(r"\*\*[^*]+\*\*", text.strip()) else ""
        out.append(f"<p{klass}>{inline(text)}</p>")

    return "\n".join(out)


CSS = f"""
@page {{ size: A4; margin: 17mm 15mm 15mm; }}
@page :first {{ margin: 0; }}
html, body {{ margin:0; padding:0; }}
body {{ font-family: Pretendard, -apple-system, sans-serif; font-size: 9.9pt;
       line-height: 1.75; color:#1d2836; letter-spacing:-.004em;
       -webkit-print-color-adjust: exact; word-break:keep-all; }}

/* ---------- cover ---------- */
.cover {{ position:relative; width:210mm; height:297mm; background:{INK}; color:#e8eefc;
          overflow:hidden; }}
.cover .hero {{ display:block; width:210mm; }}
.cover-body {{ padding:16mm 18mm 0; }}
.kicker {{ color:{CYAN}; font-size:8.4pt; letter-spacing:.14em; text-transform:uppercase;
          margin:0 0 9mm; font-weight:600; }}
.kicker .x {{ color:#6f7d9e; }}
.lead {{ font-size:19pt; color:#f4f7ff; margin:0; font-weight:600; letter-spacing:-.015em; }}
.sub-lead {{ font-size:11pt; color:{NVIDIA_GREEN}; margin:3.5mm 0 0; font-weight:600; }}
.sub-lead .plus {{ color:#3f4d6b; margin:0 1.5mm; }}
.blurb {{ font-size:9.6pt; color:#a9b6d3; margin:5mm 0 0; max-width:158mm; line-height:1.7; }}
.rule {{ height:2px; width:26mm; background:{NVIDIA_GREEN}; margin:9mm 0 7mm; }}
.links {{ width:100%; border-collapse:collapse; font-size:8.8pt; }}
.links td {{ border:0; padding:1.7mm 0; vertical-align:top; }}
.links td.k {{ width:40mm; color:#6f7d9e; white-space:nowrap; }}
.links td.v a {{ color:#a9b6d3; text-decoration:none; }}
.stats.dark {{ display:grid; grid-template-columns:repeat(3, 1fr); gap:3mm; margin-top:10mm; }}
.stats.dark .stat {{ background:rgba(255,255,255,.035); border:1px solid rgba(169,182,211,.16);
          border-top:2px solid {NVIDIA_GREEN}; border-radius:3px; padding:3.6mm 3.4mm 3.2mm; }}
.stats.dark .stat .n {{ font-size:14.5pt; font-weight:700; color:#f4f7ff; margin:0 0 1.4mm;
          letter-spacing:-.02em; line-height:1.05; }}
.stats.dark .stat .l {{ font-size:7.2pt; color:#8a97b5; margin:0; line-height:1.42; }}
.date {{ margin-top:10mm; color:#5b6885; font-size:8.6pt; }}

/* ---------- highlights ---------- */
.eyebrow {{ font-size:8.4pt; letter-spacing:.16em; text-transform:uppercase;
          color:{NVIDIA_GREEN}; font-weight:700; margin:0 0 2mm; }}
.h-big {{ font-size:22pt; font-weight:700; letter-spacing:-.02em; margin:0 0 7mm;
          border:0; padding:0; color:{INK_2}; }}
.archpage {{ position:relative; height:262mm; overflow:hidden; }}
.archpage .logo {{ position:absolute; top:-2mm; right:0; width:21mm; height:auto; }}
.archfig {{ width:100%; height:206mm; background-repeat:no-repeat; background-position:center top;
          background-size:contain; }}
.archnote {{ font-size:9pt; color:#5c6a7d; margin:0 0 5mm; max-width:160mm; line-height:1.7; }}

.arch figcaption {{ font-size:8.2pt; color:#8a94a6; margin-top:2.4mm; }}

/* ---------- body ---------- */
.pagebreak {{ page-break-after: always; height:0; overflow:hidden; }}
h1 {{ font-size:17pt; font-weight:700; margin:0 0 5mm; letter-spacing:-.015em; color:{INK_2}; }}
h2 {{ font-size:14pt; font-weight:700; margin:10mm 0 4mm; padding:0 0 2mm 0;
     letter-spacing:-.02em; border-bottom:1.6px solid {NVIDIA_GREEN};
     page-break-after:avoid; color:{INK_2}; }}
h3 {{ font-size:10.6pt; font-weight:600; margin:6.5mm 0 2.2mm; letter-spacing:-.01em;
     page-break-after:avoid; color:{INK_2}; }}
p {{ margin:0 0 3.4mm; max-width:170mm; }}
ul {{ margin:0 0 4mm; padding-left:5mm; }}
p.grouplabel {{ font-weight:700; color:{INK_2}; font-size:10.2pt; margin:7mm 0 2.6mm;
          padding-left:2.6mm; border-left:2.4px solid {NVIDIA_GREEN}; background:none; }}
p.grouplabel strong {{ background:none; font-weight:700; }}
li {{ margin-bottom:1.8mm; padding-left:.6mm; }}
li::marker {{ color:{NVIDIA_GREEN}; }}
strong {{ font-weight:600; color:{INK_2};
          background:linear-gradient(transparent 62%, rgba(118,185,0,.22) 62%); }}
code {{ font-family:"SF Mono",Menlo,monospace; font-size:8.3pt; background:#eef2f7;
       padding:.3mm 1.1mm; border-radius:2px; color:#2b3a4d; letter-spacing:0; }}
a {{ color:#1f6feb; text-decoration:none; word-break:break-all; }}
table {{ width:100%; border-collapse:collapse; margin:0 0 4mm; font-size:8.4pt;
        page-break-inside:auto; table-layout:fixed; }}
tr {{ page-break-inside:avoid; }}
thead {{ display:table-header-group; }}
td, th {{ word-break:break-word; }}
th {{ text-align:left; background:#eef4e6; border:1px solid #dbe3ea;
     border-bottom:1.6px solid {NVIDIA_GREEN}; padding:2mm 2.4mm;
     font-weight:600; color:{INK_2}; }}
tbody tr:nth-child(even) td {{ background:#fafbfc; }}
.links td, .links tbody tr:nth-child(even) td {{ background:transparent; }}
td {{ border:1px solid #e3e8ee; padding:2mm 2.4mm; vertical-align:top; }}
hr {{ border:0; border-top:1px solid #e3e8ee; margin:6mm 0; }}
.sub {{ color:#8a94a6; font-size:8.4pt; }}
"""


def resolve_image(repo_root: Path, pattern: str) -> Path | None:
    """Return the newest file matching the pattern, or None after saying what was missed."""
    hits = sorted(repo_root.glob(pattern))
    if not hits:
        print(f"  그림 없음: {pattern}. 그 쪽을 건너뛴다.", file=sys.stderr)
        return None
    if len(hits) > 1:
        print(f"  {pattern} → {hits[-1].name} (후보 {len(hits)}개 중 최신)", file=sys.stderr)
    return hits[-1]


def check_highlights(sources: list[str]) -> None:
    """Refuse to print a headline number that the team's own documents no longer contain."""
    joined = "\n".join(sources)
    missing = [v for v, _, needle in HIGHLIGHTS if needle not in joined]
    if missing:
        sys.exit("아래 수치를 원본 문서에서 찾지 못했다. 고치기 전에는 PDF 를 만들지 않는다: "
                 + ", ".join(missing))


def stat_cards() -> str:
    """The measured numbers, as cards. Used on the cover so the first page carries evidence."""
    return "".join(
        f'<div class="stat"><p class="n">{html_mod.escape(v)}</p>'
        f'<p class="l">{html_mod.escape(label)}</p></div>'
        for v, label, _ in HIGHLIGHTS)


def cover(repo_root: Path) -> str:
    """Dark cover page: the team's own hero banner, then every address a reviewer needs.

    The banner already carries the wordmark and the English tagline, so the cover does not
    repeat them. Cropping it was worse: the artwork has type baked in and any crop cut words
    in half.
    """
    hero = resolve_image(repo_root, HERO_IMAGE)
    art = f'<img class="hero" src="{hero.as_uri()}" alt="FlyGate">' if hero else ""
    rows = "".join(f'<tr><td class="k">{html_mod.escape(k)}</td>'
                   f'<td class="v"><a href="{v}">{html_mod.escape(v)}</a></td></tr>'
                   for k, v in COVER_LINKS)
    return f"""<section class="cover">
  {art}
  <div class="cover-body">
    <p class="kicker">NVIDIA <span class="x">&times;</span> 패스트캠퍼스 &nbsp;·&nbsp; Korea Agentic AI Hackathon 2026</p>
    <p class="lead">분자에서 환자까지, 추론보다 근거가 먼저.</p>
    <p class="sub-lead">FlyDiscovery <span class="plus">+</span> FlyVigilance</p>
    <p class="blurb">시판 전 표적 결합과 시판 후 이상사례를 한 에이전트로 잇고,
       나온 주장이 근거를 넘었는지 검사합니다.</p>
    <div class="rule"></div>
    <table class="links">{rows}</table>
    <div class="stats dark">{stat_cards()}</div>
    <p class="date">팀 {TEAM_NAME} &nbsp;·&nbsp; {date.today().isoformat()}</p>
  </div>
</section>
<div class="pagebreak"></div>"""


def architecture(repo_root: Path) -> str:
    """One page for the structure diagram. It reads better large than squeezed beside text."""
    arch = resolve_image(repo_root, ARCH_IMAGE)
    if not arch:
        return ""
    mark = (f'<img src="{LOGO_IMAGE.as_uri()}" alt="Team FlyGate" '
            f'style="position:absolute;top:-3mm;right:0;width:22mm;height:auto">'
            if LOGO_IMAGE.exists() else "")
    return f"""<section class="archpage" style="position:relative;height:262mm">
  {mark}
  <p class="eyebrow">Two stages. One evidence trail.</p>
  <h2 class="h-big">구조</h2>
  <p class="archnote">NemoClaw 배포 구성에서 OpenShell 안의 OpenClaw 에이전트가 flygate CLI로
  FlyDiscovery와 FlyVigilance를 실행합니다. 근거 ID와 숫자 대조, 해석과 안전 검사를 거쳐
  사람이 검토합니다.</p>
  <div style="width:100%;height:204mm;background:url('{arch.as_uri()}') no-repeat center top;
       background-size:contain"></div>
</section>
<div class="pagebreak"></div>"""


def evidence_table(repo_root: Path) -> str:
    """Lift the team README's evidence map into the PDF.

    The organiser asked that the entry make plain "which technology was used and how". The
    README answers that with one table per NVIDIA technology: the code that calls it, the
    screen that shows it, the diagram it sits in, and the logged request id. That table is
    not in SUBMISSION.md, so without this the PDF would leave the question half answered.

    It is read rather than copied so it follows the README, which the team edits all day.
    """
    readme = repo_root / "README.md"
    if not readme.exists():
        print("  README 를 찾지 못해 증거 대응표를 건너뛴다.", file=sys.stderr)
        return ""
    text = readme.read_text(encoding="utf-8")
    start = text.find("### 증거 대응표")
    if start == -1:
        print("  README 에 증거 대응표가 없다. 그 쪽을 건너뛴다.", file=sys.stderr)
        return ""
    end = text.find("###", start + 3)
    block = text[start:end if end != -1 else len(text)]
    # Keep the table only. The surrounding prose duplicates the page's own note.
    rows = [ln for ln in block.splitlines() if ln.strip().startswith("|")]
    if len(rows) < 3:
        return ""
    return f"""<div class="pagebreak"></div>
<section>
  <p class="eyebrow">What we used, and where</p>
  <h2 class="h-big">NVIDIA 기술 활용 내역</h2>
  <p class="archnote">기술 하나하나에 그것을 부르는 코드와 동작이 보이는 화면, 구조도에서의
  위치, 실제 호출 기록을 나란히 두었습니다. <code>#/…</code> 로 적은 것은 데모 대시보드의
  메뉴 주소입니다.</p>
  {md_to_html(chr(10).join(rows))}
</section>"""


def cli_page() -> str:
    """The agent as a person actually runs it.

    Everything else in this PDF is a result or a diagram. This page shows the thing being
    used: one install command, seven tools, and the same commands the sandboxed agent calls.
    """
    if not CLI_IMAGE.exists():
        print("  CLI 화면을 찾지 못해 그 쪽을 건너뛴다.", file=sys.stderr)
        return ""
    return f"""<div class="pagebreak"></div>
<section style="position:relative;height:262mm">
  <p class="eyebrow">Run it yourself</p>
  <h2 class="h-big">FlyGate Agent CLI</h2>
  <p class="archnote">저장소를 받아 <code>./scripts/install_flygate.sh</code> 한 줄이면 설치가
  끝납니다. 명령 아홉 개가 모두 근거 ID를 붙인 JSON을 내고, OpenShell 샌드박스 안의 OpenClaw
  에이전트도 사람과 똑같은 명령을 씁니다. 설치 안내는
  <a href="https://project-flygate.vercel.app/#/cli">project-flygate.vercel.app/#/cli</a>
  에 있습니다.</p>
  <div style="width:100%;height:172mm;background:url('{CLI_IMAGE.as_uri()}') no-repeat center top;
       background-size:contain"></div>
</section>"""


def call_log(repo_root: Path) -> str:
    """Closing page: the call log screen.

    The entry form accepts a single file, so evidence that would otherwise be an attachment
    has to live inside this PDF. The call log is the one the organiser named, and it is also
    the hardest to fake: every line carries the request id NVIDIA issued.
    """
    shot = resolve_image(repo_root, CALLLOG_IMAGE)
    if not shot:
        return ""
    return f"""<div class="pagebreak"></div>
<section style="position:relative;height:262mm">
  <p class="eyebrow">Evidence you can check</p>
  <h2 class="h-big">NVIDIA 호출 기록</h2>
  <p class="archnote">쓴 NVIDIA 기술마다 실제로 한 번씩 불러 요청 ID를 남겼습니다.
  기록에 키와 프롬프트, 응답 본문은 넣지 않고 시각과 엔드포인트, 모델, 상태, 지연, 요청 ID,
  토큰 수만 남깁니다. 실패한 호출도 지우지 않고 그대로 둡니다. 표 전체와 이전 실측은
  <a href="https://github.com/Team-FlyGate/Project-FlyGate/blob/main/docs/NVIDIA_CALL_LOG_v1.0.0.md">docs/NVIDIA_CALL_LOG_v1.0.0.md</a>,
  화면은 <a href="https://project-flygate.vercel.app/#/calls">project-flygate.vercel.app/#/calls</a>
  에 있습니다.</p>
  <div style="width:100%;height:196mm;background:url('{shot.as_uri()}') no-repeat center top;
       background-size:contain"></div>
</section>"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--md", type=Path, required=True, help="SUBMISSION.md 경로")
    parser.add_argument("--out", type=Path,
                        default=Path.home() / "Downloads" / f"[NVIDIA 해커톤_{TEAM_NAME}_{PROJECT_NAME}].pdf")
    parser.add_argument("--keep-html", action="store_true", help="중간 HTML 을 남긴다")
    args = parser.parse_args()

    md_path = args.md.expanduser().resolve()
    if not md_path.exists():
        sys.exit(f"markdown 을 찾지 못했다: {md_path}")

    md_text = md_path.read_text(encoding="utf-8")
    # The markdown sits in <repo>/docs/, so its repository root is two levels up. Images and
    # the numbers we check against are read from there.
    repo_root = md_path.parent.parent
    readme = repo_root / "README.md"
    check_highlights([md_text, readme.read_text(encoding="utf-8") if readme.exists() else ""])

    body = md_to_html(emphasise_results(add_missing_sources(strip_form_scaffolding(md_text))))
    html = (f"<!doctype html><html lang='ko'><head><meta charset='utf-8'>"
            f"<title>{PROJECT_NAME}</title><style>{font_faces()}\n{CSS}</style></head>"
            f"<body>{cover(repo_root)}{architecture(repo_root)}{body}"
            f"{evidence_table(repo_root)}{cli_page()}{call_log(repo_root)}</body></html>")

    out = args.out.expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    html_path = out.with_suffix(".html")
    html_path.write_text(html, encoding="utf-8")

    if not Path(CHROME).exists():
        sys.exit(f"Chrome 을 찾지 못했다: {CHROME}. CHROME 환경변수로 경로를 지정한다.")
    proc = subprocess.run(
        [CHROME, "--headless", "--disable-gpu", "--no-pdf-header-footer",
         f"--print-to-pdf={out}", html_path.as_uri()],
        capture_output=True, text=True)
    if proc.returncode != 0 or not out.exists():
        sys.exit(f"Chrome 이 PDF 를 만들지 못했다 (rc={proc.returncode}). {proc.stderr[-400:]}")

    if not args.keep_html:
        html_path.unlink()
    print(f"만들었다: {out} ({out.stat().st_size/1024:.0f} KB)", file=sys.stderr)


if __name__ == "__main__":
    main()
