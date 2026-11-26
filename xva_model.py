"""구버전(엔/원 검토 당시), 회귀 비교 전용. 현재 방법은 `linefee/`, `docs/METHODOLOGY_v1.md`. KVA 조달금리 등이 확정 결정(DECISIONS M5)과 다르다.

30년 JPY/KRW 고정-고정 CCS 백투백 중개의 라인 피(XVA) 계산.

구조
    공공기관 A  <-- (귀사: KRW 수취, JPY 지급) -->  귀사  <-- (반대) -->  딜러 B
    - 공공기관 A: CSA 없음, break 없음, 자본 부과 없음, 귀사가 받는 수수료 없음
    - 딜러 B: CSA 있음(threshold 0, 정산 지연 없음), 30년 bullet 또는 10년 mutual break,
              귀사에 upfront 지급 (이 거래의 수익원)

모든 금액은 '원화 명목 1' 기준 비율로 계산하고, 마지막에 명목금액을 곱한다.
환율 S는 1엔당 원화를 시작값 1로 정규화했다 (엔화 명목 = 원화 명목 / S0).

시장 데이터 (market_data.py, 블룸버그 스냅샷)
    - KRW·JPY 할인커브는 USD 담보 기준 (KRW CCS vs SOFR, JPY OIS + TONA/SOFR 베이시스)
    - 양쪽 쿠폰은 각 커브의 30년 파 금리 (원금 교환 포함 양 다리 모두 액면가)
    - 환율은 선도환율을 평균으로 하는 로그정규, 변동성은 JPYKRW ATM 내재변동성 기간구조

단순화한 부분 (실제 가격 산정 전에 사내 XVA 시스템으로 검증할 것)
    - 금리는 결정론적(오늘 커브의 선도)이고 환율만 움직인다
    - 금리 변동성, wrong-way risk는 반영하지 않는다
"""
from dataclasses import dataclass, field

import numpy as np

from assumptions import Assumptions
from simm import simm

NORMAL_PDF_AT_0 = 0.3989  # E[max(Z,0)] for Z ~ N(0,1)


# ---------------------------------------------------------------------------
# 1. 스왑 가치와 익스포저
# ---------------------------------------------------------------------------
def coupons(a: Assumptions) -> tuple[float, float]:
    """(JPY 쿠폰, KRW 쿠폰)을 연 지급액(명목 대비)으로. 지정하지 않으면 각 커브의 만기 파 금리.

    원화가 ACT/360이면 1년 지급액은 금리 x 365/360 (윤년 366/360). 평균 365.25/360을 쓴다.
    """
    m = a.market
    c_jpy = a.coupon_jpy if a.coupon_jpy is not None else m.jpy.par_rate(a.maturity_years)
    if a.coupon_krw is None:
        c_krw = m.krw.par_rate(a.maturity_years)
    else:
        c_krw = a.coupon_krw * (365.25 / 360 if a.krw_day_count == "ACT/360" else 1.0)
    if a.fee_structure == "running_dealer_b":   # 수수료를 딜러 B 다리에서 뺀다
        c_krw -= _spread_annual(a)
    return c_jpy, c_krw


def _spread_annual(a: Assumptions) -> float:
    return a.hf_krw_spread_bp / 1e4 * (365.25 / 360 if a.krw_day_count == "ACT/360" else 1.0)


def hf_coupon_krw(a: Assumptions) -> float:
    """공공기관 A가 귀사에 내는 원화 쿠폰 (연 지급액) = 딜러 B 쿠폰 + 수수료 (running 계열)."""
    c_krw = coupons(a)[1]
    if a.fee_structure not in ("running", "running_dealer_b"):
        return c_krw
    return c_krw + _spread_annual(a)


def forward_fx_ratio(a: Assumptions, t) -> np.ndarray:
    """선도환율 / 현물환율 = DF_JPY(t) / DF_KRW(t)."""
    return a.market.jpy.df(t) / a.market.krw.df(t)


def remaining_leg_pv(t: float, curve, coupon: float, maturity: int) -> float:
    """t 시점에 남은 고정금리 다리(쿠폰 + 만기 원금)의 t 시점 가치, 명목 1 기준."""
    if t >= maturity:
        return 0.0
    pay_times = np.arange(1, maturity + 1, dtype=float)
    pay_times = pay_times[pay_times > t + 1e-9]
    dfs = curve.df(pay_times) / curve.df(t)
    return float(coupon * dfs.sum() + dfs[-1])


