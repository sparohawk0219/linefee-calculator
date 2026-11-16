"""ISDA SIMM으로 딜러 B와의 IM 계산 (RatesFX 상품군, delta만).

고정-고정 CCS라서 vega와 curvature는 없다. 금리 delta, 통화간 베이시스, FX delta만 있다.
이 거래 하나만 넣은 단독 계산이고, 딜러 B와의 기존 거래와의 상계는 반영하지 않는다.

현재도 쓰는 모듈이다: `linefee/regcap.py`가 PARAMS, VERTICES, vertex_pv01을 가져다 쓴다 (구버전 표시 대상 아님).

파라미터 출처: ISDA SIMM Methodology
    v2.8+2512 (2026-07-11 시행, 현재 적용)
    v2.9+2606 (2026-12-05 시행 예정)

민감도 정의
    금리: zero 금리 1bp 상승 시 가치 변화(PV01). 현금흐름별로 계산해 인접 vertex 둘에 선형 배분
    베이시스: 해당 통화 할인 스프레드 1bp 상승 시 가치 변화 (= 그 통화 다리의 PV01 합계)
    FX: 계산통화 대비 해당 통화 1% 상승 시 가치 변화
집중도 위험계수는 1로 둔다. 이 거래의 PV01은 1억~2억원(USD 0.1~0.2mm) 수준이어서
임계값(KRW 220, JPY 110 USD mm/bp)보다 훨씬 작다.
"""
import numpy as np

VERTICES = np.array([14 / 365, 1 / 12, 0.25, 0.5, 1, 2, 3, 5, 10, 15, 20, 30])

_CORR_28 = [
    [1.00, .74, .65, .54, .40, .29, .25, .22, .17, .16, .14, .14],
    [.74, 1.00, .85, .72, .50, .36, .30, .25, .20, .16, .14, .14],
    [.65, .85, 1.00, .90, .69, .53, .46, .40, .34, .27, .25, .25],
    [.54, .72, .90, 1.00, .86, .73, .65, .58, .52, .47, .44, .42],
    [.40, .50, .69, .86, 1.00, .94, .87, .81, .73, .69, .64, .63],
    [.29, .36, .53, .73, .94, 1.00, .97, .92, .86, .82, .77, .76],
    [.25, .30, .46, .65, .87, .97, 1.00, .97, .91, .87, .82, .81],
    [.22, .25, .40, .58, .81, .92, .97, 1.00, .96, .93, .89, .88],
    [.17, .20, .34, .52, .73, .86, .91, .96, 1.00, .98, .95, .95],
    [.16, .16, .27, .47, .69, .82, .87, .93, .98, 1.00, .98, .97],
    [.14, .14, .25, .44, .64, .77, .82, .89, .95, .98, 1.00, .98],
    [.14, .14, .25, .42, .63, .76, .81, .88, .95, .97, .98, 1.00],
]
_CORR_29 = [
    [1.00, .72, .64, .53, .38, .28, .24, .20, .17, .15, .13, .11],
    [.72, 1.00, .82, .66, .48, .36, .31, .27, .22, .17, .15, .13],
    [.64, .82, 1.00, .89, .69, .55, .49, .43, .36, .30, .26, .25],
    [.53, .66, .89, 1.00, .85, .74, .67, .60, .52, .47, .44, .42],
    [.38, .48, .69, .85, 1.00, .95, .89, .82, .73, .68, .63, .61],
    [.28, .36, .55, .74, .95, 1.00, .96, .92, .84, .79, .74, .72],
    [.24, .31, .49, .67, .89, .96, 1.00, .97, .90, .85, .80, .78],
    [.20, .27, .43, .60, .82, .92, .97, 1.00, .96, .92, .88, .86],
    [.17, .22, .36, .52, .73, .84, .90, .96, 1.00, .98, .95, .95],
    [.15, .17, .30, .47, .68, .79, .85, .92, .98, 1.00, .98, .97],
    [.13, .15, .26, .44, .63, .74, .80, .88, .95, .98, 1.00, .99],
    [.11, .13, .25, .42, .61, .72, .78, .86, .95, .97, .99, 1.00],
]

PARAMS = {
    "2.8+2512": dict(
        rw_regular=[107, 101, 90, 69, 68, 69, 66, 61, 60, 58, 58, 66],   # KRW
        rw_low=[15, 18, 12, 11, 15, 21, 23, 25, 29, 27, 26, 28],          # JPY
        rw_basis=21, rho_basis=-0.01, tenor_corr=np.array(_CORR_28), gamma=0.35,
        rw_fx=7.4, rho_fx=0.50, psi_ir_fx=0.15,
    ),
    "2.9+2606": dict(
        rw_regular=[100, 93, 83, 68, 60, 57, 54, 51, 49, 51, 54, 63],
        rw_low=[21, 22, 12, 11, 15, 21, 23, 25, 26, 24, 26, 28],
        rw_basis=21, rho_basis=-0.09, tenor_corr=np.array(_CORR_29), gamma=0.30,
        rw_fx=7.2, rho_fx=0.50, psi_ir_fx=0.16,
    ),
}


