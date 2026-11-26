"""규제자본(SA-CCR, 신용 RWA, BA-CVA)과 증거금(SIMM) — 거래 1건, 상계 집합 단독.

근거: 은행업감독업무시행세칙 별표3 (SA-CCR 266~267, CVA 306·313), ISDA SIMM.
"""
from statistics import NormalDist

import numpy as np

from simm import PARAMS, VERTICES, vertex_pv01

from .market import MarketData
from .trade import CSA, BankParams, Counterparty, Trade

FX_SF = 0.04   # SA-CCR 환율 감독요인
IR_SF = 0.005  # SA-CCR 금리 감독요인
MIN_HOLD = 10 / 250   # 10영업일 (년). 코드의 MPOR 환산(영업일 / 250)과 같은 관행


def floor_remaining(remaining: np.ndarray) -> np.ndarray:
    """잔존만기에 10영업일 하한. 만기 시점 노드(잔존 0)는 그대로 0으로 둔다 (add-on 0, RC만 남김)."""
    return np.where(remaining > 1e-9, np.maximum(remaining, MIN_HOLD), 0.0)


# ---------------------------------------------------------------------------
# SA-CCR
# ---------------------------------------------------------------------------
def fx_adjusted_notional(trade: Trade, fx_paths: dict) -> np.ndarray:
    """(경로 x 시점) 환율 자산군 조정명목, 원화.

    KRW 다리가 있으면 외화 다리 명목을 그 시점 환율로 환산. 두 다리 모두 외화면 큰 쪽 (CRE52.43).
    통화스왑은 환율 자산군 하나로만 분류한다 (금리 다리 제외, 확정 사항).
    """
    foreign = [l for l in trade.legs if l.ccy != "KRW"]
    if not foreign:
        return np.zeros_like(fx_paths["KRW"])
    return np.max([l.notional * fx_paths[l.ccy] for l in foreign], axis=0)


def addon_before_mf(trade: Trade, times: np.ndarray, fx_paths: dict, end: float) -> np.ndarray:
    """(경로 x 시점) 만기계수 적용 전 add-on, 원화.

    통화스왑·FX 선도: 환율 자산군, 4% x 조정명목
    IRS·OIS: 금리 자산군, 0.5% x 명목(원화) x 감독 듀레이션 SD = (1 - e^{-0.05 E}) / 0.05,
             E = 잔존만기 (하한 10영업일, 267.자.(1). 만기 시점 노드는 0), 감독 델타 |1|. 거래 1건이라 헤징셋·만기 버킷 간 상계 없음
    end: 마지막 지급 시점 (MarketData.end_time)
    """
    if trade.product == "IRS":
        leg = trade.legs[0]
        remaining = np.maximum(end - times, 0.0)
        sd = (1 - np.exp(-0.05 * floor_remaining(remaining))) / 0.05
        return IR_SF * leg.notional * fx_paths[leg.ccy] * sd[None, :]
    return FX_SF * fx_adjusted_notional(trade, fx_paths)