@dataclass
class Exposure:
    times: np.ndarray        # 시간 격자
    value_vs_hf: np.ndarray  # (경로 x 시점) 귀사 입장에서 본 공공기관 A 거래의 가치
    epe_hf: np.ndarray       # 공공기관 A가 귀사에 갚을 돈의 기대값 (익스포저)
    ene_hf: np.ndarray       # 귀사가 공공기관 A에 갚을 돈의 기대값 (음수)
    vm_posted: np.ndarray    # 귀사가 딜러 B에 낼 VM 기대값 (딜러 B 거래 기준)
    vm_received: np.ndarray  # 딜러 B에서 받을 VM 기대값 (양수)
    fwd_fx: np.ndarray       # 선도환율 / 현물환율
    pfe_hf: dict = field(default_factory=dict)  # {신뢰수준: 시점별 PFE}
    epe_dealer_b: np.ndarray = None  # CSA 하에서 딜러 B에 남는 익스포저


def rate_params(a: Assumptions) -> tuple[float, float, np.ndarray]:
    """(KRW 금리 변동성, JPY 금리 변동성, [FX, KRW, JPY] 상관행렬). 금리 요인을 끄면 변동성 0."""
    h = a.market.rate_hist
    if not a.rate_factors or not h:
        return 0.0, 0.0, np.eye(3)
    corr = np.array([
        [1.0, h["corr_fx_krw"], h["corr_fx_jpy"]],
        [h["corr_fx_krw"], 1.0, h["corr_krw_jpy"]],
        [h["corr_fx_jpy"], h["corr_krw_jpy"], 1.0],
    ])
    if a.rate_corr_override is not None:
        corr = np.array(a.rate_corr_override)
    return h["sigma_krw"] * a.rate_vol_scale, h["sigma_jpy"] * a.rate_vol_scale, corr


def _ou_paths(z: np.ndarray, sigma: float, kappa: float, dt: float) -> tuple[np.ndarray, np.ndarray]:
    """평행이동 요인 X(t) 경로와 시점별 분산. kappa = 0이면 브라운 운동 (Ho-Lee)."""
    paths, steps = z.shape
    decay = np.exp(-kappa * dt)
    step_sd = sigma * (np.sqrt((1 - decay**2) / (2 * kappa)) if kappa > 0 else np.sqrt(dt))
    x = np.zeros((paths, steps + 1))
    for i in range(steps):
        x[:, i + 1] = x[:, i] * decay + step_sd * z[:, i]
    t = np.arange(steps + 1) * dt
    var = sigma**2 * ((1 - np.exp(-2 * kappa * t)) / (2 * kappa) if kappa > 0 else t)
    return x, var


def _leg_pv_paths(t: float, curve, coupon: float, maturity: int, x: np.ndarray,
                  var_x: float, kappa: float) -> np.ndarray:
    """t 시점 남은 고정금리 다리 가치 (경로별). 커브 = 오늘 선도커브를 X(t)만큼 이동.

    P(t,T) = P(0,T)/P(0,t) x exp(-B(tau) X - B(tau)^2 Var[X] / 2), B(tau) = tau (kappa=0)
    → 경로 평균이 오늘 선도 할인계수와 같아진다.
    """
    if t >= maturity:
        return np.zeros_like(x)
    pay = np.arange(1, maturity + 1, dtype=float)
    pay = pay[pay > t + 1e-9]
    tau = pay - t
    b = (1 - np.exp(-kappa * tau)) / kappa if kappa > 0 else tau
    fwd_df = curve.df(pay) / curve.df(t)
    cf = np.full(len(pay), coupon)
    cf[-1] += 1.0
    dfs = fwd_df[None, :] * np.exp(-np.outer(x, b) - 0.5 * b**2 * var_x)
    return dfs @ cf


