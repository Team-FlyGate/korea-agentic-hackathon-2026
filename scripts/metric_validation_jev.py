#!/usr/bin/env python3
"""파일럿 참조 세트의 쌍마다 Jev 에 novel 질문을 던져, 그 확률을 지표와 같은 ROC 로 잰다.

왜 있는가
---------
`scripts/metric_validation.py` 는 PRR, ROR, 카이제곱, IC 가 라벨 기재 쌍을 얼마나 골라내는지 쟀다.
같은 참조 쌍에 Jev 의 noul 확률을 받으면 모델이 그 지표보다 나은지 같은 그림 위에서 비교할 수 있다.
설계는 `.scratch/metric-validation/issues/06-jev-arm.md` 다. 이번 제출에서는 키가 없어 돌리지 않았고,
`TYPESAFE_API_KEY` 를 가진 팀원이 이 스크립트로 돌린다.

무엇을 하고 무엇을 하지 않는가
-----------------------------
- 쌍 하나에 Jev 한 호출, 질문 하나다. 질문은 `scripts/bench_triage_scale.py` 의
  `JEV_QUESTION_SETS["novel"]` 이고, state 는 같은 파일의 `jev_state` 가 만든다(약 이름, 반응 이름,
  보고 수 a, 2x2 네 칸, PRR, ROR, Yates 카이제곱). 두 파일은 경로로 불러와 그대로 쓴다.
- 팔은 둘이다. `novel` 은 이름을 보인다. `blind` 는 이름을 DRUG_A, REACTION_1 로 가려 숫자만 남긴다.
  `jev_state` 의 `labeled` 인자(`--with-label`)는 쓰지 않는다. 라벨 기재 여부가 바로 맞힐 대상이다.
- novel 질문의 참(true)은 "라벨에 없는 새 신호일 수 있다" 다. 양성 쌍이 라벨 기재 쌍이므로 모델이
  질문대로 답하면 양성 쌍의 확률이 낮아야 한다. 그래서 AUC 는 확률을 그대로 쓴 값과 1 - 확률을 쓴
  값(`auc_inverted`)을 둘 다 적는다. 그림은 확률 그대로의 곡선을 그린다.
- AUC, 문턱 스윕, ROC 점, 귀무 뒤섞기는 `scripts/metric_validation.py` 의 함수를 불러 쓴다. 귀무
  양성 마스크는 참조 세트 전체에서 metric_validation 과 같은 시드로 만들고, Jev 확률이 있는 행으로
  좁힌다. 같은 행에서 잰 지표 일곱 개의 AUC 도 함께 적어, `--limit` 표본에서도 비교가 되게 한다.
- 실패한 호출은 확률을 None 으로 남기고 0 으로 채우지 않는다. 평가는 확률이 있는 행만 쓰고 빠진
  수를 적는다.
- `--limit N` 은 참조 세트에서 시드(참조 세트의 seed)로 N 쌍을 무작위로 뽑는다. 앞에서부터 자르면
  한 약만 남기 때문이다.
- 실제 호출 전에 쌍 수, 예상 입력 토큰, 예상 비용을 찍고 `--yes` 가 없으면 멈춘다. 토큰 수는
  요청 본문(state 와 질문)을 JSON 으로 적은 글자 수를 4 로 나눈 어림이며 토크나이저로 센 값이 아니다.
  잔액은 조회하지 않는다. `jev_client` 에 잔액을 묻는 호출이 없다.
- 결과 파일 이름의 날짜는 `date.today()` 다. 팀은 `TZ=Asia/Seoul` 로 돌려 한국 날짜를 쓴다.

사용법:
  # 네트워크 없이 비용만 본다
  TZ=Asia/Seoul .venv/bin/python scripts/metric_validation_jev.py --arm novel --limit 200 --dry-run

  # 키를 넣고 200쌍 시험 실행
  TZ=Asia/Seoul .venv/bin/python scripts/metric_validation_jev.py --arm novel --limit 200 \\
      --jev-cache eval/results/metric_validation_jev_cache.json --yes
"""
from __future__ import annotations

