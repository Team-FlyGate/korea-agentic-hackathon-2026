# Jev 를 1단 선별에 쓸 수 있는가: 첫 실측

2026-09-27 밤 측정. 회의에서 나온 3단 선별 구성의 1단 후보가 Jev 다. 값싼 판정 모델을
비싼 추론 모델 앞에 두자는 안이고, 이 문서는 그 모델을 처음으로 우리 데이터에 대 본 기록이다.

**결론을 먼저 적는다. 지연과 비용은 1단에 쓸 만하고, 판정 품질은 이번 측정으로 판단할 수 없다.**
비교 기준으로 삼은 고정 규칙이 10건 모두 같은 답을 내어 비교가 서지 않았다. 같은 10건에서
크리틱 모델도 10건을 모두 넘겼고, **실제로 걸러 낸 판정기는 Jev 하나였다.**

## 1. 접근 경로와 비용

가입은 열려 있다. 다만 **무료 크레딧이 없어 크레딧을 넣기 전에는 API 키가 발급되지 않는다.**
키 생성 화면이 "this organization has no available credits" 로 막는다.

| 항목 | 값 |
|---|---|
| 최소 충전 | 5달러. 이번에는 10달러를 넣었다 |
| 크레딧 만료 | 구매일로부터 12개월 |
| 요금 | 입력 백만 토큰당 0.042달러, 출력 무료 |
| 자동 충전 | 기본 꺼짐 |

키는 콘솔의 키 대시보드에서 만들고 `TYPESAFE_API_KEY` 로 읽는다. org 단위로 묶이며 만든
사람이 빠져도 키는 살아 있다고 화면이 밝힌다.

## 2. 호출 규격에서 걸린 것

첫 호출이 HTTP 422 로 막혔고 응답이 빠진 필드를 정확히 알려 주었다.

```
"loc": ["body","questions","time_order","choice","criteria"], "msg": "Field required"
```

세 질문 유형이 모두 `criteria` 를 받지만 **모양이 서로 다르다.** 문서를 읽고 맞춘 것이 아니라
이 응답으로 확인했다.

| 유형 | criteria |
|---|---|
| noul | 선택. `true` 와 `false` 가 각각 무엇을 뜻하는지 적은 객체 |
| choice | 필수. 선택지 이름을 설명에 대응시킨 맵 |
| score | 필수. 단계 설명을 낮은 쪽부터 늘어놓은 배열. 2단계에서 10단계 |

## 3. 단건 실측

니라파립과 혈소판감소증 한 건에 세 질문을 던졌다.

```bash
export TYPESAFE_API_KEY=...
.venv/bin/python scripts/jev_triage_probe.py --route direct
```

HTTP 200, 0.8초, 입력 851 출력 75 토큰이었다. 모델은 `jev-1.13.0` 이다.

| 질문 | 답 |
|---|---|
| needs_human (noul) | 0.54 |
| time_order (choice) | supports, 확신도 0.46. 분포는 supports 0.64, absent 0.36, contradicts 0.0 |
| signal_score (score) | 2.98, 확신도 0.98. 3단계에 확률 0.99 |

**signal_score 가 가장 값진 답이다.** 3단계는 "강하지만 이미 라벨에 적혀 있음" 으로 정의한
칸이다. PRR 9.13 으로 신호가 뚜렷하면서 허가사항에 기재된 반응이라는 두 사실을 함께 반영했다.
불균형 지표가 높다는 이유만으로 최고 단계를 고르지 않았다.

**time_order 는 답과 확신도가 어긋났다.** state 에 `drug_start_date: true` 만 있고 실제 날짜가
없으니 갈리는 것이 옳다. 모델이 애매한 것을 애매하다고 답한 것으로 읽는다.

## 4. 10건 벤치마크

```bash
.venv/bin/python scripts/bench_triage_scale.py --judge jev --limit 10 \
  --label jev-niraparib --price-in 0.042 --price-out 0
```

니라파립의 보고 건수 상위 10개 이상사례에 noul 질문 하나를 던지고, 확률 0.5 이상을 사람에게
넘기는 것으로 잘랐다. 결과는 `eval/results/triage_scale_jev-niraparib.json` 이다.

