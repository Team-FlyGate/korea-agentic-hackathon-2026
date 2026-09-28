# 팀 저장소 규약 (Team-FlyGate/Project-FlyGate)

팀 저장소에 무엇을 올리기 전에 **먼저 그쪽에 같은 것이 있는지 찾고, 없으면 그쪽 규약에
맞춰 넣는다.** 우리 저장소 규약을 그대로 들고 가지 않는다. 2026-09-28 지시.

실제로 한 번 어겼다. `scripts/collect_call_log.py` 를 만들어 PR 하려다가, 같은 일을 하는
`pipeline/bench/nvidia_call_audit.py`(456줄)가 이미 있는 것을 뒤늦게 봤다. `api/_fv/` 만
보고 `pipeline/` 과 `docs/` 를 안 봤다.

## 올리기 전 확인

1. `git ls-files` 로 전체 목록을 훑는다. 최상위 폴더마다 무엇이 있는지 먼저 본다
2. 기능 이름과 도메인 낱말로 `grep -rl` 한다. 코드뿐 아니라 `docs/` 와 `README.md` 도 본다
3. 테스트가 있으면 그것이 이미 잡아 둔 동작이다. `tests/` 를 읽고 나서 손댄다

## 폴더

| 위치 | 담는 것 |
|---|---|
| `api/_fv/*.py` | 런타임 모듈. 트리아지, 근거, 등급, 라벨 해석, 국내 인과성, 호출 기록기 |
| `pipeline/<영역>/*.py` | 데이터 적재와 측정. `faers`, `bench`, `connectome`, `discovery` |
| `agent/` | 에이전트 CLI(`agent/bin/flygate`), 스킬, 정책, 예시 입력 |
| `scripts/` | 내려받기와 렌더. 쇼릴 빌드, FAERS 내려받기, 설치 스크립트 |
| `tests/test_<모듈>.py` | `api/_fv` 모듈 이름을 그대로 따른다 |
| `web/public/data/*.json` | 커밋하는 요약본 |
| `data/`, `*.duckdb` | gitignore. 스크립트로 다시 만든다 |

## 문서

- 판이 올라가는 문서는 `docs/<이름>_v<major.minor.patch>.md` 로 새 파일을 만든다.
  이전 판을 덮어쓰지 않는다. README 도 `docs/readme-versions/v<x.y.z>/README.md` 에 남긴다
- 판이 없는 문서는 `docs/ARCHITECTURE.md` 처럼 대문자 이름 하나로 둔다

## 문체와 주석

**그쪽 코드 주석과 docstring 은 한국어 합니다체다.** 우리 저장소는 영어 주석이지만
팀 저장소에 넣는 코드는 그쪽을 따른다. 우리 규약을 옮기지 않는다.

## 기록과 안전

- 호출 기록은 `FV_CALL_LOG` 가 있을 때만 남기고 키와 프롬프트와 응답 본문은 넣지 않는다
- 실패를 지우지 않는다. OpenFold3 504 같은 것을 그대로 기록한다
- 결과마다 근거 ID(`faers:2x2:<약>:<반응>@<분기>`, `label:<setid>#<절>`, `pubmed:<PMID>`)를 붙인다
