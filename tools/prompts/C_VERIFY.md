# Run C 검증자 (V1 사실 확인, V2 독립 반박)

먼저 `tools/prompts/C_COMMON.md`를 읽는다. 새 발견사항을 찾는 역할이 아니다. 원장은 `tools/ledger.py`로만 고친다.

## V1 — 사실 확인 (sonnet, 섹터당 1명)

맡은 범위: 지시받은 섹터(`reviewer` 값이 `C<n>`)의 원장 항목 전부. `python tools/ledger.py status`, `show L-0xx`로 읽는다.

절차는 `tools/prompts/A1_V_verifier.md`의 "절차"와 "추가 판정"을 그대로 따른다. 기록은
`python tools/ledger.py verify <id> reproduced|refuted|unverifiable <메모>`.

추가 규칙:
- 영향 크기(`expected_vs_actual`의 bp·억)를 직접 다시 계산해 메모에 적는다. 리뷰어 숫자와 다르면 둘 다 적는다.
- `[섹터 밖: Cx]` 항목도 지시받은 섹터 파일에 있으면 같이 확인한다.
- C4 데이터 항목은 원본(`local/data/`, 루트 xlsx)의 해당 행을 직접 열어 본다.

**C8·C9(방법론) 항목**: 의견 자체는 판정하지 않는다. 항목이 기대는 **사실 전제**만 확인한다
(예: "엔진은 DVA를 계산하지 않는다"가 코드에서 사실인가, 인용한 문헌·규정 내용이 맞는가, 영향 추정 계산이 재현되는가).
전제가 맞으면 `reproduced`, 전제가 틀리면 `refuted`, 외부 자료로만 확인되면 `unverifiable`.
`decision-conflict` 항목에도 이 규칙을 쓴다 (A1의 "확정 결정 이견은 unverifiable" 규칙은 C8·C9에 적용하지 않는다).

## V2 — 독립 반박 (opus, 전체 1명)

맡은 범위: V1이 `reproduced`로 판정한 **P1·P2 항목 전부**. P3는 보지 않는다.

질문: "이 발견사항을 고치면 오히려 틀리게 되는 이유가 있는가?" 기본 자세는 반박이다.

각 항목마다:
1. 리뷰어·V1 메모를 읽되, 근거(규정 조항, 문서 줄, 코드)를 **처음부터 다시** 확인한다.
2. 반박 근거를 찾는다:
   - 확정 결정(`docs/DECISIONS.md`)이 그 동작을 의도했다
   - 규정의 다른 조항·특례가 현재 동작을 허용한다
   - "기대값" 쪽 계산이나 해석이 틀렸다
   - 영향이 표준오차·반올림 이하다 (이 경우 심각도 하향 제안)
3. 기록: `python tools/ledger.py challenge <id> upheld|overturned <근거>`
   - `upheld`: 반박에 실패했다. 메모에 시도한 반박과 왜 안 되는지 한 줄
   - `overturned`: 반박에 성공했다. 메모에 반증 (조항·줄·명령 출력). TRIAGE에서 빠진다
   - 심각도 의견은 메모 끝에 "심각도 P3 제안"처럼

**C8·C9 항목(P1·P2, V1 reproduced)**: 반박은 "현행 방법을 가장 강하게 옹호하는 논리"를 세우는 것이다.
DECISIONS의 근거 열, 은행 실무 제약(데이터, 시스템, 감독), 영향 크기를 근거로 현행을 옹호해 본다.
- 옹호가 개선안보다 강하면 `overturned` (메모: 옹호 논리 요약)
- 옹호해도 개선 필요성이 남으면 `upheld` (메모: 옹호 논리와 그래도 남는 이유). 사용자는 양쪽 논리를 함께 본다

## 최종 보고 (에이전트 응답)

- V1: 판정 수 (reproduced / refuted / unverifiable), refuted id와 반증 한 줄씩
- V2: upheld / overturned 수, overturned id와 반증 한 줄씩, 심각도 조정 제안 목록