def sa_ccr_ead(value: np.ndarray, trade: Trade, times: np.ndarray, fx_paths: dict,
               csa: CSA, im_received: np.ndarray, end: float) -> np.ndarray:
    """(경로 x 시점) EAD (원화). 경로별 RC·승수로 계산한다 (기대 EAD는 경로 평균).

    마지막 지급 시점 t = end에서는 결제 직전 가치를 쓴다: 무담보는 RC만 남고(MF = 0) CVA는 M = 0으로 0.
    담보 상계 집합은 RC = 0이고 add-on도 0으로 둔다(EAD 0).
    무담보: RC = max(V, 0), MF = sqrt(min(잔존만기, 1년)), 잔존만기 하한 10영업일 (267.카.(1)). 만기 시점 노드는 MF = 0
    담보(VM, 임계치 0, 정산 지연 0): C = V → RC = max(-NICA, TH + MTA - NICA, 0) = 0,
          MF = 1.5 x sqrt(MPOR / 250)
    승수 = min(1, 0.05 + 0.95 x exp((V - C - NICA) / (2 x 0.95 x AddOn)))
    EAD = 1.4 x (RC + 승수 x AddOn)
    """
    remaining = np.maximum(end - times, 0.0)
    alive = times <= end + 1e-9    # 마지막 지급 시점까지 (그 시점 가치 = 결제 직전, MF·잔존만기 0)
    base = addon_before_mf(trade, times, fx_paths, end)
    if csa.vm:
        # 마지막 지급 시점에는 남은 위험이 없다(담보 = 결제 직전 가치, MPOR 동안 남는 거래 없음) → add-on 0
        mf = np.where(remaining > 1e-9, 1.5 * np.sqrt(csa.reg_mpor_days / 250), 0.0)
        nica = im_received * (1 - csa.im_haircut) if csa.im else np.zeros_like(times)
        addon = base * mf[None, :]
        excess = -np.broadcast_to(nica[None, :], addon.shape)
        rc = np.zeros_like(addon)
    else:
        mf = np.sqrt(np.minimum(floor_remaining(remaining), 1.0))
        addon = base * mf[None, :]
        excess = value
        rc = np.maximum(value, 0.0)
    mult = np.minimum(1.0, 0.05 + 0.95 * np.exp(np.minimum(excess / np.maximum(1.9 * addon, 1e-9), 0.0)))
    ead = 1.4 * (rc + mult * addon)
    return np.where(alive[None, :], ead, 0.0)


def capital_paths(ead: np.ndarray, cp: Counterparty, times: np.ndarray, p: BankParams,
                  end: float) -> dict:
    """필요자본 (원화, CET1 목표비율 기준). ead는 (시점) 또는 (경로 x 시점).

    신용: EAD x RW x 목표비율
    BA-CVA(축약형, 헤지 없음): K = DS x RW_cva / alpha x M x DF(M) x EAD, M = max(잔존만기, 10영업일)(상한 없음)
          은행 CVA 포트폴리오 대비 증분 = K x 한계계수. 필요자본 = K x 12.5 x 목표비율
    """
    # M 하한 10영업일: 126.다(상계계약 하 장외파생상품의 하한은 최소보유기간 10영업일), 5년 상한 해제는 313.(3).
    # 126.가의 1년 하한은 쓰지 않는다 (상계계약 하 장외파생이라 126.다가 적용). 만기 시점 노드(잔존 0)는 M = 0 (RC만 남김).
    m = floor_remaining(np.maximum(end - times, 0.0))
    dfm = np.where(m > 0, (1 - np.exp(-0.05 * m)) / np.maximum(0.05 * m, 1e-12), 0.0)
    k_cva = p.ba_cva_ds * cp.cva_rw / p.ba_cva_alpha * m * dfm * ead * p.ba_cva_marginal_factor
    rw = credit_rw_profile(cp, m)
    return {
        "ead": ead,
        "ccr": ead * rw * p.capital_ratio,
        "cva": k_cva * 12.5 * p.capital_ratio,
        "rwa_ccr": ead * rw,
        "rwa_cva": k_cva * 12.5,
    }


# ---------------------------------------------------------------------------
# 기본내부등급법 (기업 익스포저, 별표3 120·123·124·126)
# ---------------------------------------------------------------------------
IRB_PD_FLOOR = 0.0005     # 123: 기업 PD 하한 0.05%
_N = NormalDist()