def simulate_exposure(a: Assumptions) -> Exposure:
    """환율 + KRW 금리 + JPY 금리 3요인 시뮬레이션.

    환율: S(t) = F(t) x exp(W(t) - V(t)/2), V(t) = 만기 t 내재변동성^2 x t
    금리: 통화별 평행이동 요인 X(t) 하나 (정규, 기본은 평균회귀 없음). 변동성과 상관은
          과거 5년 주간 변화 추정치 (market_data.fetch_hist). 측도 변경(quanto) 보정은 하지 않는다.
    """
    rng = np.random.default_rng(a.seed)
    times = np.arange(0, a.maturity_years + 1e-9, a.dt)
    steps = len(times) - 1

    sig_k, sig_j, corr = rate_params(a)
    z = rng.standard_normal((a.n_paths, steps, 3)) @ np.linalg.cholesky(corr).T

    total_var = np.array([a.market.fx_vol(max(t, 1e-6)) ** 2 * t for t in times])
    step_sd = np.sqrt(np.maximum(np.diff(total_var), 0.0))
    w = np.hstack([np.zeros((a.n_paths, 1)), np.cumsum(z[:, :, 0] * step_sd, axis=1)])
    fwd = forward_fx_ratio(a, times)
    fx = fwd[None, :] * np.exp(w - 0.5 * total_var[None, :])

    kappa = a.rate_mean_reversion
    x_k, var_k = _ou_paths(z[:, :, 1], sig_k, kappa, a.dt)
    x_j, var_j = _ou_paths(z[:, :, 2], sig_j, kappa, a.dt)

    c_jpy, c_krw = coupons(a)
    c_krw_hf = hf_coupon_krw(a)
    krw_leg = np.column_stack([
        _leg_pv_paths(t, a.market.krw, c_krw_hf, a.maturity_years, x_k[:, i], var_k[i], kappa)
        for i, t in enumerate(times)])
    krw_leg_dealer_b = krw_leg if c_krw_hf == c_krw else np.column_stack([
        _leg_pv_paths(t, a.market.krw, c_krw, a.maturity_years, x_k[:, i], var_k[i], kappa)
        for i, t in enumerate(times)])
    jpy_leg = np.column_stack([
        _leg_pv_paths(t, a.market.jpy, c_jpy, a.maturity_years, x_j[:, i], var_j[i], kappa)
        for i, t in enumerate(times)])

    # 귀사 vs 공공기관 A: 기본은 KRW 다리를 받고 JPY 다리를 준다. hf_reverse면 반대
    sign = -1.0 if a.hf_reverse else 1.0
    value_vs_hf = sign * (krw_leg - fx * jpy_leg)
    positive = np.maximum(value_vs_hf, 0)
    # 딜러 B 거래는 공공기관 A 거래의 반대이고 원화 쿠폰만 수수료만큼 낮다. 딜러 B 시가가 양수면 귀사가 VM을 낸다
    dealer_b_mtm = sign * (krw_leg_dealer_b - fx * jpy_leg)

    # 딜러 B와는 정반대 거래이고 CSA로 MTM만큼 담보가 오가므로,
    # 남는 익스포저는 정산 지연 기간 동안의 변동분뿐이다. 지연이 0이면 0.
    lag_years = a.settlement_lag_days / 250
    jpy_leg_in_krw = (fx * jpy_leg).mean(axis=0)
    epe_dealer_b = NORMAL_PDF_AT_0 * a.market.fx_vol(1.0) * np.sqrt(lag_years) * jpy_leg_in_krw

    return Exposure(
        times=times,
        value_vs_hf=value_vs_hf,
        epe_hf=positive.mean(axis=0),
        ene_hf=np.minimum(value_vs_hf, 0).mean(axis=0),
        vm_posted=np.maximum(dealer_b_mtm, 0).mean(axis=0),
        vm_received=np.maximum(-dealer_b_mtm, 0).mean(axis=0),
        fwd_fx=fwd,
        pfe_hf={q: np.quantile(positive, q, axis=0) for q in a.pfe_quantiles},
        epe_dealer_b=epe_dealer_b,
    )


# ---------------------------------------------------------------------------
# 2. CVA / FVA
# ---------------------------------------------------------------------------
def discount(a: Assumptions, t) -> np.ndarray:
    """KRW 할인계수 (USD 담보 기준 KRW 커브)."""
    return a.market.krw.df(t)


def cva(a: Assumptions, times: np.ndarray, epe: np.ndarray, cds: float) -> float:
    """CVA = LGD x sum( EPE(t) x DF(t) x 해당 구간 부도확률 ). 해저드율 = CDS / LGD."""
    hazard = cds / a.lgd
    survival = np.exp(-hazard * times)
    default_prob = survival[:-1] - survival[1:]
    return a.lgd * np.sum(epe[1:] * discount(a, times[1:]) * default_prob)


