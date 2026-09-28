# NVIDIA 스킬 카탈로그 정리: 팀원이 고른 것과 그 밖에 쓸 만한 것

2026-09-27 작성. 팀원 A와 팀원 C가 카카오톡으로 주고받은 조사 결과를 **원문을 직접 받아
확인해 옮겼다.** 말로만 오간 내용은 시간이 지나면 팀 자산이 되지 않으므로, 무엇을 어떻게
확인했는지와 우리 쪽에 어디가 닿는지를 함께 적는다. 결정은
`docs/notes/04-platform/nvidia-skills.md` 에 적었고, 여기서는 그 결정의 근거를 담고 스킬별 참조표를 둔다.

조사 경위는 이렇다. 팀원 A가 `build.nvidia.com/skills` 를 훑어 후보 넷을 올렸고
(`bionemo-openfold2-nim`, `aiq-research`, `medtech-model-evidence-export`, 그리고 스킬 공개
형식의 예시로 `nvidia-skill-finder`), 팀원 C가 `nemo-retriever` 와 `rag-blueprint` 와
`skill-card-generator` 를 더했다. 팀원 C는 `bionemo-msa-structure-prediction-pipeline` 으로
실제 구조 예측까지 돌려 결과를 공유했다. 여기에 팀장이 카탈로그 전체를 훑어 넷을 더 찾았다.

## 1. 스킬의 구성과 설치

**에이전트가 읽는 사용 설명서 묶음이다.** 파이썬 패키지가 아니고 실행 파일도 아니다.
폴더 하나가 스킬 하나이고 안에 이렇게 들어 있다.

```
skills/<스킬명>/
  SKILL.md         앞머리(YAML)에 이름, 용도, 라이선스, 허용 도구. 본문에 절차와 예제 코드
  references/*.md  조건부로 읽는 상세 문서 (api, parameters, science, validation 등)
  skill-card.md    거버넌스용 요약 카드
  BENCHMARK.md     그 스킬로 에이전트를 돌린 벤치마크 기록
```

설치는 이렇게 한다. 설치하면 에이전트가 그 문서를 읽고 규격에 맞게 호출한다.

```bash
npx skills add NVIDIA/skills --skill bionemo-openfold2-nim
```

**라이선스가 두 갈래로 붙는다.** 코드는 Apache-2.0, 문서는 CC BY 4.0 인 스킬이 많고
`license` 앞머리에 `Apache-2.0 AND CC-BY-4.0` 으로 적혀 있다. 우리가 문서를 옮겨 쓰면
출처 표기가 필요하다. 지금 설치해 쓰는 스킬의 표기는
`.claude/skills/nvidia-diffdock-nim/ATTRIBUTION.md` 에 있다.

## 2. 카탈로그 규모

2026-09-27 에 직접 조회한 값이다.

| 항목 | 값 |
|---|---|
| 저장소 | `github.com/NVIDIA/skills`, Apache-2.0, 스타 3,455개 |
| `skills/` 아래 폴더 | 382개 |
| README 제품표에 실린 스킬 | 380개, 제품 96개 |
| 최근 갱신 | 2026-09-25 |

폴더 수와 표의 수가 둘 다르다. 표에 빠진 항목이 있는 것으로 보이고 우리 판단에 영향은 없다.

훑는 방법을 스크립트로 남겼다. 카탈로그가 자주 바뀌므로 다시 돌려 확인한다.

```bash
.venv/bin/python scripts/fetch_nvidia_skills.py                      # 목록과 관련 후보
.venv/bin/python scripts/fetch_nvidia_skills.py --detail <스킬명> ...  # 앞머리와 엔드포인트
```

결과는 `eval/results/nvidia_skills_<날짜>.json` 에 남는다. 관련 후보를 63개로 걸렀고
그 가운데 16개를 원문까지 읽었다.

**도킹 스킬은 카탈로그에 없다.** 이름에 dock 이 들어간 스킬이 없고 구조와 분자 쪽은
아래 셋뿐이다. 우리가 쓰는 `diffdock-nim` 은 이 카탈로그가 아니라
`NVIDIA-BioNeMo/bionemo-agent-toolkit` 에서 왔다. 문안에서 둘을 구분해 적는다.

## 3. 팀원이 고른 스킬

### bionemo-msa-structure-prediction-pipeline

팀원 C가 이 스킬로 실제 예측까지 돌렸다.
**MSA-Search 로 정렬을 찾고 그 정렬로 OpenFold3 가 구조를 예측하는 두 단계를 잇는다.**
팀원 A가 "카탈로그에 OpenFold3 는 없고 OpenFold2 까지 있다"고 본 것과 달리, 이 스킬이
OpenFold3 를 부른다. 스킬 문서의 의존 표에 `openfold3-nim` 이 적혀 있다.

