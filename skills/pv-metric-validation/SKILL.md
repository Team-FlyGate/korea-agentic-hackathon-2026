---
name: pv-metric-validation
description: Use when a memo or grade cites a disproportionality threshold (Evans, ROR lower bound, IC025) and needs to say how well that threshold separates label-listed from label-absent drug-reaction pairs, or when the reference set and ROC behind those numbers must be rebuilt.
license: Apache-2.0
metadata:
  author: korea-agentic-hackathon-2026
  layer: memory (mushroom body)
  model: none (fixed computation; Jev only as an optional comparison arm)
  tags: [pharmacovigilance, signal-detection, disproportionality, reference-set, roc, sider]
---

# PV Metric Validation

Measures how well PRR, ROR, chi-square, IC and their lower bounds separate a reference set of drug-reaction pairs, and places the fixed rules (Evans, ROR signal, IC signal, the evidence grade's Evans-or-ROR) on the same ROC. No model is involved.

## What the numbers mean
- Positive pair: the reaction PT is in the drug's SIDER 4.1 label (MedDRA 16.1, 2015). Negative pair: not in that drug's label but inside SIDER's PT universe. Both need a >= 3 in the FAERS warehouse. So every figure is "predicts label listing", never "predicts causality" (rule R5).
- Pilot scale: 31 drugs (top 30 by FAERS suspect-case count plus warfarin), 6,222 positives, 58,574 negatives, prevalence 9.6%.
- Null AUC comes from shuffling positive PTs across drugs 100 times; 42% of shuffled positives still land on a label, so read AUC next to its null, not alone.

## Results to cite (2026-09-28 KST run, `eval/results/metric_validation_2026-09-28.json`)
| Metric | AUC | Null mean |
|---|---|---|
| PRR, ROR, IC (point) | 0.56 | 0.47 |
| PRR / ROR 95% lower bound | 0.62 | 0.52 |
| IC025 | 0.63 | 0.54 |
| chi-square (Yates) | 0.68 | 0.64 |

| Fixed rule | Sensitivity | Specificity | PPV |
|---|---|---|---|
| Evans (PRR>=2, chi2>=4, a>=3) | 0.30 | 0.75 | 0.12 |
| ROR signal (ROR lower bound > 1) | 0.47 | 0.68 | 0.13 |
| Grade signal (Evans or ROR) | 0.47 | 0.68 | 0.13 |

A memo line built from this: "PRR 9.85 exceeds the Evans threshold, which on the SIDER pilot set has sensitivity 0.30 and specificity 0.75 for label listing (metric:evans:sider-pilot@2026Q2)."

## Run
Inputs: the SIDER 4.1 download (static since 2015, reuse the same directory) and two gzipped TSV exports from the FlyVigilante warehouse, made with the duckdb-enabled venv:
```sql
SELECT drug, pt, a, b, c, d, expected, prr, prr_lo, prr_hi, ror, ror_lo, ror_hi, chi2_yates, ic, ic025, evans_signal, ror_signal, ic_signal FROM sig_signal WHERE drug IN (<upper-cased SIDER drug names>);
SELECT drug, n_drug FROM sig_drug_n;
```
plus a small meta JSON with `asof` and the SQL. Keep `--top-n 30`, the `--extra-drugs` list and `--seed 20260928` fixed across quarters so runs stay comparable; change them only as a deliberate new reference set, and say so in the note. `<YYYY-MM-DD>` is the run date in Korea Standard Time. Paths below are this repo's scratch defaults; set your own.
```bash
export TZ=Asia/Seoul
M=<자기 스크래치 경로>/metric-validation   # 예: 소속 스토리지 아래 tmp
export METRIC_VALIDATION_DIR=$M
.venv/bin/python scripts/build_refset.py --sider-dir $M/sider \
    --pairs-tsv $M/warehouse_pairs_2026q2.tsv.gz --drug-n-tsv $M/warehouse_drug_n_2026q2.tsv.gz \
    --export-meta $M/warehouse_export_2026q2.meta.json --top-n 30 \
    --extra-drugs CLOZAPINE WARFARIN LENALIDOMIDE ATORVASTATIN --seed 20260928 \
    --out eval/refsets/pilot_sider_<YYYY-MM-DD>.json.gz
.venv/bin/python scripts/metric_validation.py --refset eval/refsets/pilot_sider_<YYYY-MM-DD>.json.gz \
    --pairs-tsv $M/warehouse_pairs_2026q2.tsv.gz --out eval/results/metric_validation_<YYYY-MM-DD>.json
$M/venv-fv/bin/python scripts/plot_metric_roc.py --results eval/results/metric_validation_<YYYY-MM-DD>.json   # any python with matplotlib; the repo .venv has none
```
Full procedure, including the DuckDB export SQL, is in `docs/notes/metric-validation-2026-09-28.md` section 5. Run the tests before trusting a new export: `.venv/bin/python -m pytest -q tests/test_build_refset.py tests/test_metric_validation.py` (no network).

## Limits
- Labels are from 2015; reactions added since count as negatives.
- Name matching is exact uppercase string; SIDER names shared by several STITCH ids are dropped, and drugs absent from SIDER (niraparib, pembrolizumab) or merged with isomers (isotretinoin) are not in the set.
- PTs outside SIDER's PT universe (for example pyrexia, acute kidney injury) are in neither class.
- High-volume drugs only, so AUC may be optimistic for rarely reported drugs; per-drug AUC ranges 0.45 to 0.89.
- Chi-square's AUC tracks report volume (null 0.64), so its rank is not evidence that it separates labels best.
