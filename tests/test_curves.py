"""커브 검증: 부트스트랩에 넣은 시장 상품을 엔진(실제 일정)으로 다시 평가하면 화면 금리가 Par로 나와야 한다.

    python -m tests.test_curves      # 표 출력 + 허용오차 검사
    python -m pytest tests/test_curves.py   # pytest가 설치돼 있으면

허용오차 0.006bp (FX 선도 0.001원), METHODOLOGY_v1.md의 서술과 같은 숫자.
엔진은 OIS 지급 지연(2영업일)을 무시하므로 그만큼의 차이는 남는다 (지급 지연이 휴일에 걸리는 날은 더 커진다: 11-25 스냅샷 USD OIS 1개월 0.24bp. 허용오차는 휴일에 걸리지 않는 날 기준이라 완화하지 않는다).
1주 OIS·1주 스왑포인트·CD 91일은 엔진 일정이 개월 단위라 커브에서 직접 재평가한다.
"""
import sys

import QuantLib as ql

from linefee import curves as cv
from linefee.market import load
from linefee.pricing import market_hedge
from linefee.trade import Leg, Trade

TOL_BP = 0.006
TOL_FX = 0.001


def _months(label: str) -> float:
    n, u = int(label[:-1]), label[-1]
    return {"M": n / 12, "Y": n}[u]


def ois_par(m, ccy, label, freq, dc, curve_kind="local"):
    """같은 통화 고정 vs 변동 (원금 교환 없음) 파 금리."""
    n = 1e8
    tr = Trade([Leg(ccy, n, "receive", "fixed", 0.03, freq, dc, False, curve_kind),
                Leg(ccy, n, "pay", "float", 0.0, freq, dc, False, curve_kind)], _months(label), "IRS")
    return next(l for l in market_hedge(tr, m).legs if l.kind == "fixed").rate * 100


def krw_ccs_par(m, label):
    """KRW 고정(분기, ACT/365F) vs SOFR 플랫(분기, ACT/360), 만기 원금 교환."""
    usd = 1e8
    tr = Trade([Leg("KRW", usd * m.spot_quote["USD"], "receive", "fixed", 0.03, 4, "ACT/365"),
                Leg("USD", usd, "pay", "float", 0.0, 4, "ACT/360")], _months(label), "CCS")
    return market_hedge(tr, m).legs[0].rate * 100      # 헤지(미러)에서 KRW 다리가 은행 지급 다리


def jpy_basis(m, label):
    """TONA + 베이시스(분기, ACT/365F) vs SOFR 플랫(분기, ACT/360), 만기 원금 교환. bp."""
    usd = 1e8
    usdjpy = m.quotes["spot"]["USDJPY"]
    tr = Trade([Leg("JPY", usd * usdjpy, "receive", "float", 0.0, 4, "ACT/365"),
                Leg("USD", usd, "pay", "float", 0.0, 4, "ACT/360")], _months(label), "CCS")
    return market_hedge(tr, m).legs[0].rate * 1e4


def fx_outright(m, ccy, label):
    """은행이 외화 1단위를 사는 선도의 시장가 선도환율 (호가 단위: USDKRW, USDJPY)."""
    if ccy == "USD":
        tr = Trade([Leg("USD", 1e6, "receive", final_exchange=True),
                    Leg("KRW", 1e6 * 1300, "pay", final_exchange=True)], _months(label), "FXFWD")
        h = market_hedge(tr, m)
        return next(l for l in h.legs if l.ccy == "KRW").notional / 1e6
    # USDJPY: 달러·엔 선도 (뉴욕·도쿄 영업일). 헤지(미러)의 은행 지급 다리(USD) 금액을 조정한다
    tr = Trade([Leg("USD", 1e6, "receive", final_exchange=True),
                Leg("JPY", 1e6 * 150, "pay", final_exchange=True)], _months(label), "FXFWD")
    h = market_hedge(tr, m)
    return 1e6 * 150 / next(l for l in h.legs if l.ccy == "USD").notional


def _t(m, d) -> float:
    return cv.DC365.yearFraction(cv.qdate(m.as_of), d)