| 항목 | 값 |
|---|---|
| 엔드포인트 | `health.api.nvidia.com/v1/biology/colabfold/msa-search/predict`, 같은 호스트의 `/msa-search/paired/predict`, `/biology/openfold/openfold3/predict` |
| 라이선스 | Apache-2.0 AND CC-BY-4.0 |
| 응답 값 | `confidence_score`, `complex_plddt_score`, `ptm_score` |
| 복합체 | `paired/predict` 로 사슬별 정렬을 받아 각 분자의 `msa` 와 `paired_msa` 에 넣는다 |

문서가 직접 밝힌 두 가지를 그대로 적어 둔다. 첫째, **서열과 정렬이 호출마다 NVIDIA 외부
API 로 전송된다.** 비공개 서열은 로컬 NIM 컨테이너를 쓰라고 적혀 있다. 우리는 공개 PDB
서열을 쓰므로 해당되지 않는다. 둘째, **MSA 품질이 좋아지면 pLDDT 가 오르고 pDE 가 내려간다.**
즉 그 값은 입력 조건에 좌우되므로 정확도의 절대 지표가 아니다.

팀원 C가 받은 값은 단백질과 니라파립 복합체에서 pLDDT 95.95, pTM 0.83, ipTM 0.66,
단백질 CA RMSD 1.0 Å, 리간드 RMSD 1.16 Å 이었다. RMSD 는 기준 구조와 견준 값이라 무엇과
견줬는지 함께 남겨야 한다.

### bionemo-openfold2-nim

팀원 A가 주최사 기술이라는 이유로 먼저 추천한 스킬이다.
**단일 사슬 단백질의 구조를 예측하고** AlphaFold2 계열이며 복합체는 다루지 않는다.
엔드포인트는 `health.api.nvidia.com/v1/biology/openfold/openfold2/predict-structure-from-msa-and-template`
하나이며 참조 문서 다섯 개(api, examples, parameters, science, validation)가 붙는다.

`references/science.md` 의 "Not For" 목록이 우리 과잉해석 규칙의 출처가 된다. 네 항목 가운데
둘을 그대로 옮겼다.

- "Direct ligand docking or affinity ranking"
- "Claiming experimental validation from toy sequences or shallow alignments"

같은 문서가 단일 서열 A3M 은 "weak evidence for a production-quality fold" 라고 적었다.
이 두 문장이 `overclaim_rules.STRUCTURE_RULES` 2종의 근거다.

### aiq-research

팀원 A가 문헌 조사용으로 올린 후보다.
**AI-Q 블루프린트 백엔드에 붙어 얕은 조사나 깊은 조사를 돌린다.** 여기에 걸림돌이 하나 있다.
앞머리가 "reachable NVIDIA AI-Q Blueprint backend" 를 전제로 하고, 그 백엔드를 띄우는 일은
별도 스킬 `aiq-deploy` 가 맡는다. 참조 문서에 docker-compose 와 kubernetes-helm 이 들어 있어
**설치와 배포가 필요하다.** 우리 로컬은 GPU 가 없으므로 마감 안에 붙이기 어렵다.

문헌 근거 수집은 이미 `pubmed_search` 도구로 하고 있다. 이 스킬은 본선 과제로 남긴다.

### nemo-retriever

팀원 C가 검색 증강 생성 쪽으로 올린 후보다.
**문서 모음을 넣고 찾는 명령행 도구이고** 26.8.1 기준이며 **로컬 LanceDB 색인을 지원해
서비스를 띄우지 않고도 쓸 수 있다.** PDF, 이미지, 오피스 문서, HTML, 텍스트, 음성, 영상을
받는다. 문서 편집과 웹 검색은 대상이 아니다.

우리 쪽에서 쓸 데가 분명하다. 팀원 B가 모아 준 보고 서식과 라벨과 국내 흐름 문서를 색인해
두면 사례를 평가할 때 유사 서식과 근거 문장을 찾아올 수 있다. PV 실무자가 과거의 유사 사례를
찾아 주면 좋겠다고 밝힌 대목이 바로 이 기능에 해당한다. `nemo-retriever-mcp` 는 같은 일을 MCP 로 붙이는 얇은 스킬이다.

### rag-blueprint

팀원 C가 함께 올린 후보다.
**검색 증강 생성 구성을 배포하고 설정하고 고친다.** 배포와 중지, 문제 해결 문서가 붙는다. 에이전트형 검색, 가드레일, 질의 재작성, 요약을 켜고 끄는 범위를 다룬다.
서비스를 띄우는 성격이라 `nemo-retriever` 의 로컬 색인보다 무겁다. 본선 과제로 남긴다.

`rag-eval` 은 같은 계열에서 품질을 재는 스킬이다. RAGAS 로 재고 `corpus/`, `train.json`,
`evaluate_rag.py` 라는 정해진 폴더 구조를 요구한다. 우리 평가 구조와 달라 그대로 쓰지 못한다.

### medtech-model-evidence-export

