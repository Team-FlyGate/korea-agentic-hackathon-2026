# 불균형 지표 성능 검증과 문헌, 오믹스 근거 확장 계획

**읽는 사람:** 팀원 전체. 특히 FlyVigilante 의 Signal Memory 층과 대시보드 벤치 화면을 맡은 쪽,
그리고 인과성 평가 항목을 채우는 쪽.

2026-09-27 작성. 두 저장소를 읽고 정리한 설계 제안이다. 우리 저장소
(`kakyungkim/korea-agentic-hackathon-2026`)와 후속 데모 `Team-FlyGate/FlyVigilante` 의 코드와
문서를 근거로 삼았다. 아직 실행한 것은 없다. 어디까지가 확인한 사실이고 어디부터가 제안인지
구분해 적는다.

## 1. 지금 두 저장소에 있는 것

| 층 | 우리 저장소 | FlyVigilante |
|---|---|---|
| Jev 1단 선별 | `scripts/bench_triage_scale.py --judge jev`. 쌍 단위(약 × 반응). 질문 하나(noul) | `api/_fv/triage.py`. 사례 단위(ICSR 하나). 질문 일곱 개를 한 호출로 |
| 인과성 | `scripts/jev_causality_score.py`. 한국형 알고리즘 ver 2.0 여덟 항목을 Jev 에 choice 로 묻고, 점수 합산과 등급은 파이썬이 한다 | 일곱 질문 가운데 `causality` 하나. WHO-UMC 다섯 범주 choice. 점수 없음 |
| Jev 와 Nemotron 연결 | 없음. 같은 10건을 따로 돌려 나란히 비교만 했다 | 있음. Jev 가 `expedite` 나 `signal_review` 로 올린 사례만 Nemotron 이 평가한다 |
| 불균형 지표 | openFDA 호출로 PRR, ROR, χ² 를 쌍마다 계산. IC 없음 | DuckDB 웨어하우스 `sig_disproportionality` 에 약물-반응 쌍 1,824,832건의 PRR, ROR, χ², IC, IC025 |
| 문헌 | 없음 | `api/_fv/evidence.py::pubmed`. esearch 로 PMID 상위 5개와 총 건수만 받는다. 초록과 본문은 읽지 않는다 |
| 근거 ID | 없음 | `faers:case:`, `faers:2x2:`, `label:`, `pubmed:` 네 종. Nemotron 은 이 목록에 있는 ID 만 인용할 수 있다 |
| 정답 | 없음. Jev 와 Nemotron 의 "일치율"은 고정 규칙 대비다 | 중대성만 있다. FAERS 결과 코드가 정답 역할을 하고 AUROC 0.940 을 냈다. 인과성 정답은 없다 |

두 저장소 모두 PRR, ROR, IC 를 고정 임계값(Evans 기준 등)으로 신호 여부를 가른다. 그 임계값이
어떤 참조 세트에서 어느 정도 민감도와 특이도를 내는지는 어디에도 측정되어 있지 않다.

## 2. 제안 하나. 불균형 지표의 성능을 잰다

양성 쌍과 음성 쌍을 정하고, 쌍마다 PRR, ROR, χ², IC 를 계산해 지표별로 다음을 낸다.

- 임계값별 민감도, 특이도, 양성예측도
- ROC 곡선과 AUC
- 지금 쓰는 고정 임계값(Evans: PRR ≥ 2, χ² ≥ 4, a ≥ 3 등)이 그 곡선 위 어디에 있는지

여기까지는 모델이 필요 없다. 순수 계산이다.

Jev 는 비교 대상으로 같은 그림에 올린다. 같은 참조 쌍에 `novel` 질문의 noul 확률을 받아 임계값을
바꿔 가며 ROC 를 그리면, 불균형 지표 넷과 Jev 가운데 누가 참조 세트를 더 잘 가르는지 한 그림에서
보인다. 이름을 가린 `--blind` 팔을 함께 올리면 Jev 의 판별이 숫자에서 온 것인지 약 이름에서 온
것인지도 같은 축에서 드러난다.

