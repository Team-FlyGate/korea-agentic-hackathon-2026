#!/usr/bin/env python3
"""SIDER 4.1 라벨과 FAERS 웨어하우스로 파일럿 참조 세트를 만든다.

왜 있는가
---------
불균형 지표(PRR, ROR, 카이제곱, IC)의 민감도와 특이도를 재려면 양성 쌍과 음성 쌍의 목록이
먼저 있어야 한다. 규격은 `.scratch/metric-validation/issues/03-refset-spec.md` 의 "## Answer" 이고,
이 스크립트가 그 구현이다. 양성 쌍은 "라벨 기재" 이며 인과성이 확정된 쌍이 아니다. 음성 쌍은
"라벨 부재" 이며 "일어나지 않음" 이 아니다.

무엇을 하고 무엇을 하지 않는가
-----------------------------
- 약 매칭. `drug_names.tsv` 이름 가운데 여러 STITCH flat ID 가 나눠 쓰는 이름을 빼고, 남은 이름을
  대문자로 바꿔 웨어하우스 성분명과 정확히 맞춘다. 뺀 수와 맞지 않은 수를 남긴다. 첫 글자 대문자
  이름(상품명, 약어로 짐작한 것)을 빼던 규칙은 2026-09-28 에 없앴다. 그 규칙이 `Abarelix`,
  `Carfilzomib`, `Goserelin` 같은 일반명까지 뺐고, 상품명은 웨어하우스 성분명과 맞지 않아 어차피
  떨어지기 때문이다.
- 약 선정. 맞은 약 가운데 FAERS 보고 수(`sig_drug_n.n_drug`, PS/SS 의심약 사례 수) 상위 N 종과
  손 선정 약의 합집합이다. 손 선정 약이 매칭에서 빠졌으면 빠졌다고 적는다.
- 양성 쌍. 그 약의 SIDER PT 쌍(`meddra_all_se` 4열 `PT`) 가운데 웨어하우스에 행이 있는 것이다.
  웨어하우스 `sig_signal` 은 a≥3 인 쌍만 담으므로 a≥3 은 여기서 따로 걸지 않아도 성립한다.
  웨어하우스 행이 없어 빠진 양성 수를 남긴다(a<3 과 이름 불일치를 이 파일로는 가르지 못한다).
- 음성 쌍. 같은 약의 웨어하우스 PT 가운데 그 약의 SIDER 목록에 없고 SIDER 전체 PT 우주에는 있는
  것 전부다. 비교는 소문자로 한다. 우주 밖이라 걸러진 PT(행정·결과 용어 등)의 수와 예시를 남긴다.
- 귀무 쌍은 저장하지 않는다. 시드와 반복 수만 적고 `scripts/metric_validation.py` 가 재생성한다.
- `--out` 이 `.gz` 로 끝나면 gzip 으로 쓴다(mtime 0, 같은 입력이면 같은 바이트).
- DuckDB 를 직접 열지 않는다. 웨어하우스에서 뺀 TSV(`sig_signal` 일부와 `sig_drug_n` 전체)와 그
  메타 JSON 을 읽는다. 저장소에 duckdb 의존성을 들이지 않으려는 선택이다.

사용법:
  .venv/bin/python scripts/build_refset.py \\
      --sider-dir $METRIC_VALIDATION_DIR/sider \\
      --pairs-tsv $METRIC_VALIDATION_DIR/warehouse_pairs_2026q2.tsv.gz \\
      --drug-n-tsv $METRIC_VALIDATION_DIR/warehouse_drug_n_2026q2.tsv.gz \\
      --export-meta $METRIC_VALIDATION_DIR/warehouse_export_2026q2.meta.json
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "eval" / "refsets"

SIDER_RELEASE = "SIDER 4.1 (2015-10-21)"
MEDDRA_VERSION = "16.1"
A_THRESHOLD = 3
DEFAULT_TOP_N = 30
DEFAULT_EXTRA = ["CLOZAPINE", "WARFARIN", "LENALIDOMIDE", "ATORVASTATIN"]
DEFAULT_SEED = 20260928
NULL_REPEATS = 100

SOURCE_POSITIVE = "sider4.1:label_pt"
SOURCE_NEGATIVE = "sider4.1:label_absent+faers:a>=3"
# 행의 source 는 짧은 꼬리표로 쓰고 풀이는 메타에 한 번 둔다. 행이 수만 개라 파일 크기를 줄인다.
SOURCES = {
    SOURCE_POSITIVE: "SIDER 4.1 meddra_all_se 4열 PT 행에 있는 쌍(라벨 기재)",
    SOURCE_NEGATIVE: "그 약의 SIDER PT 목록에 없고 SIDER PT 우주에 있으며 웨어하우스 a>=3 인 쌍(라벨 부재)",
}

DRUG_RULE = (
    "drug_names.tsv 이름 가운데 여러 flat ID 가 나눠 쓰는 이름을 빼고, "
    "대문자로 바꿔 웨어하우스 sig_drug_n.drug 와 정확히 일치하는 약. 그 가운데 n_drug 상위 "
    "{top_n}종(동률은 성분명 순)과 손 선정 약 {extra} 의 합집합.")


def md5(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_sider(sider_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(flat_id, name) 표와 (flat_id, pt) PT 쌍 표를 돌려준다. pt 는 소문자다."""
    names = pd.read_csv(sider_dir / "drug_names.tsv", sep="\t", header=None,
                        names=["flat_id", "name"], dtype=str, keep_default_na=False)
    se = pd.read_csv(sider_dir / "meddra_all_se.tsv.gz", sep="\t", header=None,
                     names=["flat_id", "stereo_id", "label_umls", "kind", "pt_umls", "term"],
                     dtype=str, keep_default_na=False)
    pt = se.loc[se["kind"] == "PT", ["flat_id", "term"]].copy()
    pt["pt"] = pt["term"].str.lower()
    pt = pt[["flat_id", "pt"]].drop_duplicates().reset_index(drop=True)
    return names, pt


