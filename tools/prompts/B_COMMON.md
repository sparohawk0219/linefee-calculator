# 트랙 B 공통 규칙 (H, U0, U, P 모두 적용)

이 저장소는 은행 파생상품 데스크의 라인피(XVA) 계산기다. 트랙 B는 화면을 실사용자(본부 세일즈)가 틀리지 않고 쓰게 고치는 작업이다.
실사용자의 일은 "거래를 넣고 결과를 읽는 것" 하나다. 계산(엔진 `linefee/`)은 건드리지 않는다.

## 작업 위치

- 작업 디렉터리: `c:\projects\fun\.worktrees\b` (브랜치 `b/ui`). 메인 체크아웃 `c:\projects\fun`의 추적 파일은 고치지 않는다.
- 시장 스냅샷은 worktree에 없으면 `python tools/golden_check.py`가 메인에서 복사한다 (gitignore 대상).
- 임시 파일은 `c:\projects\fun\local\scratch\`에만 만든다.
- 저장소 밖 `c:\projects\fun_private\`: 숨긴 과제, 정답, 숨긴 과제의 행동 로그. **U0와 U는 이 폴더를 열지 않는다. P는 드라이버 명령으로만 접근한다.**

## 먼저 읽을 것

1. `docs/DECISIONS.md` — 확정 결정. 전제이며 다시 묻지 않는다. 고치지 않는다. 트랙 B에 닿는 것: O4~O8, O10, O11.
2. 이 파일, 그리고 자기 역할의 프롬프트 파일.

## 역할과 편집 권한 (`python tools/path_gate.py --list`)

| 역할 | 하는 일 | 고칠 수 있는 파일 |
|---|---|---|
| H (harness) | 평가 하니스 | `ui_eval/**` (아래 총괄 소유 3개 제외), `tests/test_score.py` |
| U0, U (ui) | 화면 | `app.py`, `ui/**`, `.streamlit/secrets.toml.example`, `tests/test_app.py`, `requirements.txt`, `README.md` |
| P (persona) | 화면을 써 보고 답을 낸다 | 없음 (로그는 드라이버가 쓴다) |

총괄 소유 (누구도 고치지 않는다): `tools/**`, `docs/DECISIONS.md`, `.gitignore`, `ui_eval/contract.py`, `ui_eval/make_answers.py`,
`ui_eval/tasks_public.yaml`, `data/counterparties.sample.csv`. 고쳐야 한다고 판단하면 고치지 말고 최종 보고에 적는다.

## 금지

- push, 메인 브랜치 변경, 브랜치 전환, rebase, reset --hard, stash, 이력 재작성.
- 엔진(`linefee/`, 루트의 `simm.py` 등 계산 코드) 수정. 화면에서 엔진 결과를 가공해 다른 값을 보여 주는 것도 금지.
- `python tools/golden_check.py --write`, `python -m ui_eval.make_answers`(`--check` 없이) 실행.
- 블룸버그 호출 (`python -m linefee.market`, `python market_data.py`, `bbg.py`, 화면의 "블룸버그에서 새로 받기").
- 다른 에이전트를 띄우는 것.
- 실명. 상대방 이름은 가명만 쓴다: 공공기관 A, 딜러 B, 기업 C, 딜러 D, 기업 E, 공공기관 F, 당행. 커밋 메시지도 같다.
- 시장 스냅샷의 원자료(호가, 커브 포인트, 환율 수준)와 과제 정답 값을 코드·문서·테스트·커밋 메시지에 적는 것 (공개 저장소).

## 화면 계약

화면(U0, U)이 지키고, 하니스(H)와 값 불변 게이트(`tools/golden_check.py --ui`)가 기대하는 것이다.
화면을 어떻게 바꾸든 아래는 그대로여야 한다. 지킬 수 없으면 바꾸지 말고 최종 보고에 적는다.

1. **진입점.** `app.py`를 Streamlit AppTest(`streamlit.testing.v1.AppTest.from_file`)로 실행할 수 있다. 로그인하지 않은 상태가 일반 사용자 화면이다.
   비밀값 파일이 없어도 일반 사용자 화면은 그대로 동작한다.
2. **예시·계산·결과.** 예시 선택 위젯의 key는 `preset`, 예시는 5개이고 순서가 같다(엔/원 30년 CCS, USD/KRW 5년 CCS, USD/KRW 1년 FX 선도, 원화 IRS 5년, 달러 OIS 3년).
   계산 버튼의 라벨은 정확히 `계산`. 계산하면 `st.session_state["result"]`에 dict를 넣고, 다음 항목을 지금과 같은 뜻으로 유지한다:
   `deal, base, be, zero, be_running, hedge_kind, loc, fee, upfront, client_no_fee, params` (정의: `ui_eval/contract.py`의 `inputs_from_result`, `answers_from_result`).
3. **입력 위젯의 key와 단위.** `product, mat, cr_ccy, cr_amt, cr_kind, cr_rate, cr_spread, cr_freq, cr_dc, cr_fx_end`(cp_ 도 같음), `irs_amt, irs_dir, irs_rate, irs_spread,
   h_mode, fee_loc, fee_bp, fee_fx, upfront_oku`는 key와 값의 단위(억원·억엔·백만달러, %, bp, 원)를 유지한다. 옆에 확인용 표시나 보조 입력을 더하는 것은 된다.
4. **역할.** 관리자 로그인: 비밀번호 입력 key `admin_pw`, 버튼 라벨 `관리자 로그인`, 로그아웃 버튼 라벨 `로그아웃`. 로그인하면 `st.session_state["role"] == "admin"`.
   비밀번호는 `st.secrets["admin_password"]`. 비밀값이 없으면 로그인을 막고 안내만 한다.
5. **관리자 전용 위젯.** key가 `adm_`로 시작한다. 일반 사용자 화면에는 그리지 않는다(비활성으로 보여 주는 것도 안 된다).
   관리자 전용: 은행 파라미터, 시뮬레이션 설정, 시장 데이터 기준 선택과 새로 받기, 거래상대방 마스터 조회·편집, 상대방의 등급·PD·위험가중치·업종·CSA 입력.
   일반 사용자는 상대방을 마스터의 이름으로 고르기만 한다 (고객 선택 key `cl_name`, 헤지 딜러 선택 key `dl_name`).
6. **데이터 디렉터리.** 환경 변수 `LINEFEE_DATA_DIR`(없으면 저장소의 `data/`)에서 읽고 쓴다.
   - `counterparties.csv`: 거래상대방 마스터. 없으면 저장소의 `data/counterparties.sample.csv`를 읽는다. 관리자가 저장하면 데이터 디렉터리의 `counterparties.csv`에 쓴다.
     열: `name, grade, pd_pct, csa, dealer_line, credit_rw_pct, ba_cva_sector` (UTF-8, BOM 허용). 값의 뜻은 샘플 파일과 `ui_eval/contract.py`의 `master_row`. LGD는 60% 고정.
   - `bank_params.json`: 관리자가 저장한 은행 파라미터·시뮬레이션 설정. 키는 `linefee.trade.BankParams`의 필드 이름. 없거나 빠진 키는 BankParams 기본값.
   - 화면 코드는 `ui_eval/`을 import하지 않는다 (하니스와 화면은 서로 독립).
7. **예시와 마스터.** 예시의 상대방은 마스터 이름을 가리킨다: 예시 1 = 공공기관 A / 딜러 B, 예시 2 = 기업 C / 딜러 B, 예시 3 = 기업 C / 딜러 D, 예시 4·5 = 기업 C / CCP.
   로컬 마스터에 그 이름이 없으면 샘플 파일의 행을 쓴다. 샘플 값은 지금 예시의 값과 같으므로 계산 결과는 변하지 않는다.
8. **오래된 결과.** 계산한 뒤 입력이 바뀌면 이전 결과를 숨기거나, 결과와 함께 `이전 입력 기준`이라는 글자가 들어간 경고를 보여 준다.
9. **경고.** 입력이 이상하다는 알림은 경고·오류 요소(`st.warning`, `st.error`)로 낸다. 설명글(caption)은 경고로 세지 않는다.
10. **입력 요약 (U의 개선 목표, U0는 선택).** 결과 영역에 `입력 요약`이라는 글자로 시작하는 묶음을 두고, 계산에 쓴 상품, 만기, 두 다리 명목(단위 포함),
    고객 이름, 라인피를 적는다. 채점 스크립트가 이 묶음이 있는지, 값이 계산에 쓴 입력과 같은지 본다.

### 화면 프레임워크 교체 (DECISIONS O11)

- U0는 Streamlit을 유지한다 (기준 점수 r0를 지금 화면과 같은 틀에서 잰다).
- U는 **라운드 R1에서만** 교체할 수 있다. 조건: 공개 과제 로그에서 Streamlit의 동작 방식 자체가 원인인 실패가 2건 이상이고, Streamlit 안에서는 고칠 수 없다고 판단할 때. R2에서는 교체하지 않는다.
- 교체하면 `ui/testing.py`를 함께 낸다. 하니스와 값 불변 게이트는 이 파일이 있으면 Streamlit AppTest 대신 이것을 쓴다 (tools, ui_eval은 고치지 않아도 된다).
  - `golden_results() -> list[(예시 이름, base, be)]`: 일반 사용자 화면의 코드 경로로 예시 5개를 순서대로 불러와 계산한 결과. `tools/golden_check.py --ui`가 읽는다.
  - `Backend` 클래스: `ui_eval/backend.py`에 H가 적어 둔 규약(화면 요소 목록, 값 넣기, 누르기, 결과 읽기, 역할)을 구현한다.
  - 계약 2~10의 key·라벨·글자는 요소의 id·라벨로 그대로 유지한다.
- 교체한 라운드의 채택 여부는 스크립트 판정에 더해 총괄이 `ui/testing.py`를 직접 확인한 뒤에 정한다 (비서가 총괄 오더를 낸다).

## 검사 명령 (worktree 루트에서)

```
python tools/golden_check.py --ui                 # 값 불변 게이트. "통과"
python tools/path_gate.py --role <harness|ui> --base <main|태그>
python -m tests.test_app                          # 화면 테스트
python -m tests.test_score                        # 채점기 단위 테스트 (H가 만든 뒤)
python -m ui_eval.audit_roles                     # 일반 사용자 화면에 관리자 영역이 없는지 (H가 만든 뒤)
python -m ui_eval.smoke                           # 드라이버 → 로그 → 채점 한 바퀴 (H가 만든 뒤)
```

## 커밋

- 한 가지 변경에 커밋 하나. 제목은 `B <역할> <한 일>` (예: `B U0 역할 분리: 은행 파라미터를 관리자 화면으로`).
- `git add`는 파일 이름으로 지정한다 (`git add -A`, `git add .` 금지).
- 메시지 끝: `Co-Authored-By: Claude Sonnet <noreply@anthropic.com>` (자기 모델 이름으로).
- 기존 코드의 문체를 따른다 (한국어 주석·docstring, 줄 길이 약 120자).