def funding_cost(a: Assumptions, ex: Exposure) -> tuple[float, float]:
    """(FCA, FBA). 딜러 B CSA 담보는 USD 현금.

    공공기관 A가 귀사에 빚진 상태(EPE_hf)면 귀사는 딜러 B에 같은 금액을 USD 현금으로 내야 하는데
    공공기관 A에서는 담보를 받지 못하므로 그만큼 USD를 조달해야 한다 → FCA.
    반대로 딜러 B에서 받은 USD 현금으로 다른 USD 조달을 대체하면 이익이 생긴다 → FBA.
    """
    t = ex.times[1:]
    df = discount(a, t)
    fca = np.sum(ex.vm_posted[1:] * df * a.usd_funding_spread * a.dt)
    fba = np.sum(ex.vm_received[1:] * df * a.usd_benefit_spread * a.dt)
    return fca, fba


# ---------------------------------------------------------------------------
# 3. 규제자본: SA-CCR EAD, CVA 자본, KVA
# ---------------------------------------------------------------------------
def supervisory_duration(remaining: np.ndarray) -> np.ndarray:
    """SA-CCR supervisory duration, 시작일이 0인 경우: (1 - exp(-0.05 E)) / 0.05."""
    return (1 - np.exp(-0.05 * remaining)) / 0.05


def sa_ccr_addon(remaining: np.ndarray, treatment: str = "fx_only") -> np.ndarray:
    """고정-고정 CCS의 SA-CCR add-on, 명목 1 기준, maturity factor 적용 전.

    시행세칙 별표3 267.파 감독요인:
    - FX: 4% x 명목 (통화짝 JPY/KRW 헤지군 하나)
    - 금리: 0.5% x 명목 x 감독 듀레이션, 통화별 헤지군 (267.다.(1))
    267.나: 거래는 주된 위험요인 기준으로 자산군 하나에 분류한다. 금리까지 넣을지는 해석 문제.
    """
    fx_addon = np.full_like(remaining, 0.04, dtype=float)
    if treatment == "fx_and_ir":
        fx_addon = fx_addon + 2 * 0.005 * supervisory_duration(remaining)
    return np.where(remaining > 0, fx_addon, 0.0)


def effective_maturity(t: float, a: Assumptions) -> float:
    """BA-CVA용 유효만기 M (5년 상한 없음).

    "cashflow": 남은 KRW 다리 현금흐름의 가중평균 만기 (CRE32.47 방식)
    "residual": 잔존만기 그대로 (CRE32.48을 보수적으로 해석한 경우)
    """
    if a.ba_cva_maturity == "residual":
        return max(a.maturity_years - t, 0.0)
    pay_times = np.arange(1, a.maturity_years + 1)
    pay_times = pay_times[pay_times > t + 1e-9] - t
    if len(pay_times) == 0:
        return 0.0
    cash_flows = np.full(len(pay_times), coupons(a)[1])
    cash_flows[-1] += 1.0
    return float(np.sum(pay_times * cash_flows) / np.sum(cash_flows))


@dataclass
class CapitalProfile:
    ead_hf: np.ndarray
    ead_dealer_b: np.ndarray
    m_eff: np.ndarray
    k_ccr_hf: np.ndarray
    k_ccr_dealer_b: np.ndarray
    k_cva_hf: np.ndarray
    k_cva_dealer_b: np.ndarray
    im: np.ndarray           # 딜러 B IM (SIMM, 양방향 같은 금액)
    multiplier: np.ndarray   # SA-CCR PFE 승수 (받은 IM 반영)


def im_profile(a: Assumptions, ex: Exposure) -> np.ndarray:
    """시점별 SIMM IM (원화 명목 1 기준). 환율은 선도환율, 금리는 오늘 커브의 선도."""
    if not a.im_csa:
        return np.zeros_like(ex.times)
    c_jpy, c_krw = coupons(a)
    return np.array([
        simm(t, a.market, c_jpy, c_krw, a.maturity_years, fx, a.simm_version,
             a.simm_calc_currency, a.simm_include_principal)["im"]
        for t, fx in zip(ex.times, ex.fwd_fx)
    ])