팀원 A가 올린 후보다.
**의료 AI 추론 실행에서 메타데이터, 파라미터, 재현 정보, 품질 지표, 검토 산출물에서
개인정보를 지운 뒤 MLflow 로 내보낸다.** 앞머리가 "evidence packs" 라는 말을 쓰고, 학습 추적이나 모델 등록,
임상 사용은 대상이 아니라고 못 박았다.

우리 근거 ID 구조와 성격이 같아 **형식을 견줘 볼 만하다.** 다만 MLflow 를 전제하고
Medical AI 추론 실행을 대상으로 하므로, 그대로 쓰기보다 어떤 항목을 근거로 남기는지를 참고한다.

### skill-card-generator, nvidia-skill-finder

`skill-card-generator` 는 이미 있는 스킬 폴더에 **거버넌스용 스킬 카드를 만들어 준다.**
주최 측이 스킬 제출을 요구하는 것으로 확인되면 이 스킬로 형식을 맞춘다.
`nvidia-skill-finder` 는 요청을 보고 어떤 NVIDIA 스킬이 맞는지 골라 주는 라우팅 스킬이고,
팀원 C가 스킬 공개 형식의 예시로 참고했다.

## 4. 팀장이 더 찾은 것

### nemotron-policy-generator

넷 가운데 이것이 우리에게 가장 쓸모가 크다.
**Nemotron 콘텐츠 안전 가드레일용 정책을 만들어 준다.** 산출물이 세 벌이다.
마크다운 정책, JSON 분류 체계, 그리고 바로 끼우는 추론 프롬프트다. 거친 낱말이나 기존
정책을 V2 범주로 옮겨 주고 사용자 정의 범주와 주제 준수 규칙을 더할 수 있다.
대상 모델은 Nemotron-Content-Safety-Reasoning-4B(텍스트)와 멀티모달 Nemotron-3-Content-Safety 다.

**우리 가드레일 계층이 지금 배선까지만 되어 있고 NemoGuard 시연을 못 한 상태다.**
이 스킬로 약물감시 도메인 정책을 만들면 그 빈 곳이 산출물로 채워진다. 의료 데이터를 다루는
주제라 보안과 윤리를 담자는 팀원 A의 제안과도 맞는다. 붙이는 비용이 낮은 편이다.

### bionemo-nvmolkit-usage

**GPU 로 RDKit 지문과 유사도, 형태 생성, 군집화를 돌리는 nvMolKit 사용 안내다.**
화합물 유사도로 후보를 견주는 작업에 쓸 수 있으나 **GPU 가 필요해 로컬에서는 못 돌린다.**
팀원 C가 받은 RunPod 자원이 있으면 가능하다.

### nemotron-retrieval-recipes

`embed` 와 `rerank` 조리법을 계획하고 조정하고 평가하는 스킬이다. 우리가 검토했던
리랭커 경로가 이 계정 모델 목록에 없어 못 썼는데, 이 스킬이 공개 조리법 쪽 경로를 담고 있어
다시 살펴볼 만하다.

### nemo-rl-auto-research

강화학습 실험을 가설 검증 형태로 자동 진행하는 워크플로다. **git 과 TSV 로그를 연구 원장으로
쓰고 사람의 감독을 유지한다**고 적혀 있어, 우리 하네스의 기록 방식과 생각이 닮았다.
지금 주제와는 거리가 있어 참고만 한다.

## 5. 지금 쓰는 것과 앞으로

| 스킬 | 출처 | 상태 |
|---|---|---|
| `diffdock-nim` | `NVIDIA-BioNeMo/bionemo-agent-toolkit` | **설치해 사용 중.** 호출 규격과 과잉해석 규칙 10번의 출처 |
| `bionemo-msa-structure-prediction-pipeline` | `NVIDIA/skills` | 팀원 C가 실행 확인. 붙일 후보 1순위 |
| `bionemo-openfold2-nim` | `NVIDIA/skills` | 규칙 2종의 출처로 이미 인용함. 단일 사슬만 필요할 때 쓴다 |
| `nemotron-policy-generator` | `NVIDIA/skills` | 가드레일 빈 곳을 채울 후보 |
| `nemo-retriever` | `NVIDIA/skills` | PV 문서 색인 후보. 로컬 색인이라 비용이 낮다 |
| `medtech-model-evidence-export` | `NVIDIA/skills` | 근거 형식 비교용 |
| `skill-card-generator` | `NVIDIA/skills` | 스킬 제출 요구가 확인되면 |
| `aiq-research`, `rag-blueprint`, `rag-eval`, `bionemo-nvmolkit-usage` | `NVIDIA/skills` | 배포나 GPU 가 필요해 본선 과제로 |

## 6. 문서 갱신 규율

카탈로그는 계속 바뀐다. 이름과 엔드포인트를 옮겨 적기 전에 스크립트로 다시 받아 대조한다.
받은 JSON 이 근거 파일이므로 문서에 쓴 값과 그 파일이 어긋나면 파일을 믿는다.
스킬 문서에서 문장을 옮길 때는 원문 그대로 인용하고 경로를 함께 적는다.