## 3. 참조 쌍을 어떻게 정하는가

이 계획의 병목은 계산이 아니라 참조 세트다. 두 저장소 어디에도 정답 쌍이 없다.

**양성 쌍 후보.** `docs/notes/pv-datasets.md` 에 팀원 B 조사와 함께 정리된 공개 자료 가운데
쌍 단위 목록을 제공하는 것은 넷이다.

| 자료 | 무엇을 주는가 | 성격 |
|---|---|---|
| SIDER 4.1 | 라벨에서 뽑은 약물-부작용 쌍 139,756건. 2015년 자료 | 라벨 기재 |
| OnSIDES | DailyMed 라벨 46,686건에서 뽑은 쌍 360만건 초과. 2023년 기준 | 라벨 기재 |
| EMA PRAC 월간 신호 권고 | 규제기관이 결론에 이른 약물-이상사례 조합 | 규제 결론 |
| 국내 실마리정보 알리미 | 허가사항에 반영된 사례 128건 | 규제 결론 |

넷 모두 "라벨에 있다" 또는 "규제기관이 인정했다"이고 인과성 판정은 아니다. 인과성 평가 결과를
가진 공개 자료는 KAERS 원시자료 하나인데 신청 뒤 30일이 걸려 이번 일정에는 쓰지 못한다.

**음성 쌍은 정의가 없다.** 열두 자료 어디에도 "이 약은 이 반응을 일으키지 않는다"는 목록이
없다. "SIDER 에 없음"을 음성으로 두면 아직 발견되지 않은 신호를 음성으로 세는 오류가 생긴다.
음성을 어떻게 정할지는 팀이 정해야 하고, 그 정의가 AUC 를 좌우한다.

**그래서 이 측정이 재는 것은 "불균형 지표가 라벨 기재를 예측하는가"이다.** 인과성 예측 성능이
아니다. 그림 옆에 참조 세트 구성과 이 한계를 한 단락으로 반드시 같이 쓴다. 메모가 이 수치를
인과성 성능처럼 인용하면 FlyVigilante 규칙 R5(라벨 기재는 개별 사례 인과 확정이 아님) 위반이다.

## 4. 어디서 계산하는가

openFDA 가 아니라 FlyVigilante 웨어하우스에서 뽑는다. 이유는 셋이다.

- 웨어하우스에는 이미 쌍 1,824,832건의 PRR, ROR, χ², IC, IC025 가 있다. 우리 저장소의 openFDA
  경로에는 IC 가 없어 새로 짜야 한다.
- openFDA 로 다시 계산하면 쌍마다 세 번 호출이 들고, 키를 받아도 하루 120,000회 제한이다.
- openFDA 는 caseid 버전 중복과 FDA 삭제 목록을 반영하지 않는다. 웨어하우스는 둘 다 반영했다.

openFDA 는 몇 쌍만 교차 검증에 쓴다. 우리 저장소의 `evidence_grade.py` 가 니라파립과
혈소판감소증에서 a=1,065, PRR 9.13 을 낸 것과 웨어하우스 값을 맞춰 보는 정도다.

## 5. 어디에 붙는가

**Action 층 뒤가 아니다.** 이 계산은 쌍 단위이고 정적이며 분기마다 한 번 돈다. 케이스 흐름의
끝에 붙는 것이 아니라 Signal Memory 층의 메타데이터다.

근거 ID 를 하나 늘린다. 예를 들어 `metric:PRR@2.0:<refset>@<asof>` 는 "PRR ≥ 2 임계값이 참조 세트
`<refset>` 에서 내는 민감도와 특이도"를 가리킨다. Nemotron 이 메모에서 PRR 을 언급할 때 이 ID 를
같이 인용하게 하면, 메모가 "PRR 9.13 은 신호다"에서 "PRR 9.13 은 참조 세트 X 에서 특이도 y 의
임계값을 넘는다"로 바뀐다. 크리틱 T2 숫자 오라클이 민감도와 특이도 수치도 검사할 수 있게 된다.

