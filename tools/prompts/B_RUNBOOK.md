# 트랙 B 실행 순서 (비서용)

화면 개선 트랙. B0(하니스 + 역할 분리 + 기준 점수) → R1 → R2 → 사용자 최종 검토 (DECISIONS O8: 최대 2라운드).
모든 에이전트는 worktree `c:\projects\fun\.worktrees\b` (브랜치 `b/ui`)에서 일한다. 스크립트 단계는 비서가 worktree 루트에서 돌린다.
채택·되돌림은 스크립트(`python -m ui_eval.score --compare`)가 정한다. push는 사용자 지시가 있을 때만.

에이전트에 줄 지시는 한 줄이다 (Workflow 스크립트 문자열에 백틱을 넣지 않는다):

    c:\projects\fun\.worktrees\b 에서 tools/prompts/<파일> 을 읽고 그대로 수행하라. <덧붙임>

## 준비 (비서, 메인 체크아웃에서)

```
git worktree add .worktrees/b -b b/ui main
cd .worktrees/b
python tools/golden_check.py --ui            # "통과" (스냅샷을 worktree로 복사한다)
python -m ui_eval.make_answers --check       # "같음" 2줄 (정답 파일이 지금 스냅샷·과제와 맞는다)
```

`make_answers --check`가 "다름"이면 멈추고 총괄에 보고한다 (스냅샷이나 과제가 정답을 만든 뒤 바뀌었다).

## 과제 묶음 (페르소나 에이전트 하나에 한 묶음)

| 묶음 | 과제 |
|---|---|
| A | T01 T02 T03 T04 |
| B | T05 T06 T07 T08 |
| C | T09 T10 T11 T12 |
| D | H01 H02 H03 |
| E | H04 H05 H06 |

한 라운드의 페르소나 실행 = (p1, p2) × (A~E) = haiku 에이전트 10개. 서로 독립이라 동시에 돌려도 된다 (동시 3개까지 권장).
지시 덧붙임 예: `실행 r0, 페르소나 p1, 과제 T01 T02 T03 T04.`

## B0

| 단계 | 실행 | 모델 | 지시 / 스크립트 |
|---|---|---|---|
| 1 | H | sonnet | `B0_H_harness.md` |
| 2 | 게이트 H | haiku(스크립트만) | 아래 "게이트 H" 전부 → `git tag b-h` |
| 3 | U0 | sonnet | `B0_U0_roles.md` |
| 4 | 게이트 U | haiku(스크립트만) | 아래 "게이트 U" (실행 이름 `r0`, 기준 태그 `b-h`) 전부 → `git tag b-r0` |
| 5 | P1, P2 | haiku × 10 | `B_P_persona.md`, 실행 `r0`, 묶음 A~E |
| 6 | 채점 | - | `python -m ui_eval.score r0` → 기준 점수. 빠진 세션이 0이어야 한다 |

**게이트 H** (모두 만족)
```
python tools/path_gate.py --role harness --base main     # "통과"
python -m tests.test_score                               # 종료 코드 0
python -m ui_eval.smoke                                  # 종료 코드 0
python -m ui_eval.fault_inject smoke                     # ui_eval/runs/smoke/faults.json 생성, not_run 비어 있음
python -m ui_eval.make_answers --check                   # "같음" 2줄
python tools/golden_check.py --ui                        # "통과"
```
`python -m ui_eval.audit_roles`는 이 시점에 실패가 정상이다 (아직 역할이 없다). 돌리지 않아도 된다.

**게이트 U** (U0, R1, R2 공통. `<실행>` = r0 / r1 / r2, `<태그>` = 직전에 채택된 상태의 태그)
```
python tools/path_gate.py --role ui --base <태그>         # "통과"
python tools/golden_check.py --ui                        # "통과"
python -m tests.test_app                                 # 종료 코드 0
python -m ui_eval.audit_roles <실행>                      # 종료 코드 0
python -m ui_eval.smoke                                  # 종료 코드 0
python -m ui_eval.fault_inject <실행>                     # not_run 비어 있음
python -m ui_eval.make_answers --check                   # "같음" 2줄
```
게이트가 실패하면: 같은 에이전트 프롬프트에 `게이트 실패: <명령과 출력 마지막 20줄>. 이것만 고쳐라.`를 붙여 **한 번** 다시 돌린다. 그래도 실패하면
B0에서는 멈추고 총괄에 보고, R1·R2에서는 "가드 실패"로 처리한다(아래).