| 이상사례 | noul | 판정 | 규칙 | PRR | 카이제곱 |
|---|---|---|---|---|---|
| NAUSEA | 0.48 | no | yes | 7.00 | 30,711 |
| FATIGUE | 0.46 | no | yes | 6.43 | 24,758 |
| PLATELET COUNT DECREASED | 0.48 | no | yes | 38.24 | 144,679 |
| CONSTIPATION | 0.54 | yes | yes | 18.34 | 66,725 |
| INSOMNIA | 0.52 | yes | yes | 10.20 | 23,603 |
| BLOOD PRESSURE INCREASED | 0.54 | yes | yes | 15.45 | 32,925 |
| HEADACHE | 0.40 | no | yes | 3.60 | 4,565 |
| OFF LABEL USE | 0.63 | yes | yes | 2.46 | 2,028 |
| VOMITING | 0.46 | no | yes | 4.13 | 4,918 |
| DECREASED APPETITE | 0.45 | no | yes | 7.77 | 11,148 |

| 지표 | 값 |
|---|---|
| 규칙과 일치 | 4/10 |
| 지연 중앙값 | 255ms (평균 299ms) |
| 건당 토큰 | 입력 543.5, 출력 22.0 |
| 건당 비용 | 0.0000228달러 |
| 1,000건 추정 | 0.023달러, 순차 0.08시간 |

## 5. 해석

**일치율 4/10 을 정확도로 읽으면 안 된다.** 고정 규칙이 10건 모두 yes 를 냈기 때문이다.
니라파립 상위 10건은 전부 PRR 2 이상에 카이제곱 4 이상이라 Evans 기준을 통과한다. 비교 기준이
상수이면 일치율은 Jev 의 yes 비율을 다시 쓴 값에 지나지 않는다. **이 약물에서는 고정 규칙이
아무것도 걸러 내지 못한다는 사실이 오히려 이번 측정의 소득이다.**

**확률이 0.40에서 0.63 사이에 몰렸다.** 임계값을 0.45로 내리면 여덟 건이 yes 가 되고 0.55로
올리면 두 건으로 줄어든다. 지금 상태로는 임계값을 정할 근거가 없다.

**순서가 지표와 어긋난다.** 혈소판감소증은 PRR 38.24 로 가장 강한데 0.48 이고, 허가사항 외
사용은 PRR 2.46 으로 가장 약한데 0.63 이다. 혈소판감소증은 라벨에 이미 적힌 반응이라 검토자가
먼저 볼 이유가 적고 허가사항 외 사용은 성격이 다른 신호라는 판단으로 읽으면 말이 된다.
단건 실험에서 "강하지만 이미 라벨에 적혀 있음" 을 고른 것과 같은 방향이다.

**다만 이 판단이 숫자에서 나왔다고 말할 수 없다.** state 에 약물명과 이상사례명을 함께 넣었고
라벨 기재 여부는 넣지 않았다. 모델이 니라파립에 대해 이미 아는 것으로 답했을 가능성이 크다.
그래서 이번 측정은 "숫자와 사전 지식을 합친 판단" 으로 읽어야 한다.

**지연과 비용은 확실하다.** 건당 255ms 와 0.0000228달러는 1단 게이트에 넉넉하다.
크리틱 3단의 Nemotron 이 건당 2,243ms 였으니 한 자릿수 배 빠르다.

## 6. Nemotron 과 나란히

같은 10건을 크리틱이 쓰는 모델로 돌렸다.

```bash
set -a; source .env; set +a
.venv/bin/python scripts/bench_triage_scale.py --limit 10 --label nemotron-niraparib
```

| 이상사례 | Jev noul | Jev | Nemotron | 규칙 |
|---|---|---|---|---|
| NAUSEA | 0.48 | no | yes | yes |
| FATIGUE | 0.46 | no | yes | yes |
| PLATELET COUNT DECREASED | 0.48 | no | yes | yes |
| CONSTIPATION | 0.54 | yes | yes | yes |
| INSOMNIA | 0.52 | yes | yes | yes |
| BLOOD PRESSURE INCREASED | 0.54 | yes | yes | yes |
| HEADACHE | 0.40 | no | yes | yes |
| OFF LABEL USE | 0.63 | yes | yes | yes |
| VOMITING | 0.46 | no | yes | yes |
| DECREASED APPETITE | 0.45 | no | yes | yes |

