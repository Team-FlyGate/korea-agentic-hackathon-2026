# FlyVigilante PR 설명 초안 (2026-09-28, 링크만 거는 형태)

**읽는 사람:** 이 PR을 여는 우리 팀원, 그리고 PR을 받는 FlyVigilante 팀.

아래 "PR 본문" 절을 GitHub PR 본문에 그대로 붙인다. "적용하는 법" 절은 PR을 여는 사람이 쓰는 명령이고
PR 본문에는 넣지 않는다. 패치 파일은
`$M/flyvigilante-metric-validation.patch`이고
FlyVigilante 커밋 `fdc046d` 위에서 만들었다.

## 왜 링크만 거는가

처음 계획은 결과 JSON, ROC 그림, 스킬 두 개, 근거 등급 코드를 FlyVigilante로 옮기는 것이었다. 팀장
검토 노트(`docs/notes/flyvigilante-review-2026-09-28.md`, 커밋 `2652709`)가 이렇게 정했다.

> 합치지 않는다. 한쪽에서 다른 쪽 파일을 복사하지도 않는다. 같은 파일이 두 곳에 생기면 어느 쪽이 원본인지 곧 알 수 없게 된다.

그래서 이 PR은 파일을 하나도 복사하지 않는다. 결과의 원본은 우리 저장소 노트에 두고, FlyVigilante에는
규칙 R13, 수치 세 벌을 담은 코드 상수, 그 노트로 가는 링크만 넣는다. 근거 등급 이식, `sig_rule_performance`
테이블, 스킬 두 개는 이번 PR에서 뺐다.

---

## PR 본문

### 제목

Add overclaim rule R13 and metric evidence IDs from the SIDER pilot

### 무엇을 바꾸나

| 파일 | 바뀌는 것 |
| --- | --- |
| `api/_fv/assess.py` | 과잉해석 규칙 R13을 더한다 |
| `api/_fv/evidence.py` | `RULE_PERFORMANCE` 상수와, 켜진 규칙마다 `metric:<rule>:sider-pilot@2026Q2` 근거 ID를 싣는 코드를 더한다. 상수 주석에 측정 노트 주소와 날짜를 적었다 |
| `docs/ARCHITECTURE.md` | 크리틱 T3 규칙 범위를 R1~R13으로 고친다 |
| `skills/pv-critic/SKILL.md` | 규칙 범위를 R1~R13으로 고치고 R13을 한 줄로 적는다 |
| `skills/pv-signal-memory/SKILL.md` | 근거 ID 목록에 `metric:` 한 줄을 더한다 |
| `README.md` | FlyGate 저장소 링크 설명을 고치고, 참조 세트 성능을 한 단락으로 적어 측정 노트로 링크한다. 크리틱 규칙 수를 13으로 고친다 |

R13 문구는 다음과 같다. 다른 규칙처럼 Nemotron 작성 지시와 Jev 크리틱 질문에 자동으로 들어간다.

> A threshold's reference-set sensitivity or specificity, and an evidence grade, describe the drug-reaction pair across a population; neither establishes causality in this individual case.

근거 묶음의 FAERS 2x2 행에서 규칙이 켜지면 아래 항목이 카탈로그에 한 번씩 실린다. 같은 규칙이 여러 반응에서
켜져도 한 번만 싣는다.

| 근거 ID | 켜지는 조건 | 민감도 | 특이도 | PPV |
| --- | --- | --- | --- | --- |
| `metric:evans:sider-pilot@2026Q2` | `evans` (PRR≥2, χ²≥4, a≥3) | 0.304 | 0.754 | 0.116 |
| `metric:ror_signal:sider-pilot@2026Q2` | `ror_sig` (ROR025>1, a≥3) | 0.474 | 0.676 | 0.134 |
| `metric:ic_signal:sider-pilot@2026Q2` | `ic_sig` (IC025>0) | 0.461 | 0.706 | 0.143 |

항목의 `what`은 이런 모양이다. 숫자가 근거 묶음에 들어가므로 크리틱 2단 숫자 오라클이 그대로 대조한다.

```
Evans threshold on the SIDER pilot reference set (31 drugs, 6,222 label-listed and 58,574 label-absent pairs, a>=3): sensitivity 0.304, specificity 0.754, PPV 0.116 for label listing; not causality
```

### 왜 바꾸나

메모가 "PRR 9.85로 Evans 기준을 넘었다"고 쓸 때, 그 기준이 라벨에 적힌 쌍을 얼마나 잡는지는 지금까지 어디에도
없었다. 이제 메모는 그 기준의 성능을 근거 ID로 인용할 수 있다. 크리틱은 그 성능을 이 케이스의 인과로 읽는
주장을 R13으로 막는다.

