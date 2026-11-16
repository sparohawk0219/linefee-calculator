# I1 — 구현자 1: 가명화·구버전 표시·문서 정합

- 모델: **sonnet**
- 먼저 `tools/prompts/A2_COMMON.md`, `tools/prompts/A2_I_RULES.md`를 읽고 따른다.
- 맡은 항목 (이 순서로): **L-011, L-006 (+L-020 한 커밋), L-003, L-005, L-007, L-008, L-019**

## 값 조건

이 배치는 계산 결과를 바꾸지 않는다. **커밋마다 `python tools/golden_check.py --ui`가 "통과"여야 한다.**
차이가 나오면 그 변경이 잘못된 것이다. 되돌리고 원인을 찾는다.

## 항목별 지시

**L-011 가명화** — 추적되는 모든 파일에서 실명을 가명으로 바꾼다. 대응표: `c:\projects\fun\local\a2\pseudonyms.md` (로컬 전용).
- 대상: 문자열, 주석, docstring, 문서, 예시 거래 이름(`app.py`의 `PRESETS`와 `load_preset` 분기),
  **식별자**(실명이 들어간 변수·함수·필드·딕셔너리 키·명령행 인자. 고객 쪽은 `client_a`, 딜러 쪽은 `dealer_b`를 넣어 바꾼다.
  예: `rw_<딜러 실명>` → `rw_dealer_b`), DESIGN.md의 예시(상대방 이름, 상계군 이름).
- 예시 거래 이름은 `엔/원 30년 CCS (공공기관 A 중개)`로 한다. 예시 목록의 순서는 바꾸지 않는다 (golden 게이트가 순서로 거래를 맞춘다).
- 거래 조건(쿠폰, 명목, upfront 수치)은 그대로 둔다. 이름만 바꾼다.
- 범위 밖: `reg/`, 루트의 규정 PDF, git 이력. (`tools/`는 이미 처리되어 있다. 고치지 않는다.)
- 식별자를 바꾸기 전에 `python -m tests.regression_jpykrw`와 `python main.py`의 출력을 `c:\projects\fun\local\scratch\`에 저장하고,
  바꾼 뒤 출력의 숫자가 모두 같은지 비교한다 (이름과 소요 시간만 달라야 한다).
- 종결 확인 명령 (둘 다 출력이 없어야 한다. 패턴 파일은 로컬 전용):
  - `git grep -n -i -f c:/projects/fun/local/a2/names_substring.txt -- . ":!reg" ":!*.pdf"`
  - `git grep -n -i -w -f c:/projects/fun/local/a2/names_word.txt -- . ":!reg" ":!*.pdf"`

**L-006 + L-020 구버전 표시와 스냅샷 보호** (한 커밋)
- `xva_model.py`, `assumptions.py`, `main.py`, `market_data.py`의 docstring 첫머리에
  "구버전(엔/원 검토 당시), 회귀 비교 전용. 현재 방법은 `linefee/`, `docs/METHODOLOGY_v1.md`. KVA 조달금리 등이 확정 결정(DECISIONS M5)과 다르다"는 뜻을 적는다.
  `simm.py`는 `linefee/regcap.py`가 지금도 쓰는 모듈이므로 구버전으로 표시하지 않는다 (누가 쓰는지만 적는다).
- `assumptions.py`의 '확정' 주석이 현재 확정 방법으로 읽히지 않게 한다.
- `market_data.py`를 실행해도 루트 `market_snapshot.json`(회귀 기준 스냅샷)을 기본으로 덮어쓰지 않게 한다
  (저장 경로를 분리하거나 명시적 인자가 있을 때만 저장). `linefee.market.load_legacy`와 회귀 테스트는 지금처럼 루트 스냅샷을 읽어야 한다.
- 블룸버그를 호출해 확인하지 않는다. 저장 경로 로직만 코드로 확인한다.

**L-003** — `DESIGN.md` 2.3·3.1·4.4와 `METHODOLOGY_v1.md` 12장 해당 행을 DECISIONS M10(wrong-way risk는 라인피에 넣지 않고
한계 상황 스트레스로 감당 가능성만 점검)·M11(등급 고정, 전이행렬 미적용) 문구에 맞춘다. 스트레스 점검 절차·코드를 새로 만들지 않는다.

**L-005** — `DESIGN.md` 3.4의 r(t) 정의를 M5 문구(CD(원화 IRS) 커브 선도 + alpha, 기본 10bp)로.

**L-007** — `app.py` 신용 위험가중치 도움말을 별표3 32. 구분대로 (결손보전 기관 0%(32.가), 그 외 공공기관 20~50%(32.나·다)).
`reg/byeolpyo3.txt`에서 조항을 직접 확인하고 쓴다.

**L-008** — `METHODOLOGY_v1.md` 12장 근사 목록에 "SIMM 버전: 만기 전 구간 v2.8+2512 하나로 계산, 시행일 기준 버전 전환 미반영" 행을 추가한다.
**버전 전환을 구현하지 않는다** (사용자 결정). `DESIGN.md` 5.5가 구현된 것처럼 읽히면 v1 미반영으로 맞춘다.

**L-019** — `METHODOLOGY_v1.md` 4장에 1년 미만 만기의 변동성 처리(1년 ATM 변동성을 평탄 적용)를 적는다. 코드는 바꾸지 않는다.

## I2·I3와 겹치는 곳

`METHODOLOGY_v1.md` 7장, 12장의 CCP MPOR 행, 13장, 14장의 수치와 규정 서술은 I3가 고친다. L-003·L-008이 가리키는 행만 건드린다
(가명화는 문서 전체에 적용한다).