def match_drugs(names: pd.DataFrame, warehouse_drugs: set[str]) -> tuple[dict[str, str], dict]:
    """이름 규칙을 적용해 {웨어하우스 성분명: flat_id} 와 제외 수 일람을 돌려준다.

    공유 여부는 대문자로 바꾼 이름으로 가린다. 웨어하우스와 대문자로 맞추므로, 대소문자만 다른 두
    이름(예 `Fe` 와 `fe`)도 같은 성분명에 떨어져 어느 flat ID 인지 정할 수 없기 때문이다.
    """
    names = names.assign(key=names["name"].str.upper())
    ids_per_key = names.groupby("key")["flat_id"].nunique()
    shared = set(ids_per_key[ids_per_key > 1].index)
    eligible = sorted(set(ids_per_key.index) - shared)
    first_id = names.drop_duplicates("key").set_index("key")["flat_id"]
    matched = {k: first_id[k] for k in eligible if k in warehouse_drugs}
    unmatched = [k for k in eligible if k not in warehouse_drugs]
    exclusions = {
        "sider_flat_ids": int(names["flat_id"].nunique()),
        "sider_names_case_insensitive": int(len(ids_per_key)),
        "excluded_shared_names": len(shared),
        "excluded_shared_flat_ids": int(names["key"].isin(shared).sum()),
        # 첫 글자 대문자 이름 제외 규칙은 2026-09-28 에 없앴다. Abarelix, Carfilzomib, Goserelin 같은
        # 일반명까지 뺐기 때문이다. 이전 결과와 맞대 볼 수 있게 0 으로 남긴다.
        "excluded_capitalized_names": 0,
        "eligible_names": len(eligible),
        "unmatched_in_warehouse": len(unmatched),
        "matched_drugs": len(matched),
        "unmatched_examples": unmatched[:15],
    }
    return matched, exclusions


