# 이름과 저장소 계획 (확정 전)

팀원이 합류하면 주제와 이름이 바뀔 수 있으므로 **아직 실행하지 않은 계획**으로 남긴다.
2026-09-25 기준.

## 완료 항목

| 대상 | 이전 | 현재 |
|---|---|---|
| 선행 프로젝트 GitHub | `kakyungkim/pharmasignal` | `kakyungkim/pharmasignal-v0` |
| 선행 프로젝트 로컬 | `hackthon/AgentForgeAI` | **바꾸지 않음**(사용자 지시로 되돌림) |

옛 GitHub 주소는 301로 새 주소에 연결된다. 저장소 설명에 v0이고 미수상 제출판이며 후속이
`pharmasignal`임을 적었다. 로컬 폴더 이름은 `AgentForgeAI` 그대로 두기로 했다. 행사 이름이
폴더에 남아 있어야 어느 해커톤 결과물인지 바로 알아볼 수 있다. GitHub 이름과 로컬 이름이
다르므로 문서에 경로를 적을 때 구분한다.

## 아직 하지 않은 것 (팀 확정 후 실행)

1. **새 GitHub 저장소 생성.** 이름은 `pharmasignal` 예정. 제출 직전까지 비공개로 두고 폼에 링크를
   넣기 직전에 공개로 바꾼다. 이 이름으로 만드는 순간 옛 주소의 자동 연결이 끊기고 8월 링크가
   새 프로젝트로 들어온다. 사용자가 지난 기록을 남길 필요 없다고 판단해 받아들인 사항이다.
2. **이번 프로젝트 로컬 폴더 이름 변경.** `KoreaAgenticAIhackathon`에서 `pharmasignal`로.
   여러 작업이 이 경로를 쓰고 있어 전부 끝난 뒤에 한다.

## 변경 가능 항목

팀원 구성에 따라 주제가 Night Shift(자율 코딩 에이전트)로 갈 수 있고, 그러면 이름도 달라진다.
공통 하네스와 크리틱, 샌드박스 정책은 어느 쪽이든 그대로 쓴다. 이름 치환은 문서와 그림 제목에만
걸리고 파이썬 모듈 이름은 건드리지 않기로 했으므로 나중에 바꿔도 비용이 작다.

## 계보 서술 방침

선행 프로젝트를 숨기지 않고 v0으로 부르며 한 문단으로 밝힌다. v0은 임상시험 등록의 이상사례만
보고 ROR을 계산했고 FAERS와 허가 라벨, 문헌을 다루지 못했다. 그 한계를 스스로 문서에 적어 두었고
이번 판이 그 셋을 메운다. "아이디어 단계"라고 낮춰 적지 않는다. v0은 파이썬 2,495줄에 테스트
28개, 영상과 슬라이드까지 있는 동작하는 파이프라인이다.

---

## 2026-09-27: 이전 대신 두 곳을 함께 둔다

팀원 A가 org `Team-FlyGate` 를 만들어 다섯 명을 Owner로 초대했다. 저장소를 옮기는 안을
검토했으나 **옮기지 않고 양쪽에 같은 내용을 두기로 했다.**

| 곳 | 주소 | 역할 |
|---|---|---|
| 개인 | `github.com/kakyungkim/korea-agentic-hackathon-2026` | 작업 이력과 프로필 노출. 지금까지의 링크가 그대로 산다 |
| 팀 org | `github.com/Team-FlyGate/korea-agentic-hackathon-2026` | 팀 공동 소유. 다섯 명이 같은 권한을 가진다 |

옮기면 개인 계정에서 사라지고 문서 네 곳과 제출 PDF의 주소를 한꺼번에 고쳐야 한다.
두 곳을 두면 둘 다 살고 제출 주소는 한 줄만 고르면 된다.

**배선.** `origin` 에 push url 두 개를 걸어 `git push` 한 번이 양쪽으로 간다.

```bash
git remote -v | grep push
# origin  https://github.com/kakyungkim/korea-agentic-hackathon-2026.git (push)
# origin  https://github.com/Team-FlyGate/korea-agentic-hackathon-2026.git (push)
```

fetch 는 개인 쪽만 본다. 팀원이 org 쪽에 직접 올리는 경우를 위해 `scripts/sync_remotes.sh` 를
두었다. 확인만 하려면 인자 없이, 맞추려면 `--push` 로 돌린다. 팀 쪽에만 있는 커밋이 있으면
조용히 덮어쓰지 않고 멈춘다.

**제출 주소는 개인 쪽을 그대로 쓴다.** 문안과 PDF가 이미 그 주소이고, 팀 저장소를 내야 하면
`docs/submission-flygate.md` 와 README의 주소 한 줄씩만 바꾸면 된다.
