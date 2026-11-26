"""위험요인 시뮬레이션과 경로별 거래 가치.

위험요인 (KRW 기준, 거래에 필요한 통화만)
    금리: 통화별 평행이동 요인 x_c(t). 정규, 평균회귀 kappa (0이면 Ho-Lee)
          P_c(t,T) = P_c(0,T)/P_c(0,t) x exp(-B(T-t) x_c(t) - B(T-t)^2 Var[x_c(t)] / 2)
          B(tau) = (1 - e^{-kappa tau}) / kappa  (kappa = 0이면 tau)
          → 경로 평균 할인계수 = 오늘의 선도 할인계수
    환율: X_c(t) = F_c(t) x exp(W_c(t) - V_c(t)/2), F_c = 선도, V_c(t) = max_{s<=t} (만기 s 내재변동성)^2 x s
          (총분산이 줄어드는 구간은 증분 0. 증분과 드리프트가 같은 V를 써 E[X] = F)
상관: 과거 5년 주간 추정. 단 USDKRW-JPYKRW 상관은 USDJPY 내재변동성에서 역산한 값.
      바꾼 행렬이 양정치가 아니면 고유값을 잘라 가장 가까운 상관행렬로 고친다.
외화 금리 요인에는 quanto 드리프트를 넣는다: x_c(t)에서 Cov(W_c(t), x_c(t))를 뺀다(이산 격자에서 정확히).
      그래야 원화로 본 외화 현금흐름의 경로 평균 E[X_c(t) P_c(t,T)]이 오늘 커브의 선도가치와 같다.
할인 커브와 추정 커브는 같은 요인으로 움직인다(베이시스 고정).
현금흐름: 실제 지급일·일수 계산(curves.schedule). 시점 t의 가치는 t 당일 지급분을 포함한다(지급 직전 가치).
"""
from dataclasses import dataclass

import numpy as np

from .market import MarketData
from .trade import BankParams, Leg, Trade


@dataclass
class Scenario:
    times: np.ndarray                 # 0 ~ 만기, 간격 dt
    x: dict                           # 통화 -> (경로 x 시점) 금리 요인
    var: dict                         # 통화 -> (시점) Var[x]
    fx: dict                          # 통화 -> (경로 x 시점) 1단위당 원화. KRW = 1
    kappa: float
    corr: np.ndarray
    labels: list
    sigma: dict = None                # 통화 -> 금리 요인 연 변동성


def _nearest_corr(c: np.ndarray) -> np.ndarray:
    w, v = np.linalg.eigh((c + c.T) / 2)
    if w.min() > 1e-8:
        return c
    c2 = v @ np.diag(np.maximum(w, 1e-6)) @ v.T
    d = np.sqrt(np.diag(c2))
    return c2 / np.outer(d, d)


def factor_setup(m: MarketData, ccys: set, maturity: float, p: BankParams,
                 local: bool = False) -> tuple[list, dict, np.ndarray]:
    """(요인 이름, 금리 변동성, 상관행렬). 금리 요인은 거래 통화 + KRW, 환율 요인은 KRW 외 통화.

    local=True (CCP 청산 원화 IRS 등): KRW 금리 변동성을 KRW IRS 10년물 추정치(r_KRW_irs)로 쓴다.
    """
    labels = [f"r_{c}" for c in ("USD", "KRW", "JPY") if c in ccys or c == "KRW"]
    labels += [f"fx_{c}" for c in ("USD", "JPY") if c in ccys]
    h = m.hist
    idx = {l: i for i, l in enumerate(h["labels"])}
    full = np.array(h["corr"])
    corr = np.array([[full[idx[a], idx[b]] for b in labels] for a in labels])
    if "fx_USD" in labels and "fx_JPY" in labels:
        rho = m.implied_fx_corr(min(maturity, 10.0))
        if rho is not None:
            i, j = labels.index("fx_USD"), labels.index("fx_JPY")
            corr[i, j] = corr[j, i] = rho
    sigma = {l[2:]: h["sigma"].get(f"{l}_irs" if local else l, h["sigma"][l]) * p.rate_vol_scale
             for l in labels if l.startswith("r_")}
    return labels, sigma, _nearest_corr(corr)