def leg_cashflows(t: float, coupon: float, maturity: int, principal: bool):
    """t 이후 남은 (지급시점까지 남은 기간, 금액). 명목 1, 연 1회 지급."""
    pay = np.arange(1, maturity + 1, dtype=float)
    pay = pay[pay > t + 1e-9]
    amounts = np.full(len(pay), coupon)
    if principal and len(pay):
        amounts[-1] += 1.0
    return pay - t, amounts


def vertex_pv01(curve, t: float, tau: np.ndarray, amounts: np.ndarray) -> np.ndarray:
    """받는 다리 기준 vertex별 PV01 (zero 금리 +1bp 시 가치 변화, 음수)."""
    pv = amounts * curve.df(t + tau) / curve.df(t)
    dv = -tau * pv * 1e-4
    out = np.zeros(len(VERTICES))
    for x, d in zip(tau, dv):
        j = int(np.searchsorted(VERTICES, x))
        if j == 0:
            out[0] += d
        elif j >= len(VERTICES):
            out[-1] += d
        else:
            w = (VERTICES[j] - x) / (VERTICES[j] - VERTICES[j - 1])
            out[j - 1] += w * d
            out[j] += (1 - w) * d
    return out


def _currency_margin(s: np.ndarray, rw: np.ndarray, p: dict) -> tuple[float, float]:
    """통화 하나의 금리 delta (K_b, S_b). 베이시스 민감도는 그 통화 PV01 합계."""
    ws = rw * s
    ws_basis = p["rw_basis"] * s.sum()
    k2 = ws @ p["tenor_corr"] @ ws + ws_basis**2 + 2 * p["rho_basis"] * ws_basis * ws.sum()
    k = np.sqrt(max(k2, 0.0))
    return k, float(np.clip(ws.sum() + ws_basis, -k, k))


def simm(t: float, market, c_jpy: float, c_krw: float, maturity: int, fx: float,
         version: str = "2.8+2512", calc_ccy: str = "USD", principal: bool = False) -> dict:
    """t 시점 IM (원화 명목 1 기준). 귀사는 딜러 B로부터 JPY를 받고 KRW를 준다.

    fx: t 시점 JPYKRW / 현재 JPYKRW (JPY 다리를 원화로 바꾸는 배수)
    principal: False면 원금 교환분 제외 (비청산 장외파생 증거금 기준상 통화스왑 원금 교환은 IM 대상 아님)
    """
    p = PARAMS[version]
    tau_j, cf_j = leg_cashflows(t, c_jpy, maturity, principal)
    tau_k, cf_k = leg_cashflows(t, c_krw, maturity, principal)
    if len(tau_j) == 0:
        return dict(im=0.0, ir=0.0, fx=0.0, k_jpy=0.0, k_krw=0.0)

    s_jpy = fx * vertex_pv01(market.jpy, t, tau_j, cf_j)       # JPY 수취
    s_krw = -vertex_pv01(market.krw, t, tau_k, cf_k)           # KRW 지급
    k_j, s_j = _currency_margin(s_jpy, np.array(p["rw_low"]), p)
    k_k, s_k = _currency_margin(s_krw, np.array(p["rw_regular"]), p)
    ir = np.sqrt(k_j**2 + k_k**2 + 2 * p["gamma"] * s_j * s_k)

    pv_j = fx * np.sum(cf_j * market.jpy.df(t + tau_j) / market.jpy.df(t))
    pv_k = np.sum(cf_k * market.krw.df(t + tau_k) / market.krw.df(t))
    if calc_ccy == "USD":   # JPY/USD, KRW/USD 두 위험요인
        ws = p["rw_fx"] * 0.01 * np.array([pv_j, -pv_k])
        fx_margin = np.sqrt(ws @ ws + 2 * p["rho_fx"] * ws[0] * ws[1])
    else:                   # 계산통화 KRW: JPY/KRW 하나
        fx_margin = p["rw_fx"] * 0.01 * abs(pv_j)

    im = np.sqrt(ir**2 + fx_margin**2 + 2 * p["psi_ir_fx"] * ir * fx_margin)
    return dict(im=float(im), ir=float(ir), fx=float(fx_margin), k_jpy=float(k_j), k_krw=float(k_k))
