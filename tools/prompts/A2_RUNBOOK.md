# Run A2 실행 순서 (비서용)

에이전트 5개, 순차 실행. 모두 worktree `c:\projects\fun\.worktrees\a2` (브랜치 `a2/fixes`)에서 일한다.
스크립트 단계는 비서가 worktree 루트에서 돌린다. push는 사용자 지시가 있을 때만 한다.

배치 1 = 값이 바뀌지 않는 16건, 배치 2 = 값이 바뀌는 방법론·엔진 4건.

## 배치 1 (값 불변)

| 단계 | 실행 | 모델 | 에이전트에 줄 지시 / 스크립트 |
|---|---|---|---|
| 1 | I1 | sonnet | `c:\projects\fun\.worktrees\a2 에서 tools/prompts/A2_I1_docs_naming.md 를 읽고 그대로 수행하라.` |
| 2 | 게이트 | - | `python tools/golden_check.py --ui` → "통과" / `python tools/path_gate.py --role implementer --base main` → "통과" |
| 3 | I2 | sonnet | `c:\projects\fun\.worktrees\a2 에서 tools/prompts/A2_I2_code_tests.md 를 읽고 그대로 수행하라.` |
| 4 | 게이트 | - | 2와 같음 + `python -m tests.test_curves`, `python -m tests.verify_methodology`, `python -m tests.regression_jpykrw` 종료 코드 0 |
| 5 | C1 | sonnet | `c:\projects\fun\.worktrees\a2 에서 tools/prompts/A2_C_closer.md 를 읽고 배치 1(C1) 점검을 수행하라.` |
| 6 | 병합 | - | `python tools/ledger.py status`에서 배치 1의 16건이 모두 closed이면, 메인 체크아웃에서 `git merge --ff-only a2/fixes` |

- 6에서 not-closed가 있으면: 그 항목만 같은 구현자 프롬프트에 "L-0xx만 다시. `ledger.py show`의 closure_note를 읽고 고쳐라"를 붙여 다시 돌리고,
  C1도 그 항목만 다시 본다. 한 항목에 재시도 1회. 그래도 남으면 그 항목은 남긴 채 사용자에게 보고하고 나머지를 병합한다.
- 배치 1은 값이 바뀌지 않으므로 사용자 게이트 없이 병합한다. 병합 후 메인에서 `python tools/golden_check.py --ui`가 "통과"인지 한 번 더 본다.

## 배치 2 (값 변화, 사용자 게이트 2)

| 단계 | 실행 | 모델 | 에이전트에 줄 지시 / 스크립트 |
|---|---|---|---|
| 7 | I3 | sonnet | `c:\projects\fun\.worktrees\a2 에서 tools/prompts/A2_I3_methodology.md 를 읽고 그대로 수행하라.` |
| 8 | 게이트 | - | `python tools/path_gate.py --role implementer --base main` → "통과" / 테스트 3개 + `python -m tests.test_engine_kappa` 종료 코드 0 / `local/a2/`에 `diff_L-010.txt`(마지막 줄 "통과"), `diff_L-002.txt`, `diff_L-004.txt`, `diff_L-001.txt`, `diff_total.txt`가 있는지 |
| 9 | C2 | opus | `c:\projects\fun\.worktrees\a2 에서 tools/prompts/A2_C_closer.md 를 읽고 배치 2(C2) 점검을 수행하라.` |
| 10 | 게이트 2 | 사용자 | 아래 자료를 사용자에게 전달하고 승인을 받는다 |
| 11 | 병합·기준값 | - | 승인 후: 메인에서 `git merge --ff-only a2/fixes` → `python tools/golden_check.py --write --ui` → `python tools/golden_check.py --ui` "통과" 확인 |

**게이트 2에 올릴 자료**
- `local/a2/diff_total.txt`의 "거래별 요약" 표 (거래 13건의 첫날 이익·비용·순마진·손익분기 전→후)
- 항목별 증분: `diff_L-002.txt`, `diff_L-004.txt`, `diff_L-001.txt` 각각의 요약 표 (어느 결정이 얼마를 움직였는지)
- C2의 최종 보고 (closed 수, 기대와 달랐던 점)
- `python tools/ledger.py status`

사용자가 일부 항목의 값 변화를 승인하지 않으면: 병합하지 않고 총괄에 넘긴다 (항목별 커밋이라 해당 커밋만 되돌릴 수 있다).

## 중지 조건 (해당하면 다음 단계로 가지 말고 총괄에 보고)

- 배치 1의 어느 게이트에서든 golden 차이가 나온다.
- `path_gate` 위반 (특히 `docs/DECISIONS.md`, `tools/`, `.gitignore` 변경).
- 배치 2에서 `day1`, `v0_client`, `v0_hedge`가 바뀌었거나, `diff_L-010.txt`가 "통과"가 아니다.
- 에이전트가 메인 체크아웃의 추적 파일을 고쳤다 (`git -C c:\projects\fun status --short`에 변경이 보인다), push했다, 블룸버그를 호출했다,
  `golden_check.py --write`를 돌렸다 (`local/golden_baseline.json` 수정 시각으로 확인. 준비 시점 값은 총괄 보고에 있다).
- `--ff-only` 병합이 거절된다 (메인이 그사이 바뀜) — 임의로 rebase하지 말고 보고.

## 끝난 뒤

- `python tools/ledger.py status`: accept 21건 모두 closed.
- worktree 정리는 사용자 승인 후: `git worktree remove .worktrees/a2`, `git branch -d a2/fixes`.
- Track B(B0)는 새 기준값 위에서 시작한다.
