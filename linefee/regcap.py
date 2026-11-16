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
             E = 잔존만기, 감독 델타 |1|. 거래 1건이라 헤징셋·만기 버킷 간 상계 없음
    end: 마지막 지급 시점 (MarketData.end_time)
    """
    if trade.product == "IRS":
        leg = trade.legs[0]
        remaining = np.maximum(end - times, 0.0)
        sd = (1 - np.exp(-0.05 * remaining)) / 0.05
        return IR_SF * leg.notional * fx_paths[leg.ccy] * sd[None, :]
    return FX_SF * fx_adjusted_notional(trade, fx_paths)


def sa_ccr_ead(value: np.ndarray, trade: Trade, times: np.ndarray, fx_paths: dict,
               csa: CSA, im_received: np.ndarray, end: float) -> np.ndarray:
    """(경로 x 시점) EAD (원화). 경로별 RC·승수로 계산한다 (기대 EAD는 경로 평균).

    마지막 지급 시점 t = end에서는 결제 직전 가치를 쓴다: 무담보는 RC만 남고(MF = 0) CVA는 M = 0으로 0.
    담보 상계 집합은 RC = 0이고 add-on도 0으로 둔다(EAD 0).
    무담보: RC = max(V, 0), MF = sqrt(min(잔존만기, 1년))
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
        mf = np.sqrt(np.minimum(remaining, 1.0))
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
    BA-CVA(축약형, 헤지 없음): K = DS x RW_cva / alpha x M x DF(M) x EAD, M = 잔존만기(상한 없음)
          은행 CVA 포트폴리오 대비 증분 = K x 한계계수. 필요자본 = K x 12.5 x 목표비율
    """
    m = np.maximum(end - times, 0.0)
    dfm = np.where(m > 0, (1 - np.exp(-0.05 * m)) / np.maximum(0.05 * m, 1e-12), 0.0)
    k_cva = p.ba_cva_ds * cp.cva_rw / p.ba_cva_alpha * m * dfm * ead * p.ba_cva_marginal_factor
    return {
        "ead": ead,
        "ccr": ead * cp.credit_rw * p.capital_ratio,
        "cva": k_cva * 12.5 * p.capital_ratio,
        "rwa_ccr": ead * cp.credit_rw,
        "rwa_cva": k_cva * 12.5,
    }


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
    """시점별 IM. 연 단위(및 0, 만기 직전)로 계산하고 선형 보간한다."""
    end = m.end_time(trade)
    grid = np.unique(np.concatenate([np.arange(0, end, 1.0), [max(end - 1e-6, 0.0)]]))
    vals = np.array([simm_im(trade, t, m, version) for t in grid])
    out = np.interp(times, grid, vals)
    return np.where(times < end - 1e-9, out, 0.0)


# ---------------------------------------------------------------------------
# CCP IM (은행 일방 납부): 보유기간 VaR 근사
# ---------------------------------------------------------------------------
def ccp_im_profile(trade: Trade, times: np.ndarray, m: MarketData, sigma: float,
                   mpor_days: int = 5, conf: float = 0.99) -> np.ndarray:
    """시점별 CCP IM (원화) = z(conf) x sigma x sqrt(mpor / 250) x |PV01 (금리 1 = 100% 기준)|.

    sigma: 해당 통화 금리 평행이동 연 변동성 (정규, 과거 추정).
    PV01: 두 다리의 순 PV01. 현금흐름은 원금 제외, 오늘 커브의 선도로 평가하고(변동 다리는 다음 리셋일에
          명목 + 확정 쿠폰, 만기에 -명목으로 바꿔 놓음) 해당 다리 할인 커브 평행이동으로 계산한다.
    실제 CCP 모형(과거 시나리오 VaR 등)의 근사이며 반기 간격으로 계산해 선형 보간한다.
    """
    z = NormalDist().inv_cdf(conf)
    end = m.end_time(trade)
    grid = np.unique(np.concatenate([np.arange(0, end, 0.5), [max(end - 1e-6, 0.0)]]))
    vals = []
    for t in grid:
        dv = 0.0
        for leg in trade.legs:
            tau, amt = _flows_for_simm(leg, trade, t, m)
            if len(tau) == 0:
                continue
            disc = m.disc_for(leg)
            pv = amt * disc.df(t + tau) / disc.df(t)
            dv += leg.sign * float(m.fwd_fx(leg.ccy, t)) * float(np.sum(tau * pv))
        vals.append(z * sigma * np.sqrt(mpor_days / 250) * abs(dv))
    out = np.interp(times, grid, vals)
    return np.where(times < end - 1e-9, out, 0.0)