def capital_profile(a: Assumptions, ex: Exposure) -> CapitalProfile:
    remaining = a.maturity_years - ex.times
    addon = sa_ccr_addon(remaining, a.sa_ccr_ccs_treatment)

    # 공공기관 A: 무담보 → maturity factor 1, RC는 경로별 max(V,0)의 기대값(EPE)으로 근사
    ead_hf = 1.4 * (ex.epe_hf + addon)
    # 딜러 B: 증거금부 → maturity factor 1.5 x sqrt(MPOR/250). 실제 정산 지연이 없어도
    # 규제상 MPOR 하한(10영업일)이 적용된다.
    # 266.다: RC = max(V - C, TH + MTA - NICA, 0). VM이 V를 덮고 TH = 0이므로 RC = 0.
    # NICA = 딜러 B가 낸 분리보관 IM (귀사가 낸 IM은 분리보관이라 차감하지 않음, 266.다.(2))
    # 267.가: 승수 = min(1, 5% + 95% x exp((V - C) / (2 x 95% x add-on))), V - C = -NICA
    margined_mf = 1.5 * np.sqrt(a.regulatory_mpor_days / 250)
    addon_margined = margined_mf * addon
    im = im_profile(a, ex)
    nica = im * (1 - a.im_haircut)
    multiplier = np.minimum(1.0, 0.05 + 0.95 * np.exp(
        -nica / np.maximum(2 * 0.95 * addon_margined, 1e-12)))
    ead_dealer_b = 1.4 * multiplier * addon_margined

    # BA-CVA (reduced version, 헤지 없음), CRE50:
    #   SCVA_c = (1/alpha) x RW_c x M x EAD x DF,  DF = (1 - e^{-0.05M}) / (0.05M)
    #   K_reduced = sqrt( (rho x sum SCVA)^2 + (1 - rho^2) x sum SCVA^2 ), rho = 0.5
    #   K = DS x K_reduced
    # 거래상대방 1곳만 보면 K_reduced = SCVA 이므로 K = DS x SCVA (단독 기준, 보수적).
    # K는 최저비율 8% 기준 자본이므로 RWA(= K x 12.5) x 목표비율로 환산한다.
    m = np.array([effective_maturity(t, a) for t in ex.times])
    disc_factor = np.where(m > 0, (1 - np.exp(-0.05 * m)) / np.maximum(0.05 * m, 1e-12), 0.0)
    to_target = 12.5 * a.capital_ratio

    def ba_cva(rw: float, ead: np.ndarray) -> np.ndarray:
        scva = rw * m * ead * disc_factor / a.ba_cva_alpha
        return a.ba_cva_ds * scva * to_target

    k_ccr_hf = ead_hf * a.rw_hf * a.capital_ratio
    k_ccr_dealer_b = ead_dealer_b * a.rw_dealer_b * a.capital_ratio

    if a.cva_capital_method == "ccr_proxy":
        k_cva_dealer_b = k_ccr_dealer_b.copy()
        k_cva_hf = k_ccr_hf.copy() if a.hf_cva_capital else np.zeros_like(ead_hf)
    elif a.cva_capital_method == "ccr_ratio":
        k_cva_dealer_b = k_ccr_dealer_b * a.cva_to_ccr_rwa_ratio
        k_cva_hf = k_ccr_hf * a.cva_to_ccr_rwa_ratio if a.hf_cva_capital else np.zeros_like(ead_hf)
    else:
        k_cva_dealer_b = ba_cva(a.ba_cva_rw_dealer_b, ead_dealer_b) * a.ba_cva_marginal_factor
        k_cva_hf = (ba_cva(a.ba_cva_rw_hf, ead_hf) * a.ba_cva_marginal_factor
                    if a.hf_cva_capital else np.zeros_like(ead_hf))

    return CapitalProfile(
        ead_hf=ead_hf,
        ead_dealer_b=ead_dealer_b,
        m_eff=m,
        k_ccr_hf=k_ccr_hf,
        k_ccr_dealer_b=k_ccr_dealer_b,
        k_cva_hf=k_cva_hf,
        k_cva_dealer_b=k_cva_dealer_b,
        im=im,
        multiplier=multiplier,
    )