| 판정기 | 규칙과 일치 | 지연 중앙값 | 건당 토큰 | 걸러 낸 비율 |
|---|---|---|---|---|
| 고정 규칙 | 기준 | 0.01ms 수준 | 없음 | 0/10 |
| Nemotron 3 Super | 10/10 | 823.9ms | 364.4 | 0/10 |
| Jev 1.13 | 4/10 | 255.0ms | 565.5 | 6/10 |

**셋 가운데 실제로 걸러 낸 것은 Jev 뿐이다.** 규칙과 Nemotron 은 10건을 모두 사람에게
넘겼으므로, 1단 게이트로 놓으면 아무것도 줄이지 못한다. 일치율 10/10 은 Nemotron 이 정확하다는
뜻이 아니라 **이 데이터에서 규칙과 같은 자리에 서 있다**는 뜻이다.

**의견이 갈린 지점이 질문의 뜻을 드러낸다.** Jev 는 니라파립 라벨에 이미 적힌 반응
(오심, 피로, 혈소판감소증, 두통, 구토, 식욕감소)을 넘기지 않고, 성격이 다른 신호
(허가사항 외 사용)와 라벨 밖으로 읽히는 것들을 넘겼다. Nemotron 은 불균형 지표가 기준을
넘었다는 사실만으로 전부 넘겼다.

**우리가 던진 질문이 애매했던 탓이 크다.** "검토자가 먼저 읽어야 하는가" 는 두 가지로 읽힌다.
새로운 신호를 찾는 일이라면 라벨에 있는 반응은 넘기지 않는 쪽이 맞고, 개별 사례를 처리하는
일이라면 신호 세기로 줄 세우는 쪽이 맞다. 두 모델이 서로 다른 쪽을 골랐다.
**다음 측정에서는 질문을 하나로 못 박아야 한다.** 예를 들어 "라벨에 아직 없는 새로운 신호일
가능성이 있는가" 로 바꾸면 두 판정기를 같은 기준에서 비교할 수 있다.

지연은 Jev 가 세 배 남짓 빠르고 토큰은 오히려 더 쓴다. 입력에 criteria 를 함께 싣기 때문인데,
출력이 무료라 비용은 그래도 Jev 쪽이 싸다.

## 7. 다음에 할 것

- **규칙이 갈리는 약물로 다시 잰다.** 상위 이상사례가 전부 신호로 걸리지 않는 약물을 골라야
  일치율이 뜻을 갖는다
- **이름을 가린 대조군을 돌린다.** 약물명과 이상사례명을 지운 state 로 같은 10건을 다시 물으면
  사전 지식과 숫자를 가를 수 있다
- **라벨 기재 여부를 state 에 넣는다.** 지금은 모델이 기억으로 채우는 자리를 근거로 바꾼다
- **같은 10건을 Nemotron 으로 돌려 나란히 둔다.** 명령은 아래와 같고 `.env` 의 키가 필요하다

```bash
set -a; source .env; set +a
.venv/bin/python scripts/bench_triage_scale.py --limit 10 --label nemotron-niraparib
```

마감이 9/28 밤이므로 위 네 가지는 본선 준비로 미룬다. 지금 확정된 것은 접근 경로와 호출 규격,
그리고 지연과 비용이다.

## 8. 만든 것

| 파일 | 무엇 |
|---|---|
| `src/harness/tools/jev_client.py` | systemone 호출, noul 확률 읽기, 토큰 정규화 |
| `scripts/jev_triage_probe.py` | 단건에 세 질문. TypeSafe 직접과 Vercel 게이트웨이 두 경로 |
| `scripts/bench_triage_scale.py --judge jev` | 여러 건을 돌려 고정 규칙과 일치율 비교 |
| `eval/results/jev_triage_probe.json` | 단건 응답 원문 |
| `eval/results/triage_scale_jev-niraparib.json` | 10건 결과와 요약 |
