# Run A1 실행 순서 (비서용)

에이전트 3개. 모두 메인 체크아웃에서 읽기 전용으로 돈다 (worktree 불필요).

| 단계 | 실행 | 모델 | 에이전트에 줄 짧은 지시 |
|---|---|---|---|
| 1 (병렬) | R1 | opus | `c:\projects\fun 에서 tools/prompts/A1_R1_methodology.md 를 읽고 그대로 수행하라.` |
| 1 (병렬) | R2 | sonnet | `c:\projects\fun 에서 tools/prompts/A1_R2_numerics.md 를 읽고 그대로 수행하라.` |
| 2 (스크립트) | `python tools/ledger.py merge` | - | 형식 오류(종료 코드 1)가 나오면 해당 리뷰어에게 그 목록을 주고 고치게 한 뒤 다시 merge |
| 3 | V | sonnet | `c:\projects\fun 에서 tools/prompts/A1_V_verifier.md 를 읽고 그대로 수행하라.` |
| 4 (스크립트) | `python tools/ledger.py render` 후 `python tools/path_gate.py --role a1-run` | - | 게이트 위반(reviews/ 밖 변경)이 있으면 중지하고 총괄에 보고 |

끝나면: `python tools/ledger.py status` 결과와 `reviews/TRIAGE.md`를 사용자에게 전달한다 (게이트 1).
사용자 결정은 `python tools/ledger.py decide L-00x accept|reject|defer [메모]`로 기록하고 `render`를 다시 돌린다.

중지 조건: 에이전트가 저장소 파일을 고쳤다 / 블룸버그를 호출했다 / `git status`에 reviews/ 밖 변경이 있다.
