# 구조 예측을 붙일 때의 규칙 2종과 그 측정

2026-09-27 측정. 구조 예측 스킬(`bionemo-openfold2-nim` 계열)을 파이프라인에 붙이는 안이
나왔으므로, 그때 필요한 과잉해석 규칙을 먼저 적고 **정말 필요한지 재 보았다.**

## 무엇을 넣었나

`src/harness/tools/overclaim_rules.py` 의 `STRUCTURE_RULES` 2종이다. 기존 `RULES` 15종에
넣지 않고 분리했다. 적발률 16/16 과 13/16 을 15종 프롬프트로 쟀으므로, 분모를 늘리면 이미
적은 수치의 근거가 흔들린다. 기본 프롬프트가 한 글자도 바뀌지 않은 것을 해시로 확인했다.

| 규칙 | 반려 대상 |
|---|---|
| 예측 구조를 실험 근거로 쓰지 않기 | 예측 모델이 낸 구조를 실험으로 결정된 구조와 같은 근거로 취급하거나, 예측 구조에 도킹한 결과를 실험으로 확인된 결합이라 말하는 것 |
| 구조 예측 신뢰도 지표 해석 제한 | pLDDT, pTM, ipTM, pDE 를 결합 세기나 친화도로 옮기거나, 실험 구조와의 일치도로 말하거나, 다른 단백질이나 다른 MSA 조건의 값을 견주어 순위를 매기는 것 |

출처는 NVIDIA 가 스킬 문서에 직접 적어 둔 사용 범위다. `openfold2-nim/references/science.md`
의 "Not For" 목록에 "Direct ligand docking or affinity ranking" 과 "Claiming experimental
validation from toy sequences or shallow alignments" 가 있고, 같은 문서가 단일 서열 A3M 은
"weak evidence for a production-quality fold" 라고 적었다. 파이프라인 스킬 문서는
"A larger, higher-quality MSA typically yields higher pLDDT and lower pDE" 라고 적어 pLDDT 가
입력 조건에 좌우된다는 것을 밝혔다.

## 규칙이 보태는 것이 있나 (실측)

케이스 5건을 만들어 같은 모델에 두 프롬프트로 물었다. 케이스는
`eval/cases_structure.jsonl` 에 있고 반려 정답 4건과 정상 1건이다.

| 프롬프트 | 적발률 | 거짓 양성 | 지연 중앙값 |
|---|---|---|---|
| 기존 규칙 15종 | 4/4 | 0/1 | 1,878ms |
| 15종 + 구조 예측 규칙 2종 | 4/4 | 0/1 | 938ms |

**새 규칙이 추가로 잡아낸 것은 없다.** 기존 15종만으로도 네 건을 모두 반려했다. 친화도 환산
금지와 RMSD 기준 명시가 도킹 점수에 한정해 적히지 않아 pLDDT 에도 그대로 걸렸기 때문으로
보인다. 규칙 이름을 그대로 말한 정상 케이스를 반려하지도 않았다.

지연이 절반으로 줄었으나 **호출이 다섯 건뿐이라 이것으로 빠르다고 말하지 않는다.**

## 그래서 어떻게 하나

- **규칙은 넣어 둔다.** 심사나 검토에서 "예측 구조의 한계는 어디에 적혀 있나"를 물으면 도구
  제작자의 문서를 그대로 가리킬 수 있다. 우리가 정한 주의사항이 아니라는 점이 중요하다
- **적발률을 17종으로 다시 세지 않는다.** 발표하는 수치는 15종 기준 16/16 그대로다
- **구조 예측을 실제로 붙이면** `RULES` 로 옮기고 케이스도 `eval/cases.jsonl` 에 합쳐 다시 잰다
- 이 측정은 9/27 회의 뒤 나온 "구조 예측을 넣으면 규칙을 늘려야 한다"는 판단을 **한 번 반증한
  결과다.** 늘려야 하는 것은 규칙 수가 아니라 규칙이 가리키는 출처였다

## 재현

```bash
set -a; source .env; set +a
.venv/bin/python scripts/bench_critic_judge.py --cases eval/cases_structure.jsonl \
  --prompt rules            --label structure-rules --sleep 1
.venv/bin/python scripts/bench_critic_judge.py --cases eval/cases_structure.jsonl \
  --prompt rules+structure  --label structure-rules-structure --sleep 1
```

결과 파일은 `eval/results/bench_structure-rules.json` 과
`eval/results/bench_structure-rules-structure.json` 이다.
