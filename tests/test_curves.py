"""커브 검증: 부트스트랩에 넣은 시장 상품을 엔진(실제 일정)으로 다시 평가하면 화면 금리가 Par로 나와야 한다.

    python -m tests.test_curves      # 표 출력 + 허용오차 검사
    python -m pytest tests/test_curves.py   # pytest가 설치돼 있으면

허용오차 0.01bp (FX 선도 0.001원). 엔진은 OIS 지급 지연(2영업일)을 무시하므로 그만큼(최대 약 0.005bp)의 차이는 남는다.
"""
from linefee.market import load
from linefee.pricing import market_hedge
from linefee.trade import Leg, Trade

TOL_BP = 0.01
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
        if label.endswith("W"):
            continue
        add("USD OIS", label, ois_par(m, "USD", label, 1, "ACT/360"), v, TOL_BP / 100, "%")
    for label, v in q["jpy_ois"].items():
        if label.endswith("W"):
            continue
        add("JPY OIS", label, ois_par(m, "JPY", label, 1, "ACT/365"), v, TOL_BP / 100, "%")
    for label, v in q["krw_irs"].items():
        if label in ("CD",):
            continue
        add("KRW IRS", label, ois_par(m, "KRW", label, 4, "ACT/365"), v, TOL_BP / 100, "%")
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
            if label.endswith("W"):
                continue
            add(f"FX {ccy}", label, fx_outright(m, ccy, label), s0 + pts, TOL_FX, "")
    if verbose:
        print(f"{'상품':<14}{'만기':>5}{'화면':>12}{'엔진 Par':>12}{'차이':>10}")
        print("\n".join(rows))
        print("실패: " + (", ".join(fails) if fails else "없음"))
    return fails


def test_reprice_inputs():
    assert check(verbose=False) == []


if __name__ == "__main__":
    check()
