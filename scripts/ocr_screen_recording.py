#!/usr/bin/env python3
"""Turn a phone screen recording of a scrolling chat into a readable transcript.

Teammates sometimes share a screen recording instead of exporting the chat log.
Reading such a video frame by frame is expensive, so this script samples frames,
runs Apple's Vision OCR on each one, and merges the lines back into one document.

Why Vision and not tesseract: Vision ships with macOS, recognises Korean out of
the box, and needs no language data files. It is reached through pyobjc, which is
installed in the analysis environment (/opt/anaconda3/envs/rag).

The merge step is the interesting part. While the user scrolls, consecutive
frames overlap heavily, so the same line shows up in several frames. Plain
deduplication would collapse genuinely repeated messages ("네", "감사합니다"),
so a line is only treated as a repeat when it reappears within a short window of
recently seen lines. That keeps real duplicates while dropping scroll overlap.

Usage:
    python scripts/ocr_screen_recording.py VIDEO [--every 1.5] [--out FILE]
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# Lines shorter than this are almost always UI chrome (badge counts, "1", icons).
MIN_LINE_CHARS = 2

# How many recently emitted lines to compare against when deciding whether a line
# is scroll overlap rather than a new message. Roughly one screenful of chat.
RECENT_WINDOW = 60


def extract_frames(video: Path, out_dir: Path, every: float) -> list[Path]:
    """Sample one frame every `every` seconds with ffmpeg."""
    if not shutil.which("ffmpeg"):
        sys.exit("ffmpeg not found. Install it or extract the frames by hand.")
    # -vsync 0 keeps ffmpeg from duplicating frames to hit a constant rate.
    subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(video),
         "-vf", f"fps=1/{every}", "-vsync", "0", str(out_dir / "f_%04d.png")],
        check=True,
    )
    return sorted(out_dir.glob("f_*.png"))


def ocr(path: Path, languages: tuple[str, ...]) -> list[str]:
    """Return the recognised lines of one image, top to bottom."""
    import Quartz
    import Vision

    url = Quartz.NSURL.fileURLWithPath_(str(path))
    source = Quartz.CGImageSourceCreateWithURL(url, None)
    image = Quartz.CGImageSourceCreateImageAtIndex(source, 0, None)

    request = Vision.VNRecognizeTextRequest.alloc().init()
    # Accurate is slower but the chat bubbles are small and low contrast.
    request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    request.setRecognitionLanguages_(list(languages))
    request.setUsesLanguageCorrection_(True)

    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(image, None)
    handler.performRequests_error_([request], None)

    rows = []
    for observation in request.results() or []:
        candidate = observation.topCandidates_(1)
        if not candidate:
            continue
        text = candidate[0].string().strip()
        if len(text) < MIN_LINE_CHARS:
            continue
        # Vision's origin is bottom-left, so a larger y means higher on screen.
        rows.append((observation.boundingBox().origin.y, text))

    rows.sort(key=lambda r: -r[0])
    return [text for _, text in rows]


def merge(frames: list[list[str]]) -> list[str]:
    """Stitch per-frame lines into one transcript, dropping scroll overlap."""
    merged: list[str] = []
    for lines in frames:
        for line in lines:
            if line in merged[-RECENT_WINDOW:]:
                continue
            merged.append(line)
    return merged


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path)
    parser.add_argument("--every", type=float, default=1.5,
                        help="seconds between sampled frames (default: 1.5)")
    parser.add_argument("--languages", default="ko-KR,en-US")
    parser.add_argument("--out", type=Path, help="write here instead of stdout")
    args = parser.parse_args()

    languages = tuple(s.strip() for s in args.languages.split(",") if s.strip())

    with tempfile.TemporaryDirectory() as tmp:
        frames = extract_frames(args.video, Path(tmp), args.every)
        print(f"프레임 {len(frames)}장 추출, OCR 시작", file=sys.stderr)
        per_frame = []
        for i, frame in enumerate(frames, 1):
            per_frame.append(ocr(frame, languages))
            if i % 10 == 0:
                print(f"  {i}/{len(frames)}", file=sys.stderr)

    text = "\n".join(merge(per_frame))
    if args.out:
        args.out.write_text(text, encoding="utf-8")
        print(f"저장: {args.out} ({len(text)}자)", file=sys.stderr)
    else:
        print(text)


if __name__ == "__main__":
    main()