def select_drugs(matched: dict[str, str], drug_n: pd.DataFrame, top_n: int,
                 extra: list[str]) -> tuple[list[dict], list[str]]:
    """보고 수 상위 top_n 과 손 선정 약을 합친다. 매칭에 없는 손 선정 약은 두 번째 값으로 돌려준다."""
    counts = dict(zip(drug_n["drug"], drug_n["n_drug"].astype(int)))
    ranked = sorted(matched, key=lambda d: (-counts[d], d))
    chosen = {d: "top_n" for d in ranked[:top_n]}
    missing = []
    for d in extra:
        d = d.upper()
        if d not in matched:
            missing.append(d)
        elif d not in chosen:
            chosen[d] = "extra"
    rank = {d: i + 1 for i, d in enumerate(ranked)}
    selected = [{"drug": d, "flat_id": matched[d], "n_drug": counts[d],
                 "rank_among_matched": rank[d], "reason": why}
                for d, why in sorted(chosen.items(), key=lambda kv: rank[kv[0]])]
    return selected, missing


def build_rows(selected: list[dict], sider_pt: pd.DataFrame,
               pairs: pd.DataFrame) -> tuple[list[dict], dict, list[dict]]:
    """양성과 음성 행, 버린 수, 약별 구성을 만든다."""
    universe = set(sider_pt["pt"])
    rows: list[dict] = []
    per_drug = []
    dropped_pos = 0
    outside = Counter()      # 우주 밖 PT 의 a 합계, 예시 고르기용
    outside_rows = 0
    for s in selected:
        label = set(sider_pt.loc[sider_pt["flat_id"] == s["flat_id"], "pt"])
        wh = pairs.loc[pairs["drug"] == s["drug"], ["pt", "a"]]
        wh_pts = dict(zip(wh["pt"].str.lower(), wh["pt"]))
        pos = sorted(p for p in label if p in wh_pts)
        neg = sorted(p for p in wh_pts if p not in label and p in universe)
        out = [p for p in wh_pts if p not in universe]
        outside_rows += len(out)
        for p, a in zip(wh["pt"].str.lower(), wh["a"]):
            if p not in universe:
                outside[p] += int(a)
        dropped_pos += len(label) - len(pos)
        rows += [{"drug": s["drug"], "pt": wh_pts[p], "class": "positive", "source": SOURCE_POSITIVE}
                 for p in pos]
        rows += [{"drug": s["drug"], "pt": wh_pts[p], "class": "negative", "source": SOURCE_NEGATIVE}
                 for p in neg]
        per_drug.append({**s, "sider_pts": len(label), "warehouse_pts": len(wh_pts),
                         "positive": len(pos), "negative": len(neg),
                         "positive_without_warehouse_row": len(label) - len(pos),
                         "warehouse_pts_outside_universe": len(out)})
    dropped = {
        "sider_positive_without_warehouse_row": dropped_pos,
        "warehouse_pairs_outside_sider_universe": outside_rows,
        "warehouse_pts_outside_sider_universe": len(outside),
        "outside_universe_examples_by_a": [p for p, _ in outside.most_common(15)],
    }
    return rows, dropped, per_drug


