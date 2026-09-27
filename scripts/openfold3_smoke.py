#!/usr/bin/env python3
"""Predict a protein structure with the NVIDIA MSA-Search and OpenFold3 NIMs.

This follows the request format documented by the NVIDIA skill
`msa-structure-prediction-pipeline`, installed under
`.agents/skills/msa-structure-prediction-pipeline/SKILL.md`.

Two hosted calls in sequence: MSA-Search (ColabFold) retrieves an alignment for the query
sequence, and OpenFold3 folds the sequence using that alignment. `--no-msa` skips the
search and sends the query sequence as its own single-entry alignment. That mode exists to
check credentials and the payload shape in a few seconds, not to produce evidence: the
skill's own notes call a single-sequence prediction "weak evidence for a production-quality
fold", and our measurement agrees (pLDDT 31.9 without an alignment against 90.1 with one).

Running both modes is itself the interesting result. Confidence scores move with the input
alignment, not with the truth of the molecule, which is what our overclaim rule about
folding confidence asserts. See docs/notes/openfold3-2026-09-27.md.

Usage:
  python3 scripts/openfold3_smoke.py --no-msa   # fast check of credentials and payload
  python3 scripts/openfold3_smoke.py            # full pipeline, alignment first
  python3 scripts/openfold3_smoke.py --pdb-id 4R6E --chain A
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "eval" / "results"
MSA_URL = "https://health.api.nvidia.com/v1/biology/colabfold/msa-search/predict"
OF3_URL = "https://health.api.nvidia.com/v1/biology/openfold/openfold3/predict"


def rel(path: Path) -> str:
    """Show a path relative to the repository, or as-is when it sits outside (tests use tmp)."""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def post(url: str, payload: dict, key: str, timeout: float) -> tuple[int, dict | str, float]:
    body = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json",
        "Accept": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
            raw = r.read().decode("utf-8", "replace")
            status = r.status
    except urllib.error.HTTPError as e:  # noqa: PERF203
        return e.code, e.read().decode("utf-8", "replace")[:600], time.time() - t0
    except Exception as exc:  # noqa: BLE001
        return 0, f"{type(exc).__name__}: {exc}", time.time() - t0
    try:
        return status, json.loads(raw), time.time() - t0
    except json.JSONDecodeError:
        return status, raw[:600], time.time() - t0


def sequence_from_rcsb(pdb_id: str, chain: str) -> str:
    url = f"https://www.rcsb.org/fasta/entry/{pdb_id}"
    with urllib.request.urlopen(url, timeout=30) as r:  # noqa: S310
        text = r.read().decode()
    blocks = [b for b in text.split(">") if b.strip()]
    for b in blocks:
        head, *rest = b.splitlines()
        if f"Chains {chain}" in head or f"Chain {chain}" in head or f"{chain}," in head:
            return "".join(rest).strip()
    head, *rest = blocks[0].splitlines()
    return "".join(rest).strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdb-id", default="4R6E", help="서열을 가져올 PDB 엔트리")
    ap.add_argument("--chain", default="A")
    ap.add_argument("--no-msa", action="store_true", help="정렬 없이 서열만 넣는다")
    ap.add_argument("--e-value", type=float, default=0.0001)
    ap.add_argument("--timeout", type=float, default=900.0)
    ap.add_argument("--label", default=None)
    a = ap.parse_args()

    key = (os.environ.get("NVIDIA_API_KEY") or "").strip()
    if not key:
        print("NVIDIA_API_KEY 가 비어 있다.", file=sys.stderr)
        return 2

    seq = sequence_from_rcsb(a.pdb_id, a.chain)
    print(f"{a.pdb_id} 사슬 {a.chain}: {len(seq)} 잔기")

    doc: dict = {"ran_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 "pdb_id": a.pdb_id, "chain": a.chain, "residues": len(seq),
                 "msa_used": not a.no_msa, "skill": "msa-structure-prediction-pipeline"}

    msa_data: dict = {}
    if not a.no_msa:
        print("1단계 MSA-Search ...", flush=True)
        status, body, secs = post(MSA_URL, {"sequence": seq, "e_value": a.e_value,
                                            "iterations": 1, "databases": ["Uniref30_2302"],
                                            "output_alignment_formats": ["a3m"]}, key, a.timeout)
        doc["msa"] = {"status": status, "seconds": round(secs, 1)}
        print(f"  HTTP {status}, {secs:.1f}초")
        if status != 200 or not isinstance(body, dict):
            doc["msa"]["error"] = body if isinstance(body, str) else str(body)[:400]
            OUT_DIR.mkdir(parents=True, exist_ok=True)
            out = OUT_DIR / f"openfold3_smoke_{a.label or 'msa-failed'}.json"
            out.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"  본문: {str(body)[:300]}")
            return 1
        # The database key in the response does not match the name we asked for. We requested
        # "Uniref30_2302" and the skill example reads `alignments["uniref30"]`, which raises
        # KeyError against the real reply. Record whichever keys came back, then take the first
        # one that carries an a3m alignment.
        alignments = body.get("alignments", {})
        doc["msa"]["databases_returned"] = sorted(alignments)
        pick = next((k for k, v in alignments.items() if "a3m" in v), None)
        if pick is None:
            doc["msa"]["error"] = f"a3m 정렬이 없다. 받은 키 {sorted(alignments)}"
            OUT_DIR.mkdir(parents=True, exist_ok=True)
            (OUT_DIR / f"openfold3_smoke_{a.label or 'msa-nokey'}.json").write_text(
                json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"  받은 키: {sorted(alignments)}")
            return 1
        align = alignments[pick]["a3m"]["alignment"]
        doc["msa"]["database_used"] = pick
        doc["msa"]["alignment_lines"] = align.count("\n") + 1
        print(f"  {pick} 정렬 {doc['msa']['alignment_lines']}줄")
        msa_data = {"uniref30": {"a3m": {"alignment": align, "format": "a3m"}}}

    if not msa_data:
        # OpenFold3 rejects a protein molecule with no alignment, returning 422. Its error says:
        # "If the user intent is to
        # provide 0 hits for this protein sequence, then an MSA consisting of only the query
        # sequence should be provided". So build a one-entry a3m holding the query itself.
        msa_data = {"uniref30": {"a3m": {"alignment": f">query\n{seq}\n", "format": "a3m"}}}
        doc["msa"] = {"note": "히트 없이 질의 서열만 담은 a3m. 스킬 문서 기준으로 약한 근거다"}

    mol: dict = {"type": "protein", "sequence": seq, "diffusion_samples": 1, "msa": msa_data}
    payload = {"inputs": [{"input_id": f"{a.pdb_id}_{a.chain}", "output_format": "pdb",
                           "molecules": [mol]}]}

    print("2단계 OpenFold3 ...", flush=True)
    status, body, secs = post(OF3_URL, payload, key, a.timeout)
    doc["openfold3"] = {"status": status, "seconds": round(secs, 1)}
    print(f"  HTTP {status}, {secs:.1f}초")

    if status == 200 and isinstance(body, dict):
        out0 = body["outputs"][0]
        samples = []
        for i, s in enumerate(out0.get("structures_with_scores", []), 1):
            samples.append({k: s.get(k) for k in
                            ("confidence_score", "complex_plddt_score", "ptm_score",
                             "iptm_score", "pde_score", "format")})
            struct = s.get("structure", "")
            if struct:
                p = OUT_DIR / f"openfold3_{a.pdb_id}_{a.chain}_{i}.{s.get('format','pdb')}"
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(struct, encoding="utf-8")
                samples[-1]["file"] = p.name
                samples[-1]["atom_lines"] = sum(1 for l in struct.splitlines()
                                                if l.startswith(("ATOM", "HETATM")))
        doc["openfold3"]["samples"] = samples
        for s in samples:
            print("  " + ", ".join(f"{k} {v}" for k, v in s.items() if v is not None))
    else:
        doc["openfold3"]["error"] = body if isinstance(body, str) else str(body)[:400]
        print(f"  본문: {str(body)[:300]}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tag = a.label or ("no-msa" if a.no_msa else "with-msa")
    out = OUT_DIR / f"openfold3_smoke_{tag}.json"
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n{rel(out)}")
    return 0 if status == 200 else 1


if __name__ == "__main__":
    raise SystemExit(main())
