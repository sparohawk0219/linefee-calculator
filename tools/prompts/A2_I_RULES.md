# 구현자 공통 절차 (I1, I2, I3)

먼저 `tools/prompts/A2_COMMON.md`를 읽는다.

## 항목마다

1. `python tools/ledger.py show L-0xx`로 내용과 사용자 결정(`decision_note`)을 읽는다.
2. `repro_command`를 돌려 고치기 전 상태를 확인한다.
3. 고친다. 원인을 고친다 — 증상을 가리는 예외 처리, 테스트를 통과시키려는 특수 분기를 넣지 않는다.
4. 검사 명령을 돌린다 (A2_COMMON.md). 역할 프롬프트의 "값 조건"을 만족해야 한다.
5. `repro_command`를 다시 돌려 문제가 사라졌는지 본다.
6. **항목 하나에 커밋 하나.** 제목은 `A2 L-0xx: <한 일>`. 같이 처리하라고 한 항목만 한 커밋에 묶는다.
   `git add`는 고친 파일을 이름으로 지정한다 (`git add -A`, `git add .` 금지).
7. 기록: `python tools/ledger.py impl L-0xx done <커밋 해시 7자리> <한 일 한 문장. 종결 점검자가 돌릴 확인 명령이 있으면 함께>`
   - 일부만 했으면 `partial`, 못 했으면 `skipped`와 이유. 추측으로 메우지 않는다.

## 코드 규칙

- 기존 코드의 문체를 따른다 (한국어 주석·docstring, 줄 길이 약 120자).
- 문서(`docs/METHODOLOGY_v1.md`, `DESIGN.md`)를 고칠 때: 규정 근거는 조항 번호로 적고, 코드와 문서를 같은 커밋에서 맞춘다.
- 새 테스트는 `tests/`에 두고 `python -m tests.<이름>`으로 실행되며 실패 시 종료 코드가 0이 아니어야 한다 (`tests/test_curves.py` 방식).

## 최종 보고 (에이전트 응답)

- 항목별: 상태(done/partial/skipped), 커밋 해시, 바꾼 파일
- 검사 명령의 마지막 결과 (golden, 테스트 3개, path_gate)
- 맡은 범위 밖에서 발견한 문제 (고치지 않은 것)
