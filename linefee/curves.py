"""QuantLib 커브 부트스트랩과 거래 일정.

각 커브는 시장 상품을 실제 관행(결제일, 지급 주기, 일수 계산, 영업일)대로 QuantLib 헬퍼로 넣어
PiecewiseLogLinearDiscount(ACT/365F)로 부트스트랩한다. 엔진은 넘파이로 빠르게 계산해야 하므로
부트스트랩이 끝난 커브의 노드(시점, 할인계수)를 꺼내 같은 보간(로그 할인계수 선형)을 넘파이로 재현한다.
노드 사이 값은 QuantLib과 같고, 마지막 노드 이후는 마지막 구간의 선도금리로 연장한다.

커브                     상품 (블룸버그)                                  담보
USD SOFR                 SOFR OIS 1주~30년 (USOSFR)                       USD
JPY TONA                 TONA OIS 1주~30년 (JYSO)                         JPY
KRW CD (원화 IRS)        CD 91일 + KRW IRS 6개월~30년 (KWCDC, KWSWO)      KRW (단일 커브)
KRW (USD 담보)           USDKRW 스왑포인트 1주~12개월 (KRW{n}M)           USD
                         + KRW 고정 vs SOFR CCS 2~30년 (KRWNSQ)
JPY (USD 담보)           USDJPY 스왑포인트 1주~12개월 (JPY{n}M)           USD
                         + TONA vs SOFR 베이시스 2~30년 (JYBSS)
"""
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
import QuantLib as ql

DC365 = ql.Actual365Fixed()
MF = ql.ModifiedFollowing

CALENDARS = {
    "USD": ql.UnitedStates(ql.UnitedStates.GovernmentBond),
    "JPY": ql.Japan(),
    "KRW": ql.SouthKorea(ql.SouthKorea.Settlement),
}


def calendar(ccys) -> ql.Calendar:
    """통화들의 공통 영업일 달력."""
    cals = [CALENDARS[c] for c in sorted(set(ccys))]
    return cals[0] if len(cals) == 1 else ql.JointCalendar(*cals)


def qdate(iso: str) -> ql.Date:
    return ql.DateParser.parseISO(iso)


def period(label: str) -> ql.Period:
    """'1W', '6M', '18M', '2Y' -> ql.Period."""
    return ql.Period(label)


def months(label: str) -> int:
    """테너 라벨의 개월 수 (주 단위는 0)."""
    n, u = int(label[:-1]), label[-1]
    return {"W": 0, "M": n, "Y": 12 * n}[u]


# ---------------------------------------------------------------------------
# 넘파이 커브
# ---------------------------------------------------------------------------
@dataclass
class Curve:
    """할인커브. times: 노드 시점(ACT/365F, 기준일 0), logdf: 노드 로그 할인계수."""
    times: np.ndarray
    logdf: np.ndarray
    name: str = ""

    def df(self, t) -> np.ndarray:
        t = np.maximum(np.asarray(t, dtype=float), 0.0)
        out = np.interp(t, self.times, self.logdf)
        last = self.times[-1]
        if np.any(t > last):    # 마지막 구간 선도금리로 연장
            slope = (self.logdf[-1] - self.logdf[-2]) / (self.times[-1] - self.times[-2])
            out = np.where(t > last, self.logdf[-1] + slope * (t - last), out)
        return np.exp(out)

    def zero_rate(self, t: float) -> float:
        return float(-np.log(self.df(t)) / t)

    @classmethod
    def from_ql(cls, curve: ql.YieldTermStructure, name: str = "") -> "Curve":
        ref = curve.referenceDate()
        nodes = curve.nodes()
        times = np.array([DC365.yearFraction(ref, d) for d, _ in nodes])
        return cls(times, np.log([v for _, v in nodes]), name)


# ---------------------------------------------------------------------------
# 부트스트랩
# ---------------------------------------------------------------------------
def _q(x: float) -> ql.QuoteHandle:
    return ql.QuoteHandle(ql.SimpleQuote(x))


def _build(today: ql.Date, helpers: list) -> ql.YieldTermStructure:
    c = ql.PiecewiseLogLinearDiscount(today, helpers, DC365)
    c.enableExtrapolation()
    c.nodes()   # 여기서 부트스트랩
    return c


def sofr_curve(today, quotes: dict) -> ql.YieldTermStructure:
    """USD SOFR OIS: T+2, 연 1회 지급(1년 미만은 만기 일시), ACT/360, 지급 지연 2영업일."""
    idx = ql.Sofr()
    hs = [ql.OISRateHelper(2, period(k), _q(v / 100), idx, paymentLag=2, telescopicValueDates=True)
          for k, v in quotes.items()]
    return _build(today, hs)


def tona_curve(today, quotes: dict) -> ql.YieldTermStructure:
    """JPY TONA OIS: T+2, 연 1회 지급, ACT/365F, 지급 지연 2영업일."""
    idx = ql.Tonar()
    hs = [ql.OISRateHelper(2, period(k), _q(v / 100), idx, paymentLag=2, telescopicValueDates=True)
          for k, v in quotes.items()]
    return _build(today, hs)


def krw_cd_index(handle=None) -> ql.IborIndex:
    h = handle if handle is not None else ql.YieldTermStructureHandle()
    return ql.IborIndex("KRWCD91D", ql.Period(3, ql.Months), 1, ql.KRWCurrency(), CALENDARS["KRW"], MF,
                        False, DC365, h)