**6에서 빠진 세션이 있으면**: 그 과제만 같은 페르소나로 다시 돌린다 (사용량 한도로 끊겼을 때도 같다. 끝난 세션은 다시 돌리지 않는다).
p1·p2 합쳐 공개 24, 숨김 12 세션이 모두 `answer` 또는 `giveup`으로 끝나야 채점이 유효하다.

## 라운드 R1, R2

`<이전>` = 직전에 채택된 실행(R1에서는 r0. R2에서는 r1이 채택됐으면 r1, 아니면 r0), `<태그>` = `b-<이전>`.

| 단계 | 실행 | 모델 | 지시 / 스크립트 |
|---|---|---|---|
| 1 | U | sonnet | `B_U_improver.md`, 덧붙임: `라운드 R1, 직전 실행 r0, 기준 태그 b-r0.` |
| 2 | 게이트 U | haiku(스크립트만) | 위 "게이트 U" (`<실행>` = r1) |
| 3 | P1, P2 | haiku × 10 | `B_P_persona.md`, 실행 `r1`, 묶음 A~E |
| 4 | 채점·판정 | - | `python -m ui_eval.score r1` → `python -m ui_eval.score --compare <이전> r1` |
| 5 | 채택 또는 되돌림 | - | 아래 |

**채택 규칙** (스크립트가 판정한다. 근거는 `ui_eval/runs/<실행>/verdict.json`)
- 주지표: 공개 과제 성공 수 S (p1 + p2, 최대 24), 공개 놓친 오입력 수 M.
- 개선 인정: S가 2 이상 늘고 M이 늘지 않음 / M이 2 이상 줄고 S가 줄지 않음 / 두 페르소나 모두 나빠진 것 없이 각자 하나 이상 나아짐. 셋 중 하나.
- 숨김 비악화: 숨긴 과제 성공 수가 1 넘게 줄지 않고 놓친 오입력이 1 넘게 늘지 않음.
- 가드: 값 불변(게이트 U의 golden), 관리자 영역 노출 0, 오래된 결과 미표시 0, 오경고율이 높아지지 않음.
- 채택 = 셋 다 만족. 목표 달성 = S 22 이상, M 1 이하, 숨긴 성공 10 이상.

**종료 코드 0 (채택)**: `git tag b-r1` (R2면 `b-r2`).
**종료 코드 1 (되돌림)**: `git branch b/r1-rejected` (기록용) → `git reset --hard <태그>`. 비서만 한다.
**종료 코드 2**: 자료가 빠졌다. 채점·주입·점검 파일이 있는지 보고 다시. 해결되지 않으면 총괄에 보고.
**U가 화면 프레임워크를 바꿨으면** (`ui/testing.py`가 새로 생김): 스크립트 판정이 채택이어도 태그를 달기 전에 총괄 오더로 `ui/testing.py` 확인을 받는다.

**다음으로 갈지** (verdict의 마지막 줄들로 판단)

| R1 결과 | 다음 |
|---|---|
| 채택 + 목표 달성 | 마무리로 |
| 채택 + 목표 미달 | R2 (`<이전>` = r1) |
| 되돌림, 가드 실패 (게이트 U 재시도 실패 포함) | R2 (`<이전>` = r0). U 지시에 `R1은 가드 실패로 되돌려졌다: <이유>.`를 덧붙인다 |
| 되돌림, 가드는 통과했으나 개선 없음 또는 숨김 악화 | 중단 → 마무리로 (r0 상태) |