def irb_rw(pd: float, lgd: float, m, sales_eok: float | None = None):
    """기업 익스포저 RW (소수, 12.5 x K). m은 유효만기(년, 스칼라 또는 배열), 1.06 승수 없음(2020 폐지).

    R = 0.12 w + 0.24 (1 - w), w = (1 - e^(-50 PD)) / (1 - e^(-50))
    중소기업(매출 700억 이하): R - 0.04 x (1 - (S - 7) / 63), S = 매출(10억원 단위, 70억 미만은 7)
    K = [LGD N((G(PD) + sqrt(R) G(0.999)) / sqrt(1 - R)) - PD LGD] x (1 + (M - 2.5) b) / (1 - 1.5 b),
    b = (0.11852 - 0.05478 ln PD)^2. PD 100%(부도)는 K = 0 (기본내부등급법은 EL_best = LGD로 봄).
    """
    if pd >= 1.0:
        return np.zeros_like(np.asarray(m, dtype=float))
    pd = max(pd, IRB_PD_FLOOR)
    w = (1 - np.exp(-50 * pd)) / (1 - np.exp(-50))
    r = 0.12 * w + 0.24 * (1 - w)
    if sales_eok is not None and sales_eok <= 700:
        s = max(sales_eok, 70) / 10
        r -= 0.04 * (1 - (s - 7) / 63)
    b = (0.11852 - 0.05478 * np.log(pd)) ** 2
    cond = _N.cdf((_N.inv_cdf(pd) + np.sqrt(r) * _N.inv_cdf(0.999)) / np.sqrt(1 - r))
    k0 = lgd * cond - pd * lgd
    return 12.5 * k0 * (1 + (np.asarray(m, dtype=float) - 2.5) * b) / (1 - 1.5 * b)


def credit_rw_profile(cp: Counterparty, m: np.ndarray):
    """시점별 신용 RW. 고정 RW면 스칼라, 기본내부등급법이면 유효만기 M(t)에 따른 배열.

    유효만기: 126.다(상계계약 하 장외파생) 하한 10영업일(잔존 0 노드는 RC만 남으므로 아무 값이나 무방), 126.가 상한 5년.
    """
    if not cp.irb:
        return cp.credit_rw
    return irb_rw(cp.pd, cp.irb_lgd, np.clip(np.maximum(m, MIN_HOLD), MIN_HOLD, 5.0), cp.sales_eok)


# ---------------------------------------------------------------------------
# SIMM (RatesFX delta, 계산통화 USD, 단독, 임계치 없음)
# ---------------------------------------------------------------------------
LOW_VOL = {"JPY"}


def _flows_for_simm(leg, trade: Trade, t: float, m: MarketData) -> tuple[np.ndarray, np.ndarray]:
    """t 이후 (남은 기간, 금액) — 원금 교환 제외 (통화스왑 원금·FX 선도는 IM 대상 아님).

    변동 다리는 다음 지급일에 (명목 + 확정 쿠폰), 만기에 -명목, 스프레드는 각 지급일로 바꿔 놓는다.
    """
    sc = m.schedule(trade, leg)
    pay, start, accr_all = sc.pay, sc.start, sc.accr
    live = pay > t + 1e-9
    if not live.any():
        return np.array([]), np.array([])
    p_i, s_i, accr = pay[live], start[live], accr_all[live]
    tau = p_i - t
    if leg.kind == "fixed":
        return tau, leg.notional * leg.rate * accr
    proj = m.proj[leg.ccy]
    amt = leg.notional * leg.rate * accr
    first_fix = proj.df(s_i[0]) / proj.df(p_i[0]) - 1 if s_i[0] <= t + 1e-9 else 0.0
    amt = amt.astype(float)
    if s_i[0] <= t + 1e-9:          # 다음 지급일에 명목 + 확정 쿠폰
        amt[0] += leg.notional * (1 + first_fix)
    else:                           # 아직 시작 전: 시작일에 명목
        tau = np.concatenate([[s_i[0] - t], tau])
        amt = np.concatenate([[leg.notional], amt])
    amt[-1] -= leg.notional
    return tau, amt