KAPPA_EPS = 1e-12   # |kappa|가 이보다 작으면 0 (Ho-Lee)으로 본다. kappa < 0(발산하는 OU)도 OU 식이 그대로 성립한다


def _step_sd(sigma: float, kappa: float, dt: float) -> float:
    """한 구간 동안 OU 요인의 표준편차. kappa != 0: sigma sqrt((1 - e^{-2 kappa dt}) / (2 kappa)), 0이면 sigma sqrt(dt)."""
    if abs(kappa) < KAPPA_EPS:
        return sigma * np.sqrt(dt)
    return sigma * np.sqrt((1 - np.exp(-kappa * dt)**2) / (2 * kappa))


def _ou(z: np.ndarray, sigma: float, kappa: float, dt: float) -> tuple[np.ndarray, np.ndarray]:
    paths, steps = z.shape
    decay = np.exp(-kappa * dt)
    sd = _step_sd(sigma, kappa, dt)
    x = np.zeros((paths, steps + 1))
    for i in range(steps):
        x[:, i + 1] = x[:, i] * decay + sd * z[:, i]
    t = np.arange(steps + 1) * dt
    var = sigma**2 * (t if abs(kappa) < KAPPA_EPS else (1 - np.exp(-2 * kappa * t)) / (2 * kappa))
    return x, var


def fx_total_variance(m: MarketData, ccy: str, times: np.ndarray) -> np.ndarray:
    """시점별 환율 로그 총분산 V(t) = max_{s <= t} sigma(s)^2 s. 경로의 분산 증분과 드리프트가 모두 이 값을 쓴다.

    내재변동성 기간구조가 가파르게 역전되면 sigma(t)^2 t가 줄어드는 구간이 생긴다. 분산은 줄 수 없으므로 그 구간은 증분 0
    (앞선 최댓값 유지)으로 두고, 드리프트 -V/2도 같은 V로 맞춰 E[X(t)] = F(t)를 지킨다 (L-033). 총분산이 늘기만 하면 그대로다.
    """
    tv = np.array([m.term_vol(ccy, max(t, 1e-6)) ** 2 * t for t in times])
    return np.maximum.accumulate(tv)


