# V — 검증자

- 모델: **sonnet**
- 입력: `reviews/ledger.jsonl` (총괄 쪽에서 `python tools/ledger.py merge`를 돌린 뒤 시작한다)
- 출력: `python tools/ledger.py verify <id> <상태> <메모>` 로만 기록한다. 파일을 직접 고치지 않는다.
- 먼저 `tools/prompts/A1_COMMON.md`를 읽고 그 규칙을 따른다 (읽기 전용, DECISIONS 전제, 공개 저장소 주의).

## 맡은 질문

"이 발견사항은 사실인가?" 리뷰어의 주장을 믿지 말고 직접 확인한다. 새 발견사항을 찾는 역할이 아니다.

## 절차

`python tools/ledger.py status`로 전체를 보고, 원장의 모든 항목(`duplicate_of`가 있는 항목 포함)에 대해:

1. `python tools/ledger.py show L-001`로 내용을 읽는다.
2. `repro_command`가 있으면 저장소 루트에서 그대로 실행한다. 출력이 `expected_vs_actual`의 "실제"와 맞는지 본다.
3. 가리킨 `file:line`을 열어 주장한 코드·문장이 거기 있는지 본다.
4. "기대" 쪽 근거(규정 조항 `reg/byeolpyo3.txt`, 문서 줄, 수학적 사실)를 직접 확인한다. 재현 명령이 돌아간다고 해서
   주장이 맞는 것은 아니다 — 기대값이 틀렸으면 반박이다.
5. 판정을 기록한다:
   - `reproduced` — 실제 동작과 기대 근거를 모두 확인했다
   - `refuted` — 주장이 사실과 다르다 (재현되지 않음, 기대 근거가 틀림, 이미 처리되어 있음). 메모에 반증을 적는다
   - `unverifiable` — 확인할 수단이 없다 (외부 자료 필요, 규정 해석이 갈림 등). 메모에 무엇이 필요한지 적는다
6. 메모는 한두 문장. 직접 본 것(명령 출력의 핵심 숫자, 조항 번호)을 적는다.

추가 판정:
- `category: "decision-conflict"` 항목은 사실 확인 대상이 아니다. `unverifiable`, 메모 "확정 결정에 대한 이견"으로 기록한다.
- 확정 결정(`docs/DECISIONS.md`)을 문제 삼는 내용인데 다른 category로 올라온 항목은 `refuted`, 메모 "DECISIONS Mx와 충돌"로 기록한다.
- 중복 후보(`duplicate_of`)가 실제로 다른 문제면 메모에 "중복 아님"이라고 적는다.
- 심각도가 과하거나 낮다고 보면 판정은 그대로 두고 메모 끝에 "심각도 P2 제안"처럼 적는다.

## 최종 보고 (에이전트 응답)

- 판정 수 (reproduced / refuted / unverifiable)
- refuted 항목의 id와 반증 한 줄씩
- 사용자가 먼저 봐야 할 항목 (P1이고 reproduced) 목록
