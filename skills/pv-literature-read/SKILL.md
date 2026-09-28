---
name: pv-literature-read
description: Use when a drug-reaction pair's PubMed hits must be read rather than only counted. Jev (System-1) grades each abstract for relevance, study design, dechallenge, rechallenge and the authors' causal conclusion, and the counts feed the evidence grade and the memo's evidence catalog.
license: Apache-2.0
metadata:
  author: korea-agentic-hackathon-2026 (FlyGate)
  layer: memory (mushroom body) -> deliberate input
  model: typesafe jev-latest
  tags: [pharmacovigilance, literature, pubmed, jev, evidence-grade]
---

# PV Literature Read

Turns `pubmed:<pmid>` from a list of IDs into read evidence. One Jev call per abstract, five typed questions, no prose.

## Contract
- Input: drug and reaction (MedDRA PT), or the default pairs in `scripts/evidence_grade.py`.
- PMIDs: `harness.tools.pharmasignal_pubmed.search_pubmed(drug, reaction, retmax=20)` (top 20 by relevance; total count kept).
- Abstracts: E-utilities efetch XML, cached one file per PMID. Cache directory is `LITERATURE_PUBMED_CACHE_DIR`, default `/data/hps/assoc/private/rsc/user/ybae/tmp/metric-validation/pubmed_cache/` (set it to your own scratch path on another machine; never `/tmp`). The script throttles itself to 3 requests per second, shared with the search tool, so run one instance at a time.
- Jev questions, one call per abstract (`scripts/literature_read.py::QUESTIONS`):
  - `relevant` (noul): does the abstract address this drug and this reaction? Below 0.5 the other answers are stored in the row but excluded from every aggregate (`by_design`, `dechallenge_n`, `rechallenge_n`, `conclusion_hist`).
  - `design` (choice): case_report, case_series, observational, rct, meta_analysis_or_review, mechanistic_or_preclinical, other.
  - `dechallenge`, `rechallenge` (noul).
  - `causal_conclusion` (score, 5 levels): not_stated, speculative, possible, probable, established.
- Output: `eval/results/literature_read_<YYYY-MM-DD>.json` (run date) with per pair `total_count`, `fetched_n`, `read_n`, `relevant_n`, `by_design`, `dechallenge_n`, `rechallenge_n`, `conclusion_hist`, and per abstract rows (pmid, title, answers, confidences, request_id, model, seconds). A failed call stores `None`, never 0.

## Run
```bash
export TYPESAFE_API_KEY=...   # TypeSafe issues a key only after credits are bought (minimum USD 5)
.venv/bin/python scripts/literature_read.py                       # default three pairs
.venv/bin/python scripts/literature_read.py --pair CLOZAPINE NEUTROPENIA
.venv/bin/python scripts/literature_read.py --pair WARFARIN "INTRACRANIAL HAEMORRHAGE"   # quote multi-word PTs; input is upper-cased
.venv/bin/python scripts/literature_read.py --dry-run             # fetch and cache abstracts only, no Jev
```
Tests: `.venv/bin/python -m pytest -q tests/test_literature_read.py` (no network).

## Where the result goes
- `scripts/evidence_grade.py` reads the newest `literature_read_*.json`; when the pair is present its reasons line becomes "문헌 92편, 판독 20편: 증례보고 12, 관찰연구 5". The grade letter does not change.
- Evidence catalog: keep one `pubmed:<pmid>` ID per paper and put the read answers in that entry's `what` text, so tier-2 numeric checks see the confidences.

## Limits
- Abstract only. Full text is not fetched.
- Read results are unvalidated until a pharmacist has checked a sample (planned: 20 abstracts).
- A design count is a description of the literature, not a causality verdict for any case (rule R5 applies).
- Jev probabilities are population-calibrated, not certainty for one paper (rule R10).
- `jev-latest` is not pinned. The model version Jev actually answered with is recorded per row in `model`; quote that, not the alias.