def capital_funding_rate(a: Assumptions, times: np.ndarray) -> np.ndarray:
    """자본이 대체하는 조달의 금리: KRW 순간 선도금리 + FTP 스프레드 (구간 [t, t+dt])."""
    fwd = -np.diff(np.log(a.market.krw.df(times))) / np.diff(times)
    return fwd + a.usd_funding_spread


def kva(a: Assumptions, times: np.ndarray, capital: np.ndarray) -> float:
    """자본비용의 현재가치. h = 자본 요구수익률, r = 자본의 조달대체 금리.

    "gross":         sum K x h x DF_r            자본 전액에 h, 조달대체 이익 없음
    "net_funding":   sum K x (h - r) x DF_r      자본이 조달을 대체하는 이익 반영
    "retained":      sum K x h x DF_r x e^{-ht}  받은 upfront가 자본으로 적립되어 소진
    "retained_net":  sum K x (h - r) x e^{-ht}   위 둘 다. 첫날 이익을 인식해 자본에 넣고,
                     묶인 자본은 조달을 대체한다. 주주 입장 NPV(할인율 h)와 같다.
    retained 계열 유도: 적립금 R이 r을 벌고 주주 자본(K - R)에 (h - r)를 지급하면
        dR/dt = hR - (h - r)K, R(T) = 0 → R(0) = ∫ (h - r) K e^{-hs} ds
    """
    t = times[:-1]
    k = capital[:-1]
    h = a.cost_of_capital
    r = capital_funding_rate(a, times)
    df_r = discount(a, t) * np.exp(-a.usd_funding_spread * t)
    method = a.kva_method
    if method == "gross":
        flows = k * h * discount(a, t)
    elif method == "net_funding":
        flows = k * (h - r) * df_r
    elif method == "retained":
        flows = k * h * df_r * np.exp(-h * t)
    elif method == "retained_net":
        flows = k * (h - r) * np.exp(-h * t)
    else:
        raise ValueError(method)
    return float(np.sum(flows * a.dt))


def mva(a: Assumptions, times: np.ndarray, im: np.ndarray) -> float:
    """MVA = sum( 낸 IM(t) x IM 조달 스프레드 x DF(t) x dt ). 받은 IM은 분리보관이라 이익 없음."""
    t = times[:-1]
    return np.sum(im[:-1] * a.im_funding_spread * discount(a, t) * a.dt)


# ---------------------------------------------------------------------------
# 4. Break 재헤지 비용
# ---------------------------------------------------------------------------
def annuity(a: Assumptions, start: int, end: int) -> float:
    """start년 이후 end년까지 연 1회 지급 원화 연금의 현재가치."""
    return float(discount(a, np.arange(start + 1, end + 1)).sum())


def fee_income(a: Assumptions) -> float:
    """귀사 수수료 수입의 현재가치 (명목 대비).

    running: 공공기관 A 원화 쿠폰 가산분(연 10bp, ACT/360)을 30년간 받는다.
    upfront: 딜러 B가 upfront를 지급. bullet 금액이 확정돼 있으면 그 값, break 구조는 연 bp 차이만큼 뺀다.
    """
    ann = annuity(a, 0, a.maturity_years)
    if a.fee_structure in ("running", "running_dealer_b"):
        # 공공기관 A 쿠폰과 딜러 B 쿠폰 차이의 현재가치
        return (hf_coupon_krw(a) - coupons(a)[1]) * ann
    if a.dealer_b_upfront_bullet_bp is not None:
        bullet = a.dealer_b_upfront_bullet_bp / 1e4
    else:
        bullet = a.dealer_b_fee_bullet_bp_pa / 1e4 * ann
    if a.dealer_b_structure != "break":
        return bullet
    return bullet - (a.dealer_b_fee_bullet_bp_pa - a.dealer_b_fee_break_bp_pa) / 1e4 * ann


def to_running_at_break(a: Assumptions, upfront_today: float) -> float:
    """현재 upfront 금액을 break 시점부터 남은 기간의 연 bp로 바꾼다."""
    return upfront_today / annuity(a, a.break_year, a.maturity_years) * 1e4


def implied_tail_value_bp(a: Assumptions) -> float:
    """딜러 B가 10~30년 구간에 추가로 내는 값 (bullet - break upfront), break 시점 기준 연 bp."""
    gap = (a.dealer_b_fee_bullet_bp_pa - a.dealer_b_fee_break_bp_pa) / 1e4 * annuity(a, 0, a.maturity_years)
    return to_running_at_break(a, gap)


