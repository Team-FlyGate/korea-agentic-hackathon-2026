# NVIDIA 스킬 카탈로그: 무엇을 요구하는지와 무엇을 쓸지

2026-09-27 작성. 회의 뒤 팀원 A와 팀원 C가 조사한 내용을 받아, 카탈로그를 직접 조회해
확인한 것과 확인하지 못한 것을 갈라 적었다.

## 1. 공고의 "Skill API"가 무엇을 뜻하는가

진행 안내에 "build.nvidia.com을 통해 Skill API를 활용하여 데모 프로젝트를 직접 개발합니다"라고
적혀 있다. 팀원 A가 이 문장을 "스킬을 만들어 올리라는 뜻"으로 읽고 확인해 보았고, 정리된
결과는 다음과 같다.

- `build.nvidia.com/skills` 는 NVIDIA가 만든 스킬을 **내려받아 쓰는 카탈로그**이고 외부에서
  스킬을 올리는 기능이 없다
- 카탈로그의 스킬은 전부 NVIDIA 소속자가 만든 것이다
- "Skill API"라는 표현이 NVIDIA 문서에서 검색되지 않는다. NVIDIA 챗봇도 그런 이름의 API가
  없다고 답했다

따라서 **스킬을 새로 만들어 제출하는 과제가 아니라 카탈로그의 스킬을 활용하는 과제로 읽는다.**
회의 뒤 정리한 결론과 같고, 팀 전원이 같은 판단이었다. 공고 문구가 애매한 것은 주최 측 표기
문제로 보이므로 우리 쪽에서 해석을 문서에 남기고 그대로 간다.

## 2. 카탈로그 실측 (2026-09-27 조회)

| 항목 | 값 |
|---|---|
| 저장소 | `github.com/NVIDIA/skills` |
| 스킬 수 | 382개 |
| 라이선스 | Apache-2.0 (스킬 문서는 CC BY 4.0 병기) |
| 최근 갱신 | 2026-09-25 |
| 스타 | 3,455개 |
| 설치 | `npx skills add NVIDIA/skills --skill <스킬명>` |

**도킹 스킬은 카탈로그에 없다.** 구조와 분자 쪽으로 있는 것은 `bionemo-openfold2-nim`,
`bionemo-msa-structure-prediction-pipeline`, `bionemo-nvmolkit-usage` 셋이고, 의료 근거 쪽은
`medtech-model-evidence-export` 하나다. 이름에 dock이 들어간 스킬은 없다.

지금 저장소에 설치해 둔 `nvidia-diffdock-nim` 은 카탈로그가 아니라
`NVIDIA-BioNeMo/bionemo-agent-toolkit` 에서 가져온 것이다. **출처가 다르므로 문안에서 둘을
구분해 적어야 한다.** 둘 다 NVIDIA가 낸 것이지만 공고가 가리키는 카탈로그는 앞의 것이다.

## 3. 쓸 만한 후보 (전부 존재 확인)

| 스킬 | 하는 일 | 우리 쪽 접점 |
|---|---|---|
| `bionemo-openfold2-nim` | 단일 사슬 단백질 구조 예측. AlphaFold2 계열 | 타깃 단백질 구조를 우리가 만들 때. 팀원 A가 성능보다 주최 측 기술이라는 점을 들어 권함 |
| `bionemo-msa-structure-prediction-pipeline` | MSA 검색부터 구조 예측까지 이어 붙인 파이프라인 | 팀원 C가 이것으로 단백질과 니라파립 복합체를 돌려 pLDDT 95.95, pTM 0.83, ipTM 0.66, 단백질 CA RMSD 1.0 Å, 리간드 RMSD 1.16 Å를 받았다 |
| `aiq-research` | 문헌 조사 에이전트 | 인과성 평가에 필요한 문헌 근거 수집. 팀원 B가 회의에서 말한 유의성 판단 근거 찾기에 해당한다 |
| `nemo-retriever` | 검색 증강 생성의 검색 단 | 과거 유사 사례를 참고 자료로 찾아 주는 구성. PV 실무자가 원한다고 밝힌 기능과 맞는다 |
| `rag-blueprint` | 검색 증강 생성 전체 구성 | 위와 같은 목적의 더 큰 틀 |
| `medtech-model-evidence-export` | 의료 모델의 근거를 정해진 형식으로 내보내기 | 우리 주장마다 근거 ID를 붙이는 구조와 성격이 같다. 형식을 견줘 볼 값이 있다 |
| `skill-card-generator` | 스킬 카드 정리 | 우리가 만든 것을 카탈로그 형식으로 문서화할 때 |

`nvidia-skill-finder` 는 카탈로그에서 스킬을 찾아 주는 스킬이고, 팀원 C가 스킬 공개 형식의
예시로 참고했다.

## 4. 우리가 할 것

**설치해서 실제로 쓰는 것을 하나 이상 만든다.** 문서를 참조만 한 상태와 설치해 호출까지 한
상태는 심사 1번 항목에서 값이 다르다. 지금 `nvidia-diffdock-nim` 은 DiffDock 호출 규격과
과잉해석 규칙 10번의 출처로 쓰고 있다.

카탈로그에서 하나를 더 붙일 때 `bionemo-openfold2-nim` 이 가장 값이 크다. 이유가 둘이다.
타깃 단백질 구조를 우리 쪽에서 만들면 파이프라인 앞단이 채워지고, 팀원 C가 이미
`bionemo-msa-structure-prediction-pipeline` 으로 돌려 본 결과가 있어 붙이는 비용이 낮다.

**규칙이 더 필요한지는 재 보았고 답은 아니었다.** 구조 예측 결과에는 예측 신뢰도 지표가 딸려
오고(pLDDT, pTM, ipTM), 그 값을 결합 세기나 실험 구조와의 일치로 읽으면 규칙 10번, 11번과 같은
종류의 과잉해석이 된다. 그래서 해당 규칙 2종을 NVIDIA 스킬 문서에서 옮겨 적고 케이스 5건으로
쟀는데, **기존 15종만으로도 반려 정답 4건을 모두 잡았다.** 친화도 환산 금지와 RMSD 기준 명시가
도킹 점수에만 걸리게 적혀 있지 않아 pLDDT 에도 그대로 적용됐다. 두 규칙은
`overclaim_rules.STRUCTURE_RULES` 로 분리해 두고, 구조 예측을 실제로 붙이는 시점에 `RULES` 로
옮겨 다시 잰다. 측정은 `docs/notes/structure-rules-2026-09-27.md` 에 있다.

## 5. 아직 확인하지 못한 것

- 제출물에 스킬을 담아야 하는지, 활용만 하면 되는지에 대한 **주최 측 공식 답변**. 우리 해석으로
  진행하되 9/28 문의에 이 항목을 넣는다
- 카탈로그 스킬을 NAT 워크플로에서 부르는 표준 경로. 스킬은 에이전트용 문서 묶음이고 NAT
  함수와 층이 달라, 지금은 문서를 읽어 우리 도구를 만드는 방식으로 쓰고 있다
- `medtech-model-evidence-export` 의 출력 형식이 우리 근거 ID 구조와 실제로 맞는지

## 출처

- `github.com/NVIDIA/skills` GitHub API 조회 (2026-09-27)
- `skills/bionemo-openfold2-nim/SKILL.md` 원문 (라이선스 Apache-2.0 AND CC-BY-4.0)
- 팀원 A, 팀원 C의 카카오톡 조사 공유 (2026-09-27)
- 우리가 설치한 스킬의 출처와 용도는 `.claude/skills/nvidia-diffdock-nim/ATTRIBUTION.md`