수치의 원본은 FlyGate 저장소의 측정 노트
(https://github.com/Team-FlyGate/korea-agentic-hackathon-2026/blob/main/docs/notes/metric-validation-2026-09-28.md)다.
FlyGate 팀이 이 저장소 웨어하우스(2012Q4부터 2026Q2까지 55분기)를 커밋 `fdc046d`의 스크립트로 재구축해서 쟀다.
양성은 SIDER 4.1 라벨에 그 PT가 적힌 쌍이고, 음성은 같은 약의 라벨에 없는 쌍이다. 약 31종, 양성 6,222쌍,
음성 58,574쌍이다. FlyGate 근거 등급의 신호 조건(Evans 또는 ROR)으로는 라벨 기재 쌍의 52.6%가 신호 없음으로
가장 낮은 D 등급을 받는다. 수치는 라벨 기재를 맞히는 성능이지 인과 판별 성능이 아니다. 한계와 재현 절차는
노트에 있다. 새 측정이 나오면 노트를 먼저 고치고, 이 저장소에서는 `RULE_PERFORMANCE` 상수와 근거 ID의 asof만 바꾼다.

### 확인한 것

이 저장소에는 테스트 폴더가 없어서 아래를 따로 돌렸다.

- `python -c "import api._fv.evidence, api._fv.assess"`가 오류 없이 끝난다.
- 네트워크 호출을 가짜로 바꾼 `bundle()`에서 세 규칙이 켜진 반응이 있으면 `metric:` 항목 세 개가 한 번씩 실리고, 규칙이 하나도 안 켜지면 하나도 실리지 않는다.
- 그 항목의 숫자를 인용한 주장은 크리틱 1단과 2단을 통과하고, 틀린 숫자(민감도 0.95)를 쓴 주장은 2단에서 걸린다.
- R13이 `OVERCLAIM_RULES`와 작성자 지시문에 들어간다.
- 패치가 깨끗한 `fdc046d` 트리에 그대로 적용된다.

`web/public/data/skills.json`은 이 PR에 넣지 않았다. `pipeline/build_skills.py`를 다시 돌리면 pv-critic과
pv-signal-memory 본문이 새 줄을 반영한다.

### 부탁: Jev 비교 팔

같은 참조 쌍에 Jev를 물어 지표와 같은 ROC 위에 곡선 두 개(약 이름을 보인 novel 팔, 가린 blind 팔)를 더하려
한다. 스크립트는 FlyGate 저장소의 `scripts/metric_validation_jev.py`다. 우리 팀에는 TypeSafe 키가 없어 돌리지
못했다. 키를 가진 분이 200쌍 시험부터 돌려 주면 좋겠다. 200쌍은 USD 0.01에 못 미치고, 전체 64,796쌍은 팔마다
USD 1.5 안팎이다(니라파립 벤치마크의 실측 입력 토큰 기준). 키는 환경 변수로만 넘기고 저장소나 로그에 남기지 않는다.

```bash
# FlyGate 저장소에서. --yes 가 없으면 비용만 찍고 멈춘다
export TYPESAFE_API_KEY=...
TZ=Asia/Seoul .venv/bin/python scripts/metric_validation_jev.py --arm novel --limit 200 --dry-run
TZ=Asia/Seoul .venv/bin/python scripts/metric_validation_jev.py --arm novel --limit 200 --pairs-tsv <pairs.tsv.gz> --jev-cache <cache_novel.json> --yes
TZ=Asia/Seoul .venv/bin/python scripts/metric_validation_jev.py --arm blind --limit 200 --pairs-tsv <pairs.tsv.gz> --jev-cache <cache_blind.json> --yes
```

novel 질문의 참은 "라벨에 없는 새 신호일 수 있다"라서 AUC가 0.5 아래로 갈 수 있다. 결과 JSON의 `jev.auc`와
`jev.auc_inverted`를 함께 읽는다. 절차와 기록 형식은 측정 노트 7절에 있다.

---

## 적용하는 법 (PR을 여는 사람용, PR 본문에 넣지 않는다)

FlyVigilante 커밋 메시지는 영어 명령형 문장이고 끝에 마침표가 없다(보이는 커밋은 "Load web fonts without
blocking render"). FlyVigilante로 가는 커밋만 그 형식을 쓰고 본문은 README처럼 한국어로 쓴다. 우리 저장소는
지금처럼 한국어 "~한다" 커밋 형식을 유지한다.

아래 명령은 사람이 직접 돌린다. 포크 주소와 브랜치 이름은 자기 것으로 바꾼다. 클론에 있는 `api/_data/*.json.gz`와
`web/public/data/faers/*` 변경은 웨어하우스를 다시 만들며 생긴 로컬 변경이라 패치에 없다.

```bash
git clone https://github.com/<you>/FlyVigilante.git
cd FlyVigilante
git checkout -b metric-evidence-ids fdc046d
git apply --check $M/flyvigilante-metric-validation.patch
git apply $M/flyvigilante-metric-validation.patch

git add api/_fv/assess.py api/_fv/evidence.py docs/ARCHITECTURE.md \
        skills/pv-critic/SKILL.md skills/pv-signal-memory/SKILL.md
git commit -m "Add overclaim rule R13 and metric evidence IDs from the SIDER pilot" \
           -m "켜진 신호 규칙마다 SIDER 파일럿 참조 세트의 민감도, 특이도, PPV를 metric:<rule>:sider-pilot@2026Q2 근거 ID로 싣고, 그 성능을 개별 케이스의 인과로 읽지 않도록 R13을 더한다. 수치 원본은 FlyGate 측정 노트다."

git add README.md
git commit -m "Point the harness-repo link at the rules and measurement scripts" \
           -m "FlyGate 저장소 링크 설명을 규칙 원본과 측정 스크립트, NAT 워크플로, OpenShell 정책으로 고치고, 참조 세트 성능 단락을 측정 노트로 연결한다."

git push -u origin metric-evidence-ids
gh pr create --repo Team-FlyGate/FlyVigilante --base main --head <you>:metric-evidence-ids \
    --title "Add overclaim rule R13 and metric evidence IDs from the SIDER pilot" \
    --body-file <PR 본문 절만 옮긴 파일>
```

README와 코드 주석의 노트 링크는 `Team-FlyGate/korea-agentic-hackathon-2026`의 `main` 브랜치를 가리킨다.
`metric-validation` 브랜치가 그 저장소 `main`에 들어가기 전까지 링크는 열리지 않는다. PR을 열기 전에 먼저 합친다.