import argparse
import gzip
import importlib.util
import json
import os
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from harness.tools import jev_client as jev  # noqa: E402

OUT_DIR = ROOT / "eval" / "results"
DEFAULT_REFSET = ROOT / "eval" / "refsets" / "pilot_sider_2026-09-28.json.gz"
DEFAULT_PAIRS = Path("/data/hps/assoc/private/rsc/user/ybae/tmp/metric-validation/"
                    "warehouse_pairs_2026q2.tsv.gz")
QUESTION = "novel"
CHARS_PER_TOKEN = 4
CACHE_FLUSH_EVERY = 200
PAIR_COLS = ["drug", "pt", "a", "b", "c", "d", "prr", "ror", "chi2_yates"]


def _load_script(name: str):
    """scripts/ 의 파일을 경로로 불러온다. 패키지가 아니어서 import 문으로는 닿지 않는다.

    `scripts/rule_split_scan.py` 가 bench_triage_scale 을 부르는 방식과 같다. 로직을 옮겨 적지 않고
    원본을 그대로 써야 벤치마크와 이 스크립트가 서로 다른 state 를 보내는 일이 생기지 않는다.
    """
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


bench = _load_script("bench_triage_scale")
mv = _load_script("metric_validation")


def _num(x: Any) -> int | float | None:
    """numpy 수를 JSON 에 쓸 수 있는 파이썬 수로 바꾼다. NaN 과 무한대는 None 이다."""
    if x is None:
        return None
    x = float(x)
    if not np.isfinite(x):
        return None
    return int(x) if x.is_integer() else x


def read_refset(path: Path) -> dict:
    """참조 세트를 읽는다. `.json` 과 `.json.gz` 를 모두 받는다."""
    raw = path.read_bytes()
    return json.loads(gzip.decompress(raw) if path.suffix == ".gz" else raw)


def load_pairs(refset: dict, pairs_tsv: Path) -> tuple[pd.DataFrame, int]:
    """참조 행에 2x2 와 지표를 붙인다. 웨어하우스 행이 없는 참조 행은 빼고 그 수를 돌려준다.

    지표 일곱 개는 같은 행 비교에 쓰고, 2x2 네 칸과 PRR, ROR, 카이제곱은 Jev state 에 들어간다.
    """
    rows = pd.DataFrame(refset["rows"])
    cols = list(dict.fromkeys(PAIR_COLS + mv.METRICS + list(mv.FIXED_RULES)))
    pairs = pd.read_csv(pairs_tsv, sep="\t", usecols=cols, dtype={"drug": str, "pt": str},
                        keep_default_na=False, na_values=[""])
    frame = rows.merge(pairs, on=["drug", "pt"], how="left", indicator=True)
    unmatched = int((frame["_merge"] != "both").sum())
    frame = frame[frame["_merge"] == "both"].drop(columns="_merge").reset_index(drop=True)
    for r in mv.FIXED_RULES:
        frame[r] = frame[r].astype(str).str.lower().isin(["true", "1"])
    frame["label"] = frame["class"] == "positive"
    return frame, unmatched


def pair_state(row: dict, *, blind: bool) -> dict[str, Any]:
    """쌍 하나의 Jev state. bench_triage_scale.jev_state 에 웨어하우스 값을 같은 모양으로 넘긴다.

    보고 수(`reports_with_this_reaction`)는 2x2 의 a 다. 벤치마크의 openFDA 건수와 같은 자리다.
    """
    event = {"reaction": row["pt"], "faers_count": _num(row["a"])}
    faers = {"counts": {k: _num(row[k]) for k in ("a", "b", "c", "d")},
             "prr": _num(row["prr"]), "ror": _num(row["ror"]),
             "chi2_yates": _num(row["chi2_yates"])}
    return bench.jev_state(row["drug"], event, faers, blind=blind)