Feedback 층과는 이렇게 이어진다. 처음에는 3절의 외부 참조 세트로 ROC 를 그린다. 검토자의
판정이 사람 큐에 쌓이면 그것이 참조 세트가 되어 같은 계산을 다시 돈다. FlyVigilante
아키텍처 문서에서 "설계"로만 적혀 있는 Feedback 층(검토 결과로 임계값 보정)이 이 경로로 실제
구현이 된다.

## 6. 제안 둘. 문헌을 읽는 단계를 넣는다

지금 FlyVigilante 의 문헌 근거는 PMID 목록이다. Nemotron 은 `pubmed:<pmid>` 를 인용하지만 그
논문이 무엇을 말하는지 모른다. 숫자 오라클도 논문 수치는 검사하지 못한다. 번들에 없기 때문이다.

붙일 위치와 순서는 이렇다.

1. `evidence.py::pubmed` 에서 esearch 뒤 efetch 로 초록을 받는다. eutils 는 이미 허용 호스트다.
2. 초록마다 Jev 에 typed 질문을 던진다. 연구 설계(choice: 증례보고, 관찰연구, 무작위시험,
   기전연구), dechallenge 와 rechallenge 보고 여부(noul), 저자의 인과 결론 강도(score).
   우리 저장소의 `src/harness/tools/jev_client.py` 형식을 그대로 쓴다.
3. 결과를 `pubmed:<pmid>#design`, `pubmed:<pmid>#dechallenge` 같은 근거 ID 로 카탈로그에
   넣는다. 한국형 알고리즘의 "약물에 대해 알려진 정보"와 "비약물요인" 항목의 입력으로도 쓴다.
   나머지 여섯 항목은 개별 사례의 필드라 논문으로 채울 수 없다.
4. Nemotron 이 그 ID 를 인용해 종합하고, 크리틱 T3 가 과잉해석을 검사한다.

Jev 는 글을 만들지 않으므로 논문을 요약하거나 상충하는 결과를 조정하지 못한다. 그 일은 Nemotron
몫이다. Jev 의 자리는 논문 수백 편을 건당 0.3초 안에 등급화하는 앞단이다.

## 7. 제안 셋. 오믹스 근거를 다른 축으로 넣는다

불균형 지표는 "실제로 보고됐는가"를 재고, 오믹스는 "생물학적으로 그럴 수 있는가"를 잰다.
인과성 틀에 이미 있는 항목이다. WHO-UMC 의 pharmacologically plausible, 한국형 알고리즘의
"약물에 대해 알려진 정보"가 그 칸이고, 지금은 라벨 플래그 하나로만 채운다.

**metric 후보.**

| 축 | 묻는 것 | 값 |
|---|---|---|
| 표적과 조직 발현 | 약의 표적이 반응이 일어난 장기나 조직에서 발현되는가 | 발현 수준 또는 순위, 이진 플래그 |
| 표적과 표현형 연관 | 표적 유전자의 변이나 결손이 이 반응과 닮은 표현형과 연결되는가 | 연관 근거 건수, 소스 등급 |
| 약물유전체 | 이 약과 이 이상반응에 약물유전체 주석이 있는가 | 소스의 근거 수준 |
| 섭동 시그니처 | 약 처리 전사체 시그니처가 반응 관련 질환 시그니처와 겹치는가 | 연결 점수 |

넷을 합친 기전 타당성 점수가 새 metric 이다. 근거 ID 는 `omics:<source>@<version>:<drug>:<pt>`
형태로 하고, 어느 데이터베이스의 어느 버전을 언제 조회했는지를 ID 에 박는다.

**검증은 2절과 같은 틀로 한다.** 같은 참조 쌍에서 기전 타당성 점수의 ROC 를 PRR, ROR, IC 옆에
그린다. 그리고 둘을 합친 것(불균형 신호이면서 기전 타당성이 있는 쌍)의 ROC 를 그린다. 보여줄
결과는 "두 축이 독립적이라 합치면 특이도가 오르는가"이다. 오믹스 축은 보고 편향에 영향을 받지
않으므로 FAERS 단독보다 나을 가능성이 있다. 나아지지 않으면 그것도 결과다.