def replacement_cost_if_exercised(a: Assumptions) -> float:
    """break가 실행됐을 때 남은 20년 CCS를 새로 구하는 비용 (현재가치, 실행 확률 미반영).

    break 시점 비용 = (bid-offer + 새 딜러 부과분)bp x 남은 기간 연금계수.
    새 딜러가 돈을 내고 받아 간다면(부과분 음수) 비용이 음수, 즉 수익이 된다.
    """
    running_bp = a.replacement_bid_offer_bp + a.replacement_dealer_charge_bp
    return running_bp / 1e4 * annuity(a, a.break_year, a.maturity_years)


def break_replacement_cost(a: Assumptions) -> float:
    """break 구조일 때 기대 재헤지 비용. bullet이면 0."""
    if a.dealer_b_structure != "break":
        return 0.0
    return a.break_exercise_prob * replacement_cost_if_exercised(a)


def breakeven_replacement_payment_bp(a: Assumptions) -> float:
    """break가 확실히 실행될 때 bullet과 같아지려면 새 딜러가 귀사에 내야 하는 금액 (연 bp).

    break 구조는 지금 (bullet - break) upfront를 포기한다. 10년 뒤 새 딜러가
    그만큼 + bid-offer를 내 줘야 본전이다.
    """
    return implied_tail_value_bp(a) + a.replacement_bid_offer_bp


# ---------------------------------------------------------------------------
# 5. 전체 합산: 공공기관 A 라인 피와 딜러 B 헤지 비용을 나눠서 본다
# ---------------------------------------------------------------------------
@dataclass
class LineFeeResult:
    line_fee_items: list[tuple[str, float]]   # 공공기관 A 신용 사용에 대한 대가
    hedge_cost_items: list[tuple[str, float]]  # 딜러 B와 헤지하면서 생기는 비용
    fee_income: float                          # 수수료 수입 현재가치 (running: 공공기관 A 쿠폰 가산분)
    fca: float
    fba: float
    annuity_full: float
    exposure: Exposure
    capital: CapitalProfile

    @property
    def line_fee(self) -> float:
        return sum(x for _, x in self.line_fee_items)

    @property
    def hedge_cost(self) -> float:
        return sum(x for _, x in self.hedge_cost_items)

    @property
    def total(self) -> float:
        """귀사 비용 합계 (공공기관 A 신용 + 딜러 B 헤지)."""
        return self.line_fee + self.hedge_cost

    @property
    def net_margin(self) -> float:
        """딜러 B upfront - 비용 합계. 양수면 남는 거래."""
        return self.fee_income - self.total

    def to_running_bp(self, upfront: float) -> float:
        return upfront / self.annuity_full * 1e4


def compute_line_fee(a: Assumptions) -> LineFeeResult:
    ex = simulate_exposure(a)
    cap = capital_profile(a, ex)
    fca, fba = funding_cost(a, ex)

    line_fee_items = [
        ("공공기관 A CVA (회계상)", cva(a, ex.times, ex.epe_hf, a.cds_hf) if a.include_hf_cva else 0.0),
        ("공공기관 A 신용위험 자본 (KVA)", kva(a, ex.times, cap.k_ccr_hf)),
        ("공공기관 A CVA 자본 (KVA)", kva(a, ex.times, cap.k_cva_hf)),
    ]
    net_funding = fca - fba if a.recognise_fba else fca
    hedge_cost_items = [
        ("딜러 B CVA", cva(a, ex.times, ex.epe_dealer_b, a.cds_dealer_b)),
        ("담보 조달 (FVA)", net_funding),
        ("딜러 B 신용위험 자본 (KVA)", kva(a, ex.times, cap.k_ccr_dealer_b)),
        ("딜러 B CVA 자본 (KVA)", kva(a, ex.times, cap.k_cva_dealer_b)),
        ("IM 조달 (MVA)", mva(a, ex.times, cap.im)),
        ("Break 재헤지 비용", break_replacement_cost(a)),
    ]

    return LineFeeResult(
        line_fee_items=line_fee_items,
        hedge_cost_items=hedge_cost_items,
        fee_income=fee_income(a),
        fca=fca,
        fba=fba,
        annuity_full=annuity(a, 0, a.maturity_years),
        exposure=ex,
        capital=cap,
    )