def estimate_tokens(states: list[dict], questions: dict) -> int:
    """요청 본문 글자 수를 4 로 나눈 어림 토큰 수. 토크나이저로 센 값이 아니다."""
    chars = sum(len(json.dumps({"state": s, "questions": questions}, ensure_ascii=False))
                for s in states)
    return chars // CHARS_PER_TOKEN


def cost_lines(n_pairs: int, tokens: int, price_in: float) -> list[str]:
    usd = tokens / 1e6 * price_in
    per = tokens / n_pairs if n_pairs else 0.0
    return [f"쌍 수: {n_pairs:,}",
            f"예상 입력 토큰: {tokens:,} (쌍당 {per:.1f}, 요청 JSON 글자 수 / {CHARS_PER_TOKEN} 의 어림)",
            f"예상 비용: USD {usd:.4f} (입력 USD {price_in}/M 토큰, 출력 무료)"]


def load_cache(path: Path | None) -> dict[str, dict]:
    if path is None or not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_cache(path: Path, cache: dict[str, dict]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def cache_key(arm: str, model: str, drug: str, pt: str) -> str:
    return f"{arm}|{model}|{drug}|{pt}"


def ask_pairs(frame: pd.DataFrame, *, arm: str, model: str, dry_run: bool,
              cache: dict[str, dict], cache_path: Path | None = None,
              call: Callable[..., dict] = jev.systemone, timeout: float = 60.0,
              sleep: float = 0.0) -> list[dict]:
    """쌍마다 Jev 를 부르거나 캐시에서 꺼내 확률 행을 만든다.

    dry_run 이면 캐시에 있는 응답만 쓰고 네트워크로 나가지 않는다. 성공(HTTP 200) 응답만 캐시에 넣고,
    `cache_path` 가 있으면 CACHE_FLUSH_EVERY 호출마다 파일로 내려 긴 실행이 중간에 끊겨도 받은 만큼은
    남긴다. 확률을 못 읽은 행은 None 으로 둔다.
    """
    questions = bench.jev_questions(QUESTION)
    blind = arm == "blind"
    out: list[dict] = []
    fresh = 0
    for row in frame.to_dict("records"):
        key = cache_key(arm, model, row["drug"], row["pt"])
        result = cache.get(key)
        source = "cache" if result is not None else None
        if result is None and not dry_run:
            if fresh and sleep:
                time.sleep(sleep)
            result = call(pair_state(row, blind=blind), questions, model=model, timeout=timeout)
            fresh += 1
            source = "live"
            if result.get("status") == 200:
                cache[key] = result
                if cache_path is not None and fresh % CACHE_FLUSH_EVERY == 0:
                    save_cache(cache_path, cache)
        result = result or {}
        body = result.get("body") or {}
        out.append({
            "drug": row["drug"], "pt": row["pt"], "class": row["class"], "a": _num(row["a"]),
            "probability": jev.noul_probability(result, bench.JEV_QUESTION_KEY),
            "request_id": result.get("request_id"),
            "model": (body.get("model") if isinstance(body, dict) else None)
            or (model if result else None),
            "seconds": result.get("seconds"),
            "http_status": result.get("status"),
            "input_tokens": jev.usage_tokens(result)["prompt_tokens"],
            "error": result.get("error"),
            "source": source,
        })
    return out


def score_block(s: np.ndarray, y: np.ndarray) -> dict:
    """확률 하나의 AUC, 문턱 스윕, ROC 점. metric_validation.evaluate 의 지표 항목과 같은 모양이다."""
    q = np.unique(np.quantile(s, np.linspace(0, 1, mv.N_SWEEP)))
    return {"n_used": int(len(s)), "n_positive": int(y.sum()), "n_negative": int((~y).sum()),
            "auc": mv.auc(s, y), "auc_inverted": mv.auc(-s, y),
            "conventional": [mv.at_threshold(s, y, 0.5)],
            "sweep": [mv.at_threshold(s, y, t) for t in q],
            "roc": mv.thin(mv.roc_points(s, y), mv.N_ROC_POINTS)}


def evaluate(full: pd.DataFrame, rows: list[dict], *, seed: int, repeats: int) -> dict | None:
    """확률이 있는 행으로 Jev 의 AUC 와 귀무 요약과 같은 행의 지표 AUC 를 낸다.

    귀무 양성 마스크는 참조 세트 전체(`full`)에서 metric_validation 과 같은 시드로 만든 뒤 Jev 확률이
    있는 행으로 좁힌다. 확률이 있는 행에 양성과 음성이 모두 있어야 계산한다.
    """
    prob = {(r["drug"], r["pt"]): r["probability"] for r in rows if r["probability"] is not None}
    keys = list(zip(full["drug"], full["pt"]))
    have = np.array([k in prob for k in keys], dtype=bool)
    labels = full["label"].to_numpy()
    y = labels[have]
    if not have.any() or y.all() or not y.any():
        return None
    s = np.array([prob[k] for k, h in zip(keys, have) if h], dtype=float)
    block = score_block(s, y)
    null_sets = mv.null_positive_masks(full, seed, repeats)
    block["null"] = {
        "repeats": repeats, "seed_first": seed, "seed_last": seed + repeats - 1,
        "note": "귀무 양성은 참조 세트 전체에서 metric_validation 과 같은 시드로 만들고 확률이 있는 행으로 좁혔다.",
        "n_null_positive_in_rows": mv.summarise([int(n["mask"][have].sum()) for n in null_sets]),
        "auc": mv.summarise([mv.auc(s, n["mask"][have]) for n in null_sets]),
    }
    same = {}
    for m in mv.METRICS:
        v = full[m].to_numpy(dtype=float)[have]
        ok = ~np.isnan(v)
        same[m] = mv.auc(v[ok], y[ok])
    block["metrics_auc_same_rows"] = same
    return block


def sample_rows(frame: pd.DataFrame, limit: int | None, seed: int) -> pd.DataFrame:
    """limit 이 있으면 시드로 limit 쌍을 무작위로 뽑는다. 순서는 참조 세트 순서로 되돌린다."""
    if limit is None or limit >= len(frame):
        return frame
    rng = np.random.default_rng(seed)
    idx = np.sort(rng.choice(len(frame), size=limit, replace=False))
    return frame.iloc[idx].reset_index(drop=True)


def main(argv: list[str] | None = None, *, call: Callable[..., dict] = jev.systemone) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--refset", type=Path, default=DEFAULT_REFSET)
    ap.add_argument("--pairs-tsv", type=Path, default=DEFAULT_PAIRS)
    ap.add_argument("--arm", choices=["novel", "blind"], default="novel",
                    help="novel 은 이름을 보이고 blind 는 DRUG_A, REACTION_1 로 가린다")
    ap.add_argument("--limit", type=int, default=None, help="시드로 뽑은 N 쌍만 부른다")
    ap.add_argument("--sleep", type=float, default=0.0, help="호출 사이 쉬는 초")
    ap.add_argument("--timeout", type=float, default=60.0)
    ap.add_argument("--model", default=jev.DEFAULT_MODEL)
    ap.add_argument("--price-in", type=float, default=0.042, help="입력 USD / 백만 토큰")
    ap.add_argument("--dry-run", action="store_true",
                    help="네트워크 없이 비용만 찍고, 캐시에 없는 쌍은 확률 null 로 쓴다")
    ap.add_argument("--jev-cache", type=Path,
                    help="Jev 원응답 JSON. 있으면 재생하고, 새로 받은 성공 응답을 덧붙인다")
    ap.add_argument("--yes", action="store_true", help="비용을 확인했고 실제로 부른다")
    ap.add_argument("--repeats", type=int, default=None, help="귀무 반복 수. 기본은 참조 세트 값")
    ap.add_argument("--label", default=None, help="파일 이름 꼬리. 기본은 팔 이름(dry-run 은 _dryrun)")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)

    refset = read_refset(args.refset)
    meta = refset["meta"]
    full, unmatched = load_pairs(refset, args.pairs_tsv)
    chosen = sample_rows(full, args.limit, meta["seed"])
    blind = args.arm == "blind"
    questions = bench.jev_questions(QUESTION)
    states = [pair_state(r, blind=blind) for r in chosen.to_dict("records")]
    tokens = estimate_tokens(states, questions)
    cache = load_cache(args.jev_cache)
    cached = sum(cache_key(args.arm, args.model, d, p) in cache
                 for d, p in zip(chosen["drug"], chosen["pt"]))
    new_tokens = tokens * (len(chosen) - cached) // max(len(chosen), 1)

    print(f"팔: {args.arm}, 질문: {QUESTION}, 모델: {args.model}")
    for line in cost_lines(len(chosen), tokens, args.price_in):
        print(line)
    if cached:
        print(f"캐시에 있는 쌍: {cached:,}, 새로 부를 쌍의 예상 비용 USD "
              f"{new_tokens / 1e6 * args.price_in:.4f}")

    if not args.dry_run:
        if not args.yes:
            print("실제 호출은 --yes 를 붙여야 한다. 멈춘다.")
            return 2
        if not os.environ.get(jev.API_KEY_ENV, "").strip() and cached < len(chosen):
            print(f"{jev.API_KEY_ENV} 가 비어 있다. 멈춘다.")
            return 2

    rows = ask_pairs(chosen, arm=args.arm, model=args.model, dry_run=args.dry_run, cache=cache,
                     cache_path=args.jev_cache, call=call, timeout=args.timeout, sleep=args.sleep)
    if args.jev_cache is not None and len(cache) > 0:
        save_cache(args.jev_cache, cache)

    repeats = args.repeats if args.repeats is not None else meta["null_repeats"]
    result = evaluate(full, rows, seed=meta["seed"], repeats=repeats)
    n_prob = sum(r["probability"] is not None for r in rows)
    secs = [r["seconds"] for r in rows if r["source"] == "live" and r["seconds"] is not None]
    label = args.label or (f"{args.arm}_dryrun" if args.dry_run else args.arm)
    doc = {
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "script": "scripts/metric_validation_jev.py",
        "caveat": "양성은 SIDER 라벨 기재, 음성은 라벨 부재다. novel 질문의 참은 '라벨에 없는 새 신호'라 "
                  "질문대로 답하면 양성의 확률이 낮다. auc 와 auc_inverted 를 함께 읽는다.",
        "arm": args.arm, "blind": blind, "question": QUESTION,
        "question_spec": questions, "model_requested": args.model, "dry_run": args.dry_run,
        "refset_path": str(args.refset), "pairs_tsv": str(args.pairs_tsv),
        "warehouse_asof": meta["warehouse"]["asof"], "seed": meta["seed"],
        "limit": args.limit, "n_refset_rows": int(len(full)),
        "refset_rows_without_warehouse_row": unmatched,
        "n_pairs": len(rows), "n_with_probability": n_prob, "n_failed_or_missing": len(rows) - n_prob,
        "estimate": {"input_tokens": tokens, "method": f"요청 JSON 글자 수 / {CHARS_PER_TOKEN}",
                     "price_in_usd_per_m": args.price_in,
                     "usd": tokens / 1e6 * args.price_in},
        "seconds": mv.summarise(secs),
        "jev": result,
        "pairs": rows,
    }
    out = args.out or OUT_DIR / f"metric_validation_jev_{label}_{date.today().isoformat()}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(json.dumps(mv.finite(doc), ensure_ascii=False, allow_nan=False) + "\n",
                   encoding="utf-8")
    os.replace(tmp, out)
    print(f"{out}: 쌍 {len(rows):,}, 확률 있음 {n_prob:,}")
    if result:
        print(f"  Jev {args.arm} AUC {result['auc']:.3f} (뒤집으면 {result['auc_inverted']:.3f}), "
              f"귀무 평균 {result['null']['auc'].get('mean', float('nan')):.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
