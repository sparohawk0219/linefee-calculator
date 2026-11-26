# Run C 실행 순서 (비서용) — 섹터별 최종 점검

에이전트 20개 (섹터 리뷰어 9: 구현 점검 C1~C7 + 방법론 점검 C8·C9, V1 9, V2 1, K 1). 모두 메인 체크아웃에서 읽기 전용으로 돈다 (worktree 불필요).
프롬프트: `C_COMMON.md`(공통), `C_SECTORS.md`(섹터), `C_VERIFY.md`(V1·V2), `C_K_critic.md`(K).
발견사항은 Track A와 같은 원장(`reviews/ledger.jsonl`)에 L-022부터 이어 붙는다.

## 0. 사전 확인 (비서)

- `git status`가 깨끗하다 (리뷰 중 코드가 바뀌면 재현이 깨진다). `git tag -f c-base` 로 기준을 찍는다.
- 시장 스냅샷이 있다 (`linefee/market_snapshot.json`). 실데이터 마스터 `data/counterparties.csv`가 있다.
- `python tools/golden_check.py` 비교 결과를 기록해 둔다 (다르면 시작하지 않고 사용자에게 묻는다).
- 대시보드가 떠 있어도 된다 (리뷰어는 AppTest를 쓴다).

## 1~6. 단계

| 단계 | 실행 | 모델 | 에이전트에 줄 짧은 지시 |
|---|---|---|---|
| 1 (병렬 9) | C1 ~ C9 | C2·C3·C4·C6·C8·C9 opus, C1·C5·C7 sonnet | `c:\projects\fun 에서 tools/prompts/C_COMMON.md 를 읽고, tools/prompts/C_SECTORS.md 의 C<n> 섹터를 수행하라.` |
| 2 (스크립트) | `python tools/ledger.py merge` | - | 형식 오류가 나오면 해당 리뷰어에게 목록을 주고 고치게 한 뒤 다시 merge. 각 리뷰어 최종 보고를 `reviews/C_REPORTS.md`에 섹터별로 붙여 둔다 |
| 3 (병렬 ≤9) | V1 × 섹터 (발견사항이 있는 섹터만) | sonnet | `c:\projects\fun 에서 tools/prompts/C_VERIFY.md 를 읽고 V1 역할로 섹터 C<n> 항목을 검증하라.` |
| 4 | V2 | opus | `c:\projects\fun 에서 tools/prompts/C_VERIFY.md 를 읽고 V2 역할을 수행하라.` |
| 5 | K | opus | `c:\projects\fun 에서 tools/prompts/C_K_critic.md 를 읽고 그대로 수행하라.` |
| 6 (스크립트) | `python tools/ledger.py render` 후 `python tools/path_gate.py --role c-run --base c-base` | - | 게이트 위반(reviews/ 밖 변경)이 있으면 중지하고 총괄에 보고 |

**2차 점검 (최대 1회)**: K가 "2차 점검 필요: 예"라고 하면, `C_COVERAGE.md`의 우선순위 상 항목만 해당 섹터 리뷰어에게
"C_COVERAGE.md의 상 항목 중 C<n>에 맡긴 것만 수행하라. id는 기존 번호 다음부터" 로 다시 맡기고, 2~4단계를 새 항목에만 돌린다.
2차 뒤에는 K를 다시 돌리지 않는다. 남은 공백은 그대로 사용자에게 보고한다.

## 7. 사용자 보고 (게이트 1)

- `python tools/ledger.py status`, `reviews/TRIAGE.md`, `reviews/C_COVERAGE.md` 요약
- P1이고 V2 upheld인 항목을 맨 위에
- 방법론 개선안(C8·C9)은 결함 목록과 **따로** 보고한다: 주제, 현행, 개선안, 라인피 영향 방향·크기, V2 옹호 논리, 권고
- 남은 공백(2차 뒤에도 상)과 확인하지 못한 영역

사용자 결정은 `python tools/ledger.py decide L-0xx accept|reject|defer [메모]`. accept 항목의 구현과 종결 확인은
Track A의 `A2_RUNBOOK.md`를 그대로 쓴다 (I1~I3 구현, C 종결 확인). 방법론에 닿는 항목은 항목별 개별 승인.

## 중지 조건

- 에이전트가 저장소 파일을 고쳤다 / 블룸버그를 호출했다 / `build_master.py`를 실행해 `data/counterparties.csv`가 바뀌었다
- `git status`에 reviews/·local/scratch/ 밖 변경이 있다
- 실데이터(실명·등급·한도)가 저장소 추적 파일이나 외부 요청에 나갔다

## 비용 감

opus 8개(C2·C3·C4·C6·C8·C9, V2, K), sonnet 12개(C1·C5·C7, V1 최대 9). 2차 점검 시 해당 섹터만 추가. 한 섹터 리뷰어가 가장 오래 걸리며 전체는 섹터 병렬로 그 시간에 맞춰진다.
