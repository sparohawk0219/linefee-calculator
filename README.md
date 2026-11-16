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

## 시장 스냅샷 (로컬 전용)

시장 데이터는 블룸버그 라이선스 데이터라 저장소에 없다 (DECISIONS O1). 스냅샷이 없으면 앱과 테스트는 안내를 출력하고 멈춘다.
스냅샷은 두 개이며 서로 다른 파일이다.

| 파일 | 용도 | 준비 |
|---|---|---|
| `linefee/market_snapshot.json` | 최신. 앱의 기본 시장 | 블룸버그 터미널이 켜진 PC에서 `python -m linefee.market` (현재값과 과거 5년 주간 변동성·상관을 받아 덮어쓴다) |
| `market_snapshot.json` (루트) | 회귀 기준. 날짜 고정(엔/원 검토 시점), KRW·JPY만 | 새로 받을 수 없다. 기존 작업 PC의 파일을 복사한다 |

호가 기준일(`as_of`)은 영업일이어야 한다. 주말·공휴일 기준일의 스냅샷은 읽을 때 오류를 낸다.

## 테스트 (스냅샷 필요)

    python -m tests.test_dates            # 날짜 계산 (스냅샷 불필요)
    python -m tests.test_snapshot         # 스냅샷 필수 키 검사 (스냅샷 불필요)
    python -m tests.test_curves           # 커브 재평가
    python -m tests.regression_jpykrw     # 엔/원 회귀
    python -m tests.verify_methodology    # 방법론 13장 검증 (수 분)

## 문서

- [docs/METHODOLOGY_v1.md](docs/METHODOLOGY_v1.md): 구현된 방법론 (v1)
- [DESIGN.md](DESIGN.md): 목표 설계
- [docs/DECISIONS.md](docs/DECISIONS.md): 확정 결정