def ois_par_1w(m, ccy, dc):
    """1주 OIS 파 금리. 지급이 한 번이라 지급 지연은 양 다리에서 상쇄된다. 엔진 일정은 개월 단위라 커브에서 직접 계산한다."""
    today, cal = cv.qdate(m.as_of), cv.CALENDARS[ccy]
    eff = cal.advance(today, 2, ql.Days)
    end = cal.advance(eff, ql.Period("1W"), ql.ModifiedFollowing)
    acc = (ql.Actual360() if dc == "ACT/360" else cv.DC365).yearFraction(eff, end)
    c = m.local[ccy]
    return (c.df(_t(m, eff)) / c.df(_t(m, end)) - 1) / acc * 100


def krw_cd_par(m):
    """CD 91일: T+1 시작 3개월 예금 단리 (ACT/365F)."""
    today, cal = cv.qdate(m.as_of), cv.CALENDARS["KRW"]
    s = cal.advance(today, 1, ql.Days)
    e = cal.advance(s, ql.Period(3, ql.Months), ql.ModifiedFollowing)
    c = m.proj["KRW"]
    return (c.df(_t(m, s)) / c.df(_t(m, e)) - 1) / cv.DC365.yearFraction(s, e) * 100


def fx_outright_1w(m, ccy):
    """1주 선도환율 (호가 단위: USDKRW, USDJPY). 결제일(T+2)에서 1주 뒤, 두 통화 공통 영업일."""
    today = cv.qdate(m.as_of)
    other = "KRW" if ccy == "USD" else "JPY"
    cal = cv.calendar(["USD", other])
    t = _t(m, cal.advance(cal.advance(today, 2, ql.Days), ql.Period("1W"), ql.ModifiedFollowing))
    return float(m.fwd_fx("USD", t) if ccy == "USD" else m.fwd_fx("USD", t) / m.fwd_fx("JPY", t))


def check(m=None, verbose=True) -> list[str]:
    m = m or load()
    q = m.quotes
    rows, fails = [], []

    def add(name, label, model, screen, tol, unit):
        diff = model - screen
        rows.append(f"{name:<14}{label:>5}{screen:>12.4f}{model:>12.4f}{diff:>+10.4f} {unit}")
        if abs(diff) > tol:
            fails.append(f"{name} {label}: {diff:+.4f}{unit}")

    for label, v in q["usd_ois"].items():
        model = ois_par_1w(m, "USD", "ACT/360") if label == "1W" else ois_par(m, "USD", label, 1, "ACT/360")
        add("USD OIS", label, model, v, TOL_BP / 100, "%")
    for label, v in q["jpy_ois"].items():
        model = ois_par_1w(m, "JPY", "ACT/365") if label == "1W" else ois_par(m, "JPY", label, 1, "ACT/365")
        add("JPY OIS", label, model, v, TOL_BP / 100, "%")
    for label, v in q["krw_irs"].items():
        model = krw_cd_par(m) if label == "CD" else ois_par(m, "KRW", label, 4, "ACT/365")
        add("KRW IRS", label, model, v, TOL_BP / 100, "%")
    for label, v in q["krw_ccs"].items():
        if _months(label) <= 1:          # 1년 이하는 스왑포인트로 부트스트랩
            continue
        add("KRW CCS", label, krw_ccs_par(m, label), v, TOL_BP / 100, "%")
    for label, v in q["jpy_basis_bp"].items():
        if _months(label) <= 1:
            continue
        add("JPY 베이시스", label, jpy_basis(m, label), v, TOL_BP, "bp")
    spot = q["spot"]
    for ccy, s0 in (("USD", spot["USD"]), ("JPY", spot["USDJPY"])):
        for label, pts in q[f"fx_pts_{ccy}"].items():
            model = fx_outright_1w(m, ccy) if label == "1W" else fx_outright(m, ccy, label)
            add(f"FX {ccy}", label, model, s0 + pts, TOL_FX, "")
    if verbose:
        print(f"{'상품':<14}{'만기':>5}{'화면':>12}{'엔진 Par':>12}{'차이':>10}")
        print("\n".join(rows))
        print("실패: " + (", ".join(fails) if fails else "없음"))
    return fails


def test_reprice_inputs():
    assert check(verbose=False) == []


if __name__ == "__main__":
    try:
        fails = check()
    except FileNotFoundError as e:   # 스냅샷은 로컬 전용 (L-018)
        sys.exit(str(e))
    sys.exit(1 if fails else 0)      # 실패가 있으면 종료 코드 1 (L-017)