def simm_im(trade: Trade, t: float, m: MarketData, version: str) -> float:
    """t 시점 SIMM IM (원화). 오늘 커브의 선도와 선도환율로 남은 현금흐름을 평가한다."""
    if trade.product == "FXFWD":
        return 0.0
    prm = PARAMS[version]
    k_b, s_b, fx_pv = {}, {}, {}
    for leg in trade.legs:
        tau, amt = _flows_for_simm(leg, trade, t, m)
        if len(tau) == 0:
            continue
        fx = float(m.fwd_fx(leg.ccy, t))
        disc = m.disc_for(leg)
        sens = leg.sign * fx * vertex_pv01(disc, t, tau, amt)
        k_b[leg.ccy] = k_b.get(leg.ccy, 0) + sens
        pv = leg.sign * fx * float(np.sum(amt * disc.df(t + tau) / disc.df(t)))
        fx_pv[leg.ccy] = fx_pv.get(leg.ccy, 0) + pv
    if not k_b:
        return 0.0
    kk, ss = [], []
    for ccy, s in k_b.items():
        rw = np.array(prm["rw_low"] if ccy in LOW_VOL else prm["rw_regular"])
        ws = rw * s
        k2 = ws @ prm["tenor_corr"] @ ws
        ws_sum = ws.sum()
        if ccy != "USD":   # 통화간 베이시스 (USD 대비)
            wb = prm["rw_basis"] * s.sum()
            k2 += wb**2 + 2 * prm["rho_basis"] * wb * ws_sum
            ws_sum += wb
        k = np.sqrt(max(k2, 0.0))
        kk.append(k)
        ss.append(np.clip(ws_sum, -k, k))
    kk, ss = np.array(kk), np.array(ss)
    ir2 = (kk**2).sum() + prm["gamma"] * (ss.sum() ** 2 - (ss**2).sum())
    ir = np.sqrt(max(ir2, 0.0))
    ws_fx = np.array([prm["rw_fx"] * 0.01 * v for c, v in fx_pv.items() if c != "USD"])
    fx2 = (ws_fx**2).sum() + prm["rho_fx"] * (ws_fx.sum() ** 2 - (ws_fx**2).sum())
    fxm = np.sqrt(max(fx2, 0.0))
    return float(np.sqrt(ir**2 + fxm**2 + 2 * prm["psi_ir_fx"] * ir * fxm))


def im_profile(trade: Trade, times: np.ndarray, m: MarketData, version: str) -> np.ndarray:
    """시점별 IM. 시뮬레이션 노드마다 직접 계산한다 (마지막 지급 시점부터 0).

    예전에는 연 단위 격자에서 구해 선형 보간했는데, 연 지급 거래는 지급일이 격자 바로 뒤라 격자 값이 모두 지급 직전
    (남은 쿠폰 포함) IM이 되어 IM이 체계적으로 과대했다(지급 직후 계단 하락을 놓침, Run C L-038). 노드에서 직접 계산하면 보간 편향이 없다.
    """
    end = m.end_time(trade)
    return np.array([simm_im(trade, float(t), m, version) if t < end - 1e-9 else 0.0 for t in times])


# ---------------------------------------------------------------------------
# CCP IM (은행 일방 납부): 보유기간 VaR 근사
# ---------------------------------------------------------------------------
def ccp_im_profile(trade: Trade, times: np.ndarray, m: MarketData, sigma: float,
                   mpor_days: int = 5, conf: float = 0.99) -> np.ndarray:
    """시점별 CCP IM (원화) = z(conf) x sigma x sqrt(mpor / 250) x |PV01 (금리 1 = 100% 기준)|.

    sigma: 해당 통화 금리 평행이동 연 변동성 (정규, 과거 추정).
    PV01: 두 다리의 순 PV01. 현금흐름은 원금 제외, 오늘 커브의 선도로 평가하고(변동 다리는 다음 리셋일에
          명목 + 확정 쿠폰, 만기에 -명목으로 바꿔 놓음) 해당 다리 할인 커브 평행이동으로 계산한다.
    실제 CCP 모형(과거 시나리오 VaR 등)의 근사이며 시뮬레이션 노드마다 직접 계산한다 (보간 편향 없음, im_profile과 같은 이유).
    """
    z = NormalDist().inv_cdf(conf)
    end = m.end_time(trade)
    vals = []
    for t in times:
        if t >= end - 1e-9:
            vals.append(0.0)
            continue
        dv = 0.0
        for leg in trade.legs:
            tau, amt = _flows_for_simm(leg, trade, t, m)
            if len(tau) == 0:
                continue
            disc = m.disc_for(leg)
            pv = amt * disc.df(t + tau) / disc.df(t)
            dv += leg.sign * float(m.fwd_fx(leg.ccy, t)) * float(np.sum(tau * pv))
        vals.append(z * sigma * np.sqrt(mpor_days / 250) * abs(dv))
    return np.array(vals)