def krw_irs_curve(today, quotes: dict) -> ql.YieldTermStructure:
    """KRW IRS (CD 91일): T+1, 고정·변동 분기 지급, ACT/365F, 서울 영업일. 단일 커브(할인 = 추정).

    'CD' 키는 CD 91일 금리(T+1 시작 3개월 예금으로 넣는다).
    """
    idx = krw_cd_index()
    cal = CALENDARS["KRW"]
    hs = []
    for k, v in quotes.items():
        if k == "CD":
            hs.append(ql.DepositRateHelper(_q(v / 100), ql.Period(3, ql.Months), 1, cal, MF, False, DC365))
        else:
            hs.append(ql.SwapRateHelper(_q(v / 100), period(k), cal, ql.Quarterly, MF, DC365, idx,
                                        ql.QuoteHandle(), ql.Period(0, ql.Days),
                                        ql.YieldTermStructureHandle(), 1))
    return _build(today, hs)


def _fx_helpers(points: dict, spot: float, ccy: str, usd: ql.YieldTermStructureHandle) -> list:
    """USD/ccy 스왑포인트 (ccy 단위, 이미 배율 반영) -> FxSwapRateHelper. USD가 담보 통화(기준 통화)."""
    cal = calendar(["USD", ccy])
    return [ql.FxSwapRateHelper(_q(v), _q(spot), period(k), 2, cal, MF, False, True, usd)
            for k, v in points.items()]


def krw_csa_curve(today, ccs: dict, points: dict, spot: float,
                  usd: ql.YieldTermStructureHandle) -> ql.YieldTermStructure:
    """KRW 할인 커브 (USD 현금 담보).

    1년 이하: USDKRW 스왑포인트. 그 뒤: KRW 고정(분기, ACT/365F) vs SOFR 복리(분기, ACT/360) CCS, 원금 교환,
    T+2, 서울·뉴욕 공통 영업일. 스왑포인트가 없으면 1년부터 CCS를 쓴다.
    """
    cap = max((months(k) for k in points), default=0)
    hs = _fx_helpers(points, spot, "KRW", usd)
    sofr = ql.Sofr(usd)
    cal = calendar(["USD", "KRW"])
    for k, v in ccs.items():
        if months(k) <= cap:
            continue
        hs.append(ql.ConstNotionalCrossCurrencySwapRateHelper(
            _q(v / 100), period(k), 2, cal, MF, False, ql.Quarterly, DC365, sofr, usd, False,
            0, None, ql.Quarterly))
    return _build(today, hs)


def jpy_csa_curve(today, basis_bp: dict, points: dict, spot: float, usd: ql.YieldTermStructureHandle,
                  tona: ql.YieldTermStructureHandle) -> ql.YieldTermStructure:
    """JPY 할인 커브 (USD 현금 담보).

    1년 이하: USDJPY 스왑포인트. 그 뒤: TONA + 베이시스 vs SOFR 플랫 (분기 복리, 원금 교환, T+2).
    """
    cap = max((months(k) for k in points), default=0)
    hs = _fx_helpers(points, spot, "JPY", usd)
    cal = calendar(["USD", "JPY"])
    for k, v in basis_bp.items():
        if months(k) <= cap:
            continue
        hs.append(ql.ConstNotionalCrossCurrencyBasisSwapRateHelper(
            _q(v / 1e4), period(k), 2, cal, MF, False, ql.Sofr(usd), ql.Tonar(tona), usd,
            True, False, ql.Quarterly))
    return _build(today, hs)


def flat_curve(today, rate: float) -> ql.YieldTermStructure:
    return ql.FlatForward(today, rate, DC365)


# ---------------------------------------------------------------------------
# 거래 일정
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Sched:
    pay: np.ndarray      # 지급 시점 (ACT/365F, 평가일 0)
    start: np.ndarray    # 이자 기간 시작 시점
    accr: np.ndarray     # 이자 기간 일수 비율 (다리 일수 계산 기준)


def spot_lag(product: str, ccys) -> int:
    """거래 시작(결제) 지연 영업일. 원화 IRS T+1, 나머지 T+2."""
    return 1 if product == "IRS" and set(ccys) == {"KRW"} else 2


@lru_cache(maxsize=4096)
def schedule(as_of: str, product: str, ccys: tuple, n_months: int, freq: int, day_count: str) -> Sched:
    """시작일 = 평가일 + 결제 지연(공통 영업일), 만기일 = 시작일 + 만기(조정 전 날짜).

    지급일은 조정 전 만기일에서 거꾸로 12/freq개월씩 잡은 뒤 각각 수정 익영업일로 조정한다(QuantLib 스왑과 같은 방식).
    조정한 만기일에서 거꾸로 잡으면 만기일이 휴일일 때 시작 부분에 며칠짜리 구간이 생긴다.
    FX 선도는 만기일(수정 익영업일) 한 번. 지급 지연(OIS 2영업일)은 반영하지 않는다.
    """
    today = qdate(as_of)
    cal = calendar(ccys)
    eff = cal.advance(today, spot_lag(product, ccys), ql.Days)
    end = eff + ql.Period(n_months, ql.Months)
    if product == "FXFWD":
        dates = [eff, cal.adjust(end, MF)]
    else:
        sch = ql.Schedule(eff, end, ql.Period(12 // freq, ql.Months), cal, MF, MF,
                          ql.DateGeneration.Backward, False)
        dates = list(sch)
    dc = ql.Actual360() if day_count == "ACT/360" else DC365
    t = np.array([DC365.yearFraction(today, d) for d in dates])
    accr = np.array([dc.yearFraction(a, b) for a, b in zip(dates[:-1], dates[1:])])
    return Sched(pay=t[1:], start=t[:-1], accr=accr)


def spot_date_time(as_of: str, ccys) -> float:
    today = qdate(as_of)
    return DC365.yearFraction(today, calendar(ccys).advance(today, 2, ql.Days))