R2가 끝나면 결과와 상관없이 마무리로 간다. R1·R2 연속 가드 실패면 마무리 전에 총괄에 보고한다.

## 마무리

| 단계 | 실행 | 모델 | 지시 / 스크립트 |
|---|---|---|---|
| 1 | P3 | haiku × 1 | `B_P_persona.md`, 덧붙임: `실행 <최종>, 페르소나 p3, 과제 A01 A02 A03.` (`<최종>` = 마지막으로 채택된 실행) |
| 2 | 채점 | - | `python -m ui_eval.score <최종>` (p3 결과가 따로 묶여 나온다. 채택 판정에는 쓰지 않는다) |
| 3 | 사용자 최종 검토 | 사용자 | 아래 자료 |
| 4 | 병합 | - | 승인 후 메인에서 `git merge --ff-only b/ui` → `python tools/golden_check.py --ui` "통과" |

**사용자에게 올릴 자료**
- 점수 표: r0 → r1 → r2의 S, M, 숨긴 성공 수, 오래된 결과 사용, 해석 오류, 입력 확인 가능, 조작 수, 포착률, 오경고율 (`score.json`, `faults.json`)
- 각 라운드의 판정과 이유 (`verdict.json`), U의 최종 보고(무엇을 왜 고쳤는지), 되돌린 라운드가 있으면 그 이유
- 관리자(P3) 과제 결과와 혼란 보고
- 직접 볼 화면: worktree에서 `python -m streamlit run app.py --server.port 8502`. 관리자 화면을 보려면 worktree의 `.streamlit/secrets.toml`에 `admin_password`를 넣는다 (예시 파일 참고, 커밋되지 않는다).
- 병합 뒤 할 일: 메인의 `.streamlit/secrets.toml`에 실제 관리자 비밀번호, 실데이터 마스터 `data/counterparties.csv` 작성 (둘 다 로컬 전용).

## 중지 조건 (해당하면 다음 단계로 가지 말고 총괄에 보고)

- `golden_check.py --ui`가 "통과"가 아닌데 재시도로도 해결되지 않는다. 기준값을 다시 쓰지 않는다.
- `path_gate` 위반이 재시도 뒤에도 남는다 (특히 `tools/`, `linefee/`, `ui_eval/contract.py`, `ui_eval/tasks_public.yaml`, U가 `ui_eval/`을 고침).
- `make_answers --check`가 "다름" (스냅샷·과제·샘플 마스터가 바뀌었다. 이전 실행과 점수를 비교할 수 없다).
- 에이전트가 메인 체크아웃의 추적 파일을 고쳤다, push했다, 블룸버그를 호출했다, `c:\projects\fun_private\`의 내용을 저장소 안에 옮겼다
  (`git -C c:\projects\fun status --short`, `git grep -n "H0[1-6]" -- . ":!tools/prompts"` 로 확인).
- U의 최종 보고나 커밋에 숨긴 과제의 내용이 보인다.
- 페르소나 에이전트가 드라이버 밖의 파일을 읽었다 (보고나 로그에 과제 파일·코드 내용이 보인다). 그 묶음은 버리고 다시 돌린다.
- 하니스 결함으로 보이는 일 (드라이버 오류로 세션이 진행되지 않음, 채점이 명백히 틀림). H를 다시 띄우지 말고 총괄에 보고한다.
  하니스를 고치면 그 전 실행과 비교가 깨질 수 있어 r0부터 다시 재야 하는지 총괄이 정한다.

## 비용이 큰 단계

- 페르소나 실행: 라운드마다 haiku 10개, 세션 36개 (세션당 드라이버 명령 15~40번). B0·R1·R2 합쳐 30개 + P3 1개.
- H: 파일 6~7개를 새로 쓴다 (sonnet 1회 중 가장 길다). U0가 그다음.
- 사용량 한도로 끊기면 커밋과 끝난 세션은 남는다. 끊긴 에이전트만 `이어서: 이미 한 것은 git log 와 ui_eval/runs 로 확인하고 남은 것만 하라.`를 붙여 다시 띄운다.
