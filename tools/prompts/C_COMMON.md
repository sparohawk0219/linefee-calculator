# Run C 공통 규칙 (최종 점검: 섹터 리뷰어 C1~C7, 검증 V1·V2, 누락 점검 K 모두 적용)

이 저장소는 은행 파생상품 데스크의 라인피(XVA) 계산기다 (Python + QuantLib + Streamlit).
Run C는 실사용 전 최종 점검이다. 시스템을 섹터로 나눠 섹터마다 리뷰어 한 명이 깊게 보고,
검증자가 발견사항을 하나씩 재현하고, 누락 점검자가 빈 곳을 찾는다.

## 먼저 읽을 것

1. `docs/DECISIONS.md`: 사용자가 확정한 결정(M1~M21, O1~O11). **전제이며 다시 묻지 않는다.**
   - 코드·데이터가 결정을 구현하지 않으면 정상 발견사항이다.
   - 결정 자체에 대한 이견은 `category: "decision-conflict"`로만 적는다.
   - M20(엔/원 현물 호가 삼각 불일치로 `test_curves` JPY 항목이 실패하는 날)은 발견사항이 아니다.
   - **예외: 방법론 섹터 C8·C9**는 확정 결정 자체를 평가한다 (`C_SECTORS.md`의 "C8·C9 특칙"). C1~C7은 이 예외를 쓰지 않는다.
2. `docs/METHODOLOGY_v1.md`, `DESIGN.md`: 방법론과 설계.
3. `tools/prompts/C_SECTORS.md`에서 **자기 섹터 절만** 읽는다. 다른 섹터 절은 경계를 알기 위해 제목만 본다.

## 읽기 전용

- 저장소의 어떤 파일도 고치지 않는다. 자기 역할의 출력만 쓴다.
- git 명령은 읽기만 한다 (`status`, `log`, `diff`, `show`, `grep`). commit, checkout, stash, reset, push 금지.
- 명령 실행은 된다: 테스트, 짧은 재현 스크립트. 임시 파일은 `local/scratch/C<섹터>/`에만 만든다.
- 블룸버그 호출 금지 (`python -m linefee.market`, `market_data.py`, `bbg.py`, 화면의 "블룸버그에서 새로 받기"). 스냅샷을 덮어쓴다.
- `python tools/golden_check.py --write` 금지 (비교 실행은 된다). `local/scratch/build_master.py`를 실행하지 않는다 (`data/counterparties.csv`를 다시 쓴다). 읽고, 결과 파일과 대조하는 것은 된다.
- `.env`, `.streamlit/secrets.toml`은 열지 않는다. 화면 테스트가 secrets 때문에 실패하면 그 사실만 적는다.
- 다른 에이전트를 띄우지 않는다.

## 실데이터

- `data/counterparties.csv`, `local/data/`, 루트의 `*.xlsx`·`*.csv`는 실제 은행 데이터다 (gitignore, 로컬 전용).
- 읽어도 된다. 발견사항(`reviews/`, 로컬 전용)에는 실명을 적어도 된다.
- 실명·등급·한도를 저장소 파일, 커밋 메시지, 외부 서비스(웹 검색 포함)로 내보내지 않는다.
- 웹 검색은 C8·C9만 공개 문헌용으로 쓴다. 검색어에 실데이터와 은행 이름을 넣지 않는다.
- 루트 xlsx 원본은 보안 프로그램이 지울 수 있다. 없으면 `local/data/`의 최소 추출본을 쓴다.

## 출력: 발견사항 (섹터 리뷰어)

`reviews/findings_C<n>.jsonl` (예: `findings_C3.jsonl`), id는 `C3-001`부터. 형식은 `tools/prompts/A1_COMMON.md`의
"발견사항 형식" 표와 같다. category에 `data`(상대방 마스터·변환 규칙), `ux`(화면 동작·권한)가 추가되었다.

심각도 기준 (Run C):
- `P1`: 실거래 라인피 숫자가 틀린다, 규정 위반, 권한·실데이터 유출
- `P2`: 특정 입력·상대방·통화 조합에서 틀린다, 문서·결정과 코드·데이터가 다르다
- `P3`: 사용자가 오해하거나 유지보수 때 실수할 근거가 있는 품질 문제

## 품질 기준

- 직접 확인한 것만 낸다. 재현 명령을 실제로 돌려 본 뒤 결과를 `expected_vs_actual`에 적는다.
- 재현 명령이 없는 항목(규정 해석, 문서 서술)은 근거를 조항·줄 번호까지 적는다.
- 영향 크기를 적는다. 가능하면 "라인피 x bp / y억 차이"처럼 숫자로. 영향이 0인 순수 형식 문제는 P3 이하.
- 개수를 채우지 않는다. 취향(이름, 스타일, 리팩터링)은 내지 않는다.
- 이미 닫힌 Track A 항목(`python tools/ledger.py status`, `reviews/TRIAGE.md`)을 다시 내지 않는다. 재발했으면 "재발: L-0xx"로 적는다.
- 섹터 밖 문제를 보면 내되, claim 앞에 `[섹터 밖: Cx]`를 붙이고 깊이 파지 않는다.
- 제출 전 유효한 JSONL인지 확인한다:
  `python -c "import json,sys;[json.loads(l) for l in open(sys.argv[1],encoding='utf-8') if l.strip()];print('ok')" reviews/findings_C3.jsonl`

## 최종 보고 (에이전트 응답, 섹터 리뷰어)

- 발견사항 수 (심각도별), 가장 중요한 3건의 id와 한 줄 요약
- **점검표**: 자기 섹터 절의 "볼 것" 각 번호마다 `확인·문제없음 / 발견 Cx-0yy / 확인 못 함(이유)` 한 줄
- 돌린 명령과 테스트 결과