def simulate(m: MarketData, ccys: set, horizon: float, p: BankParams, local: bool = False) -> Scenario:
    """0 ~ horizon을 같은 간격으로 나눈다. 간격은 dt 이하, 구간 수는 min_steps 이상.

    antithetic=True면 난수 절반을 만들고 부호를 바꾼 짝을 붙인다 (경로 i와 i + n/2가 짝).
    """
    n = max(int(np.ceil(horizon / p.dt - 1e-9)), p.min_steps)
    times = np.linspace(0.0, horizon, n + 1)
    dt = times[1] - times[0]
    labels, sigma, corr = factor_setup(m, ccys, horizon, p, local)
    rng = np.random.default_rng(p.seed)
    chol = np.linalg.cholesky(corr).T
    if p.antithetic:
        half = rng.standard_normal((p.n_paths // 2, n, len(labels))) @ chol
        z = np.concatenate([half, -half])
    else:
        z = rng.standard_normal((p.n_paths, n, len(labels))) @ chol
    n_paths = z.shape[0]

    x, var = {}, {}
    fx = {"KRW": np.ones((n_paths, n + 1))}
    fx_sd = {}
    for k, lab in enumerate(labels):
        kind, c = lab.split("_")
        if kind == "r":
            x[c], var[c] = _ou(z[:, :, k], sigma[c], p.rate_mean_reversion, dt)
        else:
            tv = fx_total_variance(m, c, times)
            sd = np.sqrt(np.diff(tv))
            fx_sd[c] = sd
            w = np.hstack([np.zeros((n_paths, 1)), np.cumsum(z[:, :, k] * sd, axis=1)])
            fx[c] = m.fwd_fx(c, times)[None, :] * np.exp(w - 0.5 * tv[None, :])
    # quanto: 외화 금리 요인 평균을 -Cov(W_c, x_c)로 옮긴다. 공분산은 OU 감쇠를 따라 쌓인다
    kappa = p.rate_mean_reversion
    decay = np.exp(-kappa * dt)
    for c, sd in fx_sd.items():
        if c not in x:
            continue
        rho = corr[labels.index(f"r_{c}"), labels.index(f"fx_{c}")]
        sdx = _step_sd(sigma[c], kappa, dt)
        cov = np.zeros(n + 1)
        for i in range(n):
            cov[i + 1] = cov[i] * decay + rho * sd[i] * sdx
        x[c] = x[c] - cov[None, :]
    return Scenario(times, x, var, fx, p.rate_mean_reversion, corr, labels, sigma)


def _b(tau: np.ndarray, kappa: float) -> np.ndarray:
    return tau if abs(kappa) < KAPPA_EPS else (1 - np.exp(-kappa * tau)) / kappa


LIVE_EPS = 1e-9   # 지급일 = t인 현금흐름은 아직 남은 것으로 본다


def leg_amounts_today(leg: Leg, trade: Trade, m: MarketData) -> tuple[np.ndarray, np.ndarray]:
    """(지급 시점, 금액) 오늘 커브 기준. 변동 다리는 오늘의 선도로 추정. 만기 원금 포함(설정 시)."""
    sc = m.schedule(trade, leg)
    if leg.kind == "fixed":
        amt = leg.notional * leg.rate * sc.accr
    else:
        proj = m.proj[leg.ccy]
        amt = leg.notional * (proj.df(sc.start) / proj.df(sc.pay) - 1 + leg.rate * sc.accr)
    amt = np.array(amt, dtype=float)
    if leg.final_exchange:
        amt[-1] += leg.notional
    return sc.pay, amt


def leg_value_paths(leg: Leg, trade: Trade, m: MarketData, s: Scenario,
                    include_principal: bool = True) -> np.ndarray:
    """(경로 x 시점) 다리 가치, 원화, 은행 기준 부호 포함.

    고정: 지급액 = 명목 x 금리 x 일수 비율 (+ 만기 원금)
    변동: 지급액 = 명목 x [P_proj(t,시작)/P_proj(t,지급) - 1 + 스프레드 x 일수 비율]
          이미 시작한 구간(시작 <= t)의 금리는 오늘의 선도로 고정했다고 본다 (근사).
    """
    disc = m.disc_for(leg)
    sc = m.schedule(trade, leg)
    pay, start, accr = sc.pay, sc.start, sc.accr
    k = s.kappa
    out = np.zeros((s.x[leg.ccy].shape[0], len(s.times)))
    for i, t in enumerate(s.times):
        live = pay > t - LIVE_EPS
        if not live.any():
            continue
        p_i, st_i, ac_i = pay[live], start[live], accr[live]
        xt, vt = s.x[leg.ccy][:, i], s.var[leg.ccy][i]
        b = _b(np.maximum(p_i - t, 0.0), k)
        dfs = (disc.df(p_i) / disc.df(t))[None, :] * np.exp(-np.outer(xt, b) - 0.5 * b**2 * vt)
        if leg.kind == "fixed":
            amt = np.broadcast_to(leg.notional * leg.rate * ac_i, dfs.shape).copy()
        else:
            proj = m.proj[leg.ccy]
            fwd_today = proj.df(st_i) / proj.df(p_i) - 1
            started = st_i <= t + 1e-9
            bs = _b(np.maximum(st_i - t, 0.0), k)
            ratio = (proj.df(np.maximum(st_i, t)) / proj.df(p_i))[None, :] * np.exp(
                -np.outer(xt, bs - b) - 0.5 * (bs**2 - b**2) * vt)
            simple = np.where(started[None, :], fwd_today[None, :], ratio - 1)
            amt = leg.notional * (simple + leg.rate * ac_i[None, :])
        if include_principal and leg.final_exchange:
            amt[:, -1] += leg.notional
        out[:, i] = (amt * dfs).sum(axis=1)
    return leg.sign * out * s.fx[leg.ccy]


def trade_value_paths(trade: Trade, m: MarketData, s: Scenario) -> np.ndarray:
    return sum(leg_value_paths(l, trade, m, s) for l in trade.legs)


def leg_value_today(leg: Leg, trade: Trade, m: MarketData) -> float:
    """t=0 다리 가치 (원화, 부호 포함)."""
    pay, amt = leg_amounts_today(leg, trade, m)
    return float(leg.sign * (amt * m.disc_for(leg).df(pay)).sum() * m.spot[leg.ccy])


def trade_value_today(trade: Trade, m: MarketData) -> float:
    return sum(leg_value_today(l, trade, m) for l in trade.legs)


def _forward_of(pay: np.ndarray, amt: np.ndarray, ccy: str, disc, m: MarketData,
                times: np.ndarray) -> np.ndarray:
    """현금흐름(통화 ccy, 할인 disc)의 시점별 선도가치, 원화: fwd_fx(t) x sum_{지급 >= t} 금액 x D(지급)/D(t)."""
    pv = amt * disc.df(pay)
    out = np.array([pv[pay > t - LIVE_EPS].sum() / disc.df(t) for t in times])
    return out * m.fwd_fx(ccy, times)


def trade_forward_value(trade: Trade, m: MarketData, times: np.ndarray) -> np.ndarray:
    """오늘 커브로 본 시점별 기대 가치 (원화). 시뮬레이션 평균 E[V(t)]의 해석적 값 (quanto·볼록성 무시)."""
    out = np.zeros_like(times)
    for leg in trade.legs:
        pay, amt = leg_amounts_today(leg, trade, m)
        out += leg.sign * _forward_of(pay, amt, leg.ccy, m.disc_for(leg), m, times)
    return out


def remaining_value_ratio(leg: Leg, trade: Trade, m: MarketData, times: np.ndarray) -> np.ndarray:
    """첫날 이익(비현금 부분)이 시점별로 얼마나 미수로 남아 있는지의 비율 G(t)/G(0). 오늘 커브 기준.

    라인피 현금흐름(통화스왑·IRS: 조정 다리 쿠폰의 금리 1단위분, FX 선도: 만기 KRW 금액)의
    원화 선도가치 / 오늘 가치. 수수료가 쿠폰으로 들어오며 줄고, 남은 부분에는 이자가 붙는다.
    """
    if trade.product == "FXFWD":
        k = next(l for l in trade.legs if l.ccy == "KRW")
        pay = m.schedule(trade, k).pay[-1:]
        amt, ccy, disc = np.ones(1), "KRW", m.disc_for(k)
    else:
        sc = m.schedule(trade, leg)
        pay, amt, ccy, disc = sc.pay, sc.accr, leg.ccy, m.disc_for(leg)
    fwd = _forward_of(pay, amt, ccy, disc, m, times)
    return fwd / fwd[0] if fwd[0] else np.zeros_like(times)


def receivable_df(leg: Leg, trade: Trade, m: MarketData, times: np.ndarray) -> np.ndarray:
    """미수 G(t)(remaining_value_ratio의 원화 선도가치)가 이자로 불어나는 할인계수 D_g(t). 구간 금리 r_d = -d ln D_g / dt.

    원화 선도가치 = F_c(t) x sum_{지급 >= t} a x D_c(지급) / D_c(t) = S_c x sum a D_c(지급) / [D_c(t) x S_c / F_c(t)] 이므로
    D_g(t) = D_c(t) x S_c / F_c(t). KRW 다리면 그 다리 할인 커브, USD 담보 외화 다리면 USD 담보 KRW 커브,
    KRW 담보 외화 다리면 CD 커브가 된다. FX 선도는 KRW 다리.
    """
    if trade.product == "FXFWD":
        leg = next(l for l in trade.legs if l.ccy == "KRW")
    d = m.disc_for(leg).df(times)
    if leg.ccy == "KRW":
        return d
    return d * m.spot[leg.ccy] / m.fwd_fx(leg.ccy, times)


def leg_annuity_krw(leg: Leg, trade: Trade, m: MarketData) -> float:
    """다리 금리 1(=100%)당 현재가치, 원화. 수수료 bp 환산에 쓴다."""
    sc = m.schedule(trade, leg)
    return float(leg.notional * (sc.accr * m.disc_for(leg).df(sc.pay)).sum() * m.spot[leg.ccy])
