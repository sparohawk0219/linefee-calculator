# 라인피 계산기

장외파생 거래 1건(통화스왑, FX 선도, 원화 IRS, 달러 OIS)과 그 헤지가 은행에 일으키는 신용·조달·증거금·자본 비용(E/L, FVA, MVA, KVA)을 계산하고,
이를 덮는 손익분기 수수료(라인피)를 구한다. 방법론은 [docs/METHODOLOGY_v1.md](docs/METHODOLOGY_v1.md), 확정 결정은 [docs/DECISIONS.md](docs/DECISIONS.md),
목표 설계는 [DESIGN.md](DESIGN.md)에 있다.

## 설치

Python 3.10 이상.

    pip install -r requirements.txt

블룸버그에서 시장 데이터를 새로 받을 때만 `blpapi`가 필요하다 (설치 방법은 `requirements.txt` 주석).

## 실행

    streamlit run app.py      # 대시보드 (http://localhost:8501)

## 사용자 역할과 데이터 파일

처음 화면은 일반 사용자(세일즈)다. 로그인 없이 거래를 넣고 결과를 본다. 은행 파라미터, 시뮬레이션 설정, 시장 데이터 기준,
거래상대방 마스터는 관리자만 고친다 (사이드바의 `관리자 로그인`).

- 관리자 비밀번호: `.streamlit/secrets.toml.example`을 `.streamlit/secrets.toml`로 복사해 `admin_password`를 바꾼다. 이 파일이 없으면 관리자 로그인은 막힌다.
- 데이터 디렉터리: 환경 변수 `LINEFEE_DATA_DIR`(없으면 저장소의 `data/`). 관리자가 `저장`을 누르면 여기에 쓰고, 모든 사용자의 계산에 쓰인다.
  - `counterparties.csv`: 거래상대방 마스터 (열 10개: name, grade, pd_pct, csa, dealer_line, credit_rw_pct, ba_cva_sector, vm_ccy, rw_method, sales_eok).
    뒤의 세 열은 선택이다. `vm_ccy`는 VM 담보 통화(USD 또는 KRW, 빈 칸이면 USD)로 FVA 조달 스프레드를 고른다. `rw_method`는 신용 RW 방식(`고정` 또는 `IRB`, 빈 칸이면 고정)이며, `IRB`면 `credit_rw_pct` 대신 PD로 기본내부등급법 RW를 계산한다(METHODOLOGY 7.3). `sales_eok`는 연 매출(억원, 빈 칸 가능)로 700억 이하면 IRB 중소기업 상관계수 조정에 쓴다. 7열 파일도 읽히지만 `rw_method`가 비면 고정 RW가 쓰이므로 IRB 기업은 열을 채운다. 없으면 가명 샘플 `data/counterparties.sample.csv`를 읽는다. 엑셀에서 UTF-8 CSV로 만들어도 된다.
  - `bank_params.json`: 저장한 은행 파라미터. 없으면 엔진 기본값.
  - 둘 다 실데이터라 gitignore 대상이다.

## 시장 스냅샷 (로컬 전용)

시장 데이터는 블룸버그 라이선스 데이터라 저장소에 없다 (DECISIONS O1). 스냅샷이 없으면 앱과 테스트는 안내를 출력하고 멈춘다.
스냅샷은 두 개이며 서로 다른 파일이다.

| 파일 | 용도 | 준비 |
|---|---|---|
| `linefee/market_snapshot.json` | 최신. 앱의 기본 시장 | 블룸버그 터미널이 켜진 PC에서 `python -m linefee.market` (현재값과 과거 5년 주간 변동성·상관을 받아 덮어쓴다) |
| `market_snapshot.json` (루트) | 회귀 기준. 날짜 고정(엔/원 검토 시점), KRW·JPY만 | 새로 받을 수 없다. 기존 작업 PC의 파일을 복사한다 |

호가 기준일(`as_of`)은 영업일이어야 한다. 주말·공휴일 기준일의 스냅샷은 읽을 때 오류를 낸다.

## 테스트

스냅샷 없이 돈다:

    python -m tests.test_dates            # 날짜 계산
    python -m tests.test_snapshot         # 스냅샷 필수 키 검사 (스냅샷이 있으면 그 파일을 검사)
    python -m tests.test_store            # 마스터·파라미터 저장
    python -m tests.test_irb              # 기본내부등급법 RW
    python -m tests.test_regcap_floor     # SA-CCR 만기 하한
    python -m tests.test_score            # 상대방 점수

스냅샷이 필요하다 (없으면 안내를 출력하고 멈춘다):

    python -m tests.test_app              # 대시보드(AppTest). 비밀값 파일 없이도 돈다
    python -m tests.test_curves           # 커브 재평가
    python -m tests.regression_jpykrw     # 엔/원 회귀
    python -m tests.test_engine_kappa     # 금리 평균회귀 엔진
    python -m tests.verify_methodology    # 방법론 13장 검증 (수 분)
    python main.py                        # 구버전 엔/원 계산 (루트 스냅샷)

병합 전 값 불변 게이트(O7)는 `python tools/golden_check.py --ui`다. 기준값은 로컬 파일(`local/golden_baseline.json`, 저장소에 없다)이라
기준값을 가진 PC에서만 돈다.

## 문서

- [docs/METHODOLOGY_v1.md](docs/METHODOLOGY_v1.md): 구현된 방법론 (v1)
- [DESIGN.md](DESIGN.md): 목표 설계
- [docs/DECISIONS.md](docs/DECISIONS.md): 확정 결정