def dump_refset(meta: dict, rows: list[dict]) -> str:
    """메타는 들여쓰고 행은 한 줄에 하나씩 쓴다. 행이 수만 개라 diff 와 grep 이 쉬워진다."""
    body = ",\n".join("  " + json.dumps(r, ensure_ascii=False) for r in rows)
    return ('{"meta": ' + json.dumps(meta, ensure_ascii=False, indent=1)
            + ',\n "rows": [\n' + body + "\n ]}\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sider-dir", type=Path, required=True)
    ap.add_argument("--pairs-tsv", type=Path, required=True)
    ap.add_argument("--drug-n-tsv", type=Path, required=True)
    ap.add_argument("--export-meta", type=Path, default=None,
                    help="웨어하우스 추출 메타 JSON(asof, 경로, SQL). 없으면 asof 를 비워 둔다.")
    ap.add_argument("--top-n", type=int, default=DEFAULT_TOP_N)
    ap.add_argument("--extra-drugs", nargs="*", default=DEFAULT_EXTRA)
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)

    names, sider_pt = read_sider(args.sider_dir)
    pairs = pd.read_csv(args.pairs_tsv, sep="\t", usecols=["drug", "pt", "a"],
                        dtype={"drug": str, "pt": str}, keep_default_na=False)
    drug_n = pd.read_csv(args.drug_n_tsv, sep="\t", dtype={"drug": str}, keep_default_na=False)
    export = json.loads(args.export_meta.read_text()) if args.export_meta else {}

    matched, exclusions = match_drugs(names, set(drug_n["drug"]))
    selected, missing_extra = select_drugs(matched, drug_n, args.top_n, args.extra_drugs)
    rows, dropped, per_drug = build_rows(selected, sider_pt, pairs)

    n_pos = sum(r["class"] == "positive" for r in rows)
    n_neg = len(rows) - n_pos
    meta = {
        "name": f"pilot_sider_{date.today().isoformat()}",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "script": "scripts/build_refset.py",
        "spec": ".scratch/metric-validation/issues/03-refset-spec.md",
        "sider": {
            "release": SIDER_RELEASE, "meddra_version": MEDDRA_VERSION,
            "dir": str(args.sider_dir),
            "md5": {f: md5(args.sider_dir / f) for f in ("drug_names.tsv", "meddra_all_se.tsv.gz")},
            "pt_pairs": int(len(sider_pt)), "pt_universe": int(sider_pt["pt"].nunique()),
        },
        "warehouse": {
            "asof": export.get("asof"), "path": export.get("warehouse_path"),
            "first_quarter": export.get("first_quarter"), "n_quarters": export.get("n_quarters"),
            "export_sql": export.get("sql"), "pairs_tsv": str(args.pairs_tsv),
            "pairs_md5": md5(args.pairs_tsv), "drug_n_tsv": str(args.drug_n_tsv),
            "drug_n_md5": md5(args.drug_n_tsv),
        },
        "sources": SOURCES,
        "drug_rule": DRUG_RULE.format(top_n=args.top_n, extra=", ".join(args.extra_drugs)),
        "top_n": args.top_n, "extra_drugs": args.extra_drugs,
        "extra_drugs_not_matched": missing_extra,
        "n_drugs": len(selected), "a_threshold": A_THRESHOLD,
        "seed": args.seed, "null_repeats": NULL_REPEATS,
        "null_rule": "선정 약 안에서 양성 쌍의 PT 를 약 사이에 뒤섞어 양성과 같은 수의 귀무 쌍을 만든다. "
                     "시드는 seed+i(i=0..null_repeats-1). 쌍은 저장하지 않고 metric_validation.py 가 재생성한다.",
        "exclusions": exclusions, "dropped": dropped,
        "counts": {"positive": n_pos, "negative": n_neg,
                   "prevalence": round(n_pos / len(rows), 6) if rows else None,
                   "negative_per_positive": round(n_neg / n_pos, 4) if n_pos else None},
        "drugs": per_drug,
    }
    out = args.out or OUT_DIR / f"{meta['name']}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    text = dump_refset(meta, rows)
    if out.suffix == ".gz":
        tmp.write_bytes(gzip.compress(text.encode("utf-8"), mtime=0))
    else:
        tmp.write_text(text)
    os.replace(tmp, out)
    print(f"{out}: 약 {len(selected)}종, 양성 {n_pos}, 음성 {n_neg}, 유병률 {meta['counts']['prevalence']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
