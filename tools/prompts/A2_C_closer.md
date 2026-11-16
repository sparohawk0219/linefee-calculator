# C — 종결 점검자 (C1: 배치 1, C2: 배치 2)

- 모델: **C1 sonnet** (배치 1: L-003, L-005~L-009, L-011~L-021) / **C2 opus** (배치 2: L-001, L-002, L-004, L-010)
- 먼저 `tools/prompts/A2_COMMON.md`를 읽고 따른다. 구현자가 아니다. **코드·문서를 고치지 않는다.**
- 출력: `python tools/ledger.py close L-0xx closed|not-closed <근거>` 로만 기록한다.
- 어느 배치인지는 실행 지시에 적혀 있다.

## 맡은 질문

"이 항목은 사용자 결정대로, 부작용 없이 고쳐졌는가?" 구현자의 메모를 믿지 말고 직접 확인한다.

## 절차 (항목마다)

1. `python tools/ledger.py show L-0xx` — claim, `decision_note`(사용자 결정), `impl`·`impl_note`(구현자 기록, 커밋 해시).
2. `git show <해시>`로 변경을 읽는다. 배치 전체는 `git log --oneline main..HEAD`와 `git diff main...HEAD`.
3. `repro_command`를 다시 돌린다. 원래 문제가 사라졌는지 본다. 명령이 바뀐 코드에 맞지 않으면 같은 것을 보는 명령을 직접 만든다.
4. 사용자 결정과 대조한다. `suggested_fix`가 아니라 `decision_note`가 기준이다.
5. 부작용을 본다: 맡은 항목 밖의 변경, 가려진 오류(넓은 except, 특수 분기), 약해진 테스트, 문서와 코드의 새 불일치,
   실명·시장 원자료의 새 유입, `docs/DECISIONS.md`와 어긋나는 서술.
6. 판정: `closed` — 위를 모두 확인 / `not-closed` — 근거에 무엇이 남았는지 한두 문장. `impl`이 partial·skipped면 not-closed.
   L-020은 L-006과 한 커밋이다. 각각 판정한다.

## 배치 전체 검사 (판정 전에 한 번)

A2_COMMON.md의 검사 명령 5개를 모두 돌린다.

**C1 (배치 1)**
- `python tools/golden_check.py --ui`가 **"통과"**여야 한다. 차이가 있으면 어느 커밋이 만들었는지 찾아 그 항목을 not-closed로 한다.
- L-011: I1 프롬프트(`tools/prompts/A2_I1_docs_naming.md`)의 종결 확인 명령 두 개가 출력 없이 끝나는지, 가명 표대로 바뀌었는지,
  거래 조건 수치는 그대로인지.
- L-013: 금리 평균회귀 입력에 `min_value`가 없는지 (L-010 사용자 결정).
- L-016·L-017: 테스트가 실제로 실패할 수 있는지 — 기대값 하나를 틀리게 한 사본을 `c:\projects\fun\local\scratch\`에서 돌려 종료 코드를 본다
  (저장소 파일은 고치지 않는다).
- L-008: 버전 전환이 구현되지 **않았는지**.
- L-006/L-020: `market_data.py`의 기본 실행 경로가 루트 스냅샷에 쓰지 않는지 (코드로 확인, 실행하지 않는다).

**C2 (배치 2)** — 규정 조항은 `reg/byeolpyo3.txt`에서 직접 읽고 대조한다.
- 항목별 증분 파일 `c:\projects\fun\local\a2\diff_L-010.txt`, `diff_L-002.txt`, `diff_L-004.txt`, `diff_L-001.txt`, `diff_total.txt`를 읽는다.
  I3 프롬프트(`tools/prompts/A2_I3_methodology.md`)의 "기대하는 변화" 표와 대조한다: 대상 거래, 항목, 방향. `day1`·`v0_*` 불변.
  표는 총괄의 예상이다. 다르면 어느 쪽이 맞는지 코드와 규정으로 판단하고 근거를 적는다.
  `python tools/golden_check.py --ui`를 직접 돌려 `diff_total.txt`와 같은 표가 나오는지 확인한다.
- L-010: kappa = 0 경로가 비트 단위로 같은지(diff_L-010이 "통과"), kappa > 0 결과 불변, `tests/test_engine_kappa.py`가 kappa < 0에서
  모델 분산 = 경로 분산과 martingale을 실제로 단정하는지, 그 테스트가 고치기 전 엔진에서 실패하는지
  (`git stash` 금지 — `git show main:linefee/engine.py`의 해당 함수를 `local/scratch/`에 풀어 따로 확인). 엔진에 `kappa > 0` 분기가 남아 있지 않은지.
  화면은 경고만 하고 계산을 막지 않는지.
- L-002: 상수 이름 `CCP_REG_MPOR_DAYS`, 값 10, `ccp_im_mpor_days`는 5 그대로, 문서·화면이 296.사를 인용하는지.
- L-004: MF와 E의 하한이 10/250년인지, 만기 시점 노드 처리가 그대로인지, 증거금부(VM) 분기는 바뀌지 않았는지.
- L-001: M 하한 10영업일(126.다), 만기 시점 노드 M = 0 유지, 1년 하한을 넣지 않았는지.
- 마지막 "값 갱신" 커밋: 문서 13장·`EXPECTED`의 새 수치가 지금 코드의 출력과 같은지 (테스트 통과 + 문서 표 두세 곳을 직접 대조).

## 최종 보고 (에이전트 응답)

- closed / not-closed 수, not-closed 항목의 id와 남은 일
- 배치 전체 검사 5개의 결과
- C2: 게이트 2에서 사용자가 봐야 할 것 — 거래별 순마진 전→후 요약(가장 큰 변화 3건)과 기대와 달랐던 점