**주의 셋.**

- 반응(MedDRA PT)을 조직이나 표현형으로 옮기는 매핑이 필요하다. 이 매핑도 출처가 있어야 한다.
  손으로 만들지 않는다.
- 약의 표적 정의도 출처가 있어야 한다. "이 약의 표적은 무엇이다"를 기억으로 쓰지 않고 조회한
  데이터베이스와 버전을 ID 에 남긴다.
- 후보 소스는 조직 발현 아틀라스, 유전자와 표현형 연관 플랫폼, 약물유전체 지식베이스, 약물 섭동
  시그니처 라이브러리 네 부류다. 어느 것을 쓸지는 접근 방법과 라이선스를 확인한 뒤 정한다.
  이 문서를 쓰는 시점에 확인한 것은 없다.

## 8. 크리틱 규칙 추가

FlyVigilante 규칙 R5 는 "라벨 기재는 개별 사례의 인과를 확정하지 않는다"이다. 같은 이유로
규칙 하나를 더 둔다. "기전 타당성은 개별 사례의 인과를 확정하지 않는다." 표적이 그 조직에서
발현된다는 사실은 이 환자에게 약이 그 반응을 일으켰다는 뜻이 아니다. 3절의 지표 성능 수치도
같은 규칙 아래 있다. 참조 세트 대비 특이도가 높다는 것은 이 사례의 인과가 아니다.

## 9. 하지 않는 것과 지금 없는 것

- FAERS 를 정답이라 부르지 않는다. 공개본에 인과성 필드가 없다.
- Naranjo 는 구현하지 않는다. 팀원 B 가 공유한 표의 배점이 Naranjo 1981 과 달라 원본 대조가 끝나기
  전까지 보류한 상태다(`docs/notes/causality-assessment.md`).
- 참조 세트의 음성 정의, 오믹스 소스 선정, MedDRA PT 와 조직의 매핑 출처는 이 문서에 없다. 셋 다
  팀이 정해야 하는 항목이고 정하기 전에는 2절과 7절을 실행할 수 없다.
- 두 저장소 모두 Jev 관련 코드에 테스트가 없다. 새 단계를 붙이기 전에 `jev_client.py` 와 `jev_one`
  부터 테스트를 두는 편이 안전하다.

## 출처

이 세션에서 실제로 읽은 파일만 적는다.

우리 저장소
- `docs/notes/jev-primer.md`, `docs/notes/jev-triage-2026-09-27.md`, `docs/notes/causality-assessment.md`,
  `docs/notes/pv-datasets.md`, `docs/notes/evidence-grade-2026-09-28.md`, `docs/notes/report-form-mapping.md`,
  `docs/notes/team.md`
- `scripts/bench_triage_scale.py`, `scripts/jev_causality_score.py`, `scripts/jev_triage_probe.py`,
  `src/harness/tools/jev_client.py`
- `eval/results/jev_triage_probe.json`, `eval/results/triage_scale_jev-niraparib.json`,
  `eval/results/triage_scale_nemotron-niraparib.json`

FlyVigilante (`https://github.com/Team-FlyGate/FlyVigilante`, 커밋 fdc046d, 2026-09-27 얕은 클론)
- `README.md`, `docs/ARCHITECTURE.md`, `docs/약물감시_개요.md`
- `api/_fv/triage.py`, `api/_fv/evidence.py`, `api/_fv/assess.py`
- `skills/pv-reflex-triage/SKILL.md`, `skills/pv-deliberate-assess/SKILL.md`, `skills/pv-critic/SKILL.md`,
  `skills/pv-signal-memory/SKILL.md`, `skills/faers-warehouse/SKILL.md`, `skills/pv-guardrail-policy/SKILL.md`
