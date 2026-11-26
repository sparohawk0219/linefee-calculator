"""비용 합산과 손익분기 계산.

비용 (모두 원화 현재가치, 시간 적분은 시뮬레이션 격자의 사다리꼴 규칙)
    E/L  = LGD x int EE(t) x D(t) x dS(t),  S(t) = (1 - PD)^t,  EE = E[max(V,0)] (무담보)
    FVA  = s x int E[B(t)] x D(t) dt            (조달이익 인정 시: 해석적, 오늘 커브의 선도가치)
         = s x int (E[max(B,0)] - 0) x D dt     (불인정 시: 시뮬레이션)
           B(t) = 조달 필요액 = sum_{무담보 상대방} V_cp(t) - G(t)
           G(t) = 첫날 이익 중 아직 미수로 남은 부분 (자본으로 조달된 것으로 봄, KVA와 일관)
                = [V_고객(0) + V_헤지(0) + min(upfront, 0)] x 남은 비율(t)   (engine.remaining_value_ratio)
           s = 헤지 담보 통화의 조달 스프레드 (FTP - CSA 담보 이자)
           VM 상대방은 담보 = 시가라 B에 들어가지 않는다. 거래일 기준으로
             BTB 헤지(오프마켓):   B(0) = 딜러에 낸 VM
             시장가 헤지:          B(0) = 0
           받은 upfront 현금은 자본(첫날 이익)이며 조달을 대체하는 효과는 KVA에서만 반영한다.
    MVA  = int IM_냄(t) x IM 조달 스프레드 x D dt
    KVA  = int (h - r(t)) x E[K(t)] x e^{-ht} dt
           h = 세전 요구수익률, r(t) = KRW 조달 기준금리(원화 IRS 커브) 선도 + KVA 조달 스프레드(KRW FTP - CD 커브).
           첫날 이익을 CET1로 적립하고, 묶인 자본이 조달을 대체한다 (확정 방식).
첫날 이익 = V_고객(0) + V_헤지(0) + upfront
순마진   = 첫날 이익 - 비용 합계
몬테카를로 표준오차: 순마진을 경로별 기여의 평균으로 쓰고(대조 난수면 짝 평균) 표준편차 / sqrt(경로 수).
"""
from dataclasses import dataclass, field, replace

import numpy as np

from .engine import (leg_annuity_krw, leg_value_today, remaining_value_ratio, simulate,
                     trade_forward_value, trade_value_paths, trade_value_today)
from .market import MarketData
from .regcap import capital_paths, ccp_im_profile, im_profile, sa_ccr_ead
from .trade import BankParams, Deal, Trade


def market_hedge(client_trade: Trade, m: MarketData) -> Trade:
    """딜러 라인·CCP 헤지: 고객 거래의 반대 포지션을 시장가(거래일 가치 0)로 체결한다.

    통화스왑: 미러 거래의 은행 지급 다리 금리(변동이면 스프레드)를 바꿔 가치 0으로 맞춘다.
    IRS·OIS (CCP 헤지): 미러 거래의 고정금리 다리 금리를 바꿔 가치 0으로 맞춘다.
    FX 선도: 미러 거래의 KRW 다리 금액(= 선도환율)을 바꿔 가치 0으로 맞춘다. KRW 다리가 없으면 지급 다리.
    """
    hedge = client_trade.mirrored()
    if client_trade.product == "FXFWD":
        idx = next((i for i, l in enumerate(hedge.legs) if l.ccy == "KRW"), hedge.leg("pay"))
        leg = hedge.legs[idx]
        v_leg = leg_value_today(leg, hedge, m)
        v_other = trade_value_today(hedge, m) - v_leg
        legs = list(hedge.legs)
        legs[idx] = replace(leg, notional=leg.notional * (-v_other / v_leg))
        return replace(hedge, legs=legs)
    if client_trade.product == "IRS":
        idx = next(i for i, l in enumerate(hedge.legs) if l.kind == "fixed")
    else:
        idx = hedge.leg("pay")
    leg = hedge.legs[idx]
    v0 = trade_value_today(hedge, m)
    return hedge.bumped(idx, -v0 / (leg.sign * leg_annuity_krw(leg, hedge, m)))


def par_client(client_trade: Trade, m: MarketData) -> Trade:
    """고객 거래를 시장가로: 은행 수취 다리 금리(변동이면 스프레드, FX 선도는 KRW 금액)를 조정해 가치 0.

    IRS·OIS는 고정금리 다리를 조정한다.
    """
    return market_hedge(client_trade, m).mirrored()


def market_gap(client_trade: Trade, m: MarketData) -> float:
    """고객 거래(라인피 전)의 시장가 대비 차이, Trade.favoured 단위 (통화스왑·IRS: 연 소수, FX: 원/외화 1단위).

    양수 = 은행에 유리(이미 라인피처럼 받는 오프마켓).
    """
    v0 = trade_value_today(client_trade, m)
    if client_trade.product == "IRS":
        idx = next(i for i, l in enumerate(client_trade.legs) if l.kind == "fixed")
        leg = client_trade.legs[idx]
        return v0 / leg_annuity_krw(leg, client_trade, m)
    if client_trade.product == "FXFWD":
        fx_leg = next(l for l in client_trade.legs if l.ccy != "KRW")
        k = next(l for l in client_trade.legs if l.ccy == "KRW")
        return v0 / (fx_leg.notional * float(m.disc_for(k).df(m.end_time(client_trade))))
    leg = client_trade.legs[client_trade.leg("receive")]
    return v0 / leg_annuity_krw(leg, client_trade, m)


# ---------------------------------------------------------------------------
# 적분 가중치 (사다리꼴)
# ---------------------------------------------------------------------------
def _trap(times: np.ndarray) -> np.ndarray:
    dt = np.diff(times)
    w = np.zeros_like(times)
    w[:-1] += dt / 2
    w[1:] += dt / 2
    return w


def _kva_weights(times: np.ndarray, h: float, r: np.ndarray) -> np.ndarray:
    """KVA = sum_j w_j K(t_j). 구간 i의 (h - r_i) dt_i를 양 끝 노드에 반씩, 노드마다 e^{-h t_j}."""
    a = (h - r) * np.diff(times) / 2
    w = np.zeros_like(times)
    w[:-1] += a
    w[1:] += a
    return w * np.exp(-h * times)


def _el_weights(times: np.ndarray, disc: np.ndarray, pd: float, lgd: float) -> np.ndarray:
    surv = (1 - pd) ** times
    ds = (surv[:-1] - surv[1:]) / 2
    w = np.zeros_like(times)
    w[:-1] += ds
    w[1:] += ds
    return lgd * disc * w


def _pair_mean(x: np.ndarray, antithetic: bool) -> np.ndarray:
    if not antithetic:
        return x
    h = len(x) // 2
    return (x[:h] + x[h:2 * h]) / 2


@dataclass
class Run:
    times: np.ndarray
    day1: float                      # 첫날 이익 (원)
    v0_client: float
    v0_hedge: float
    costs: dict                      # 항목 -> 원 (양수 = 비용)
    capital: dict                    # "고객 신용" 등 -> 시점별 기대 필요자본 (원)
    rwa: dict                        # 같은 키 -> 시점별 기대 RWA
    ee_client: np.ndarray
    pfe_client: np.ndarray           # 95%
    vm_posted: np.ndarray            # 딜러 등 VM 상대방에 낸 VM 기대값 (표시용)
    vm_received: np.ndarray
    fund_need: np.ndarray            # E[max(B,0)] 조달 필요액 (시뮬레이션)
    fund_surplus: np.ndarray         # E[max(-B,0)] 조달 여유 (시뮬레이션)
    fund_expected: np.ndarray        # E[B] 해석적 (오늘 커브 선도가치)
    im: np.ndarray                   # 헤지 상대방에 낸 IM
    funding_rate: np.ndarray         # r(t), 구간 [t, t+dt]
    ftp: float                       # FVA에 쓴 담보 조달 스프레드
    disc: np.ndarray                 # D(t)
    net_se: float = 0.0              # 순마진 몬테카를로 표준오차 (원)
    mc_check: tuple | None = None    # 음수 평균회귀일 때만: (경로 평균이 선도가치와 3 표준오차 넘게 다른 시점 수, 시점 수). L-034
    params: BankParams = field(repr=False, default=None)

    def _sum(self, prefix: str) -> float:
        return float(sum(v for k, v in self.costs.items() if k.startswith(prefix)))

    @property
    def el(self) -> float:
        return self._sum("E/L")

    @property
    def funding(self) -> float:
        """FVA + MVA (기간 손익으로 실제 발생하는 조달비용)."""
        return self._sum("FVA") + self._sum("MVA")

    @property
    def kva(self) -> float:
        return self._sum("KVA")

    @property
    def accounting_net(self) -> float:
        """회계 손익: 첫날 이익 - E/L (내부 위험조정영업수익 기준)."""
        return self.day1 - self.el

    @property
    def funded_net(self) -> float:
        """조달비용 반영 손익: 회계 손익 - FVA - MVA (현재가치)."""
        return self.accounting_net - self.funding

    @property
    def total_cost(self) -> float:
        return float(sum(self.costs.values()))

    @property
    def net(self) -> float:
        return self.day1 - self.total_cost

    @property
    def total_capital(self) -> np.ndarray:
        return sum(self.capital.values())

    def kva_at(self, hurdle_after_tax: float) -> float:
        h = hurdle_after_tax / (1 - self.params.tax_rate)
        return float(_kva_weights(self.times, h, self.funding_rate) @ self.total_capital)

    def net_at(self, hurdle_after_tax: float) -> float:
        kva_items = [k for k in self.costs if k.startswith("KVA")]
        other = self.total_cost - sum(self.costs[k] for k in kva_items)
        return self.day1 - other - self.kva_at(hurdle_after_tax)

    def breakeven_hurdle(self, upper: float = 0.5) -> float | None:
        """순마진이 처음 0이 되는 세후 요구수익률. 0%에서도 손실이면 None, upper까지 이익이면 upper.

        KVA = int (h - r) K e^{-ht} dt 는 h에 단조가 아니다 (h가 아주 크면 e^{-ht} 때문에 다시 작아짐).
        그래서 0%부터 0.1%p 간격으로 올라가며 첫 부호 변화를 찾고 그 구간에서 이분법을 쓴다.
        """
        if self.net_at(0.0) < 0:
            return None
        grid = np.arange(0.0, upper + 1e-9, 0.001)
        prev = 0.0
        for h in grid[1:]:
            if self.net_at(h) < 0:
                lo, hi = prev, h
                for _ in range(40):
                    mid = (lo + hi) / 2
                    lo, hi = (mid, hi) if self.net_at(mid) > 0 else (lo, mid)
                return (lo + hi) / 2
            prev = h
        return upper

    def worst_net(self, upper: float = 0.5) -> tuple[float, float]:
        """세후 요구수익률 0 ~ upper에서 가장 낮은 순마진과 그때의 요구수익률."""
        grid = np.arange(0.0, upper + 1e-9, 0.005)
        vals = [self.net_at(h) for h in grid]
        i = int(np.argmin(vals))
        return float(vals[i]), float(grid[i])


def mc_martingale_check(values: np.ndarray, forward: np.ndarray, antithetic: bool) -> tuple[int, int]:
    """경로 평균 시가가 오늘 커브의 선도가치와 맞는지 (3 표준오차 초과 시점 수, 시점 수 - 1). 진단값이며 계산에 쓰지 않는다.

    음수 평균회귀가 크면 할인계수가 소수 경로의 극단값에 몰려 평균이 선도가치에서 벗어난다 (표준오차는 작게 나온다).
    """
    v = (values[: len(values) // 2] + values[len(values) // 2:]) / 2 if antithetic else values
    se = v.std(axis=0, ddof=1) / np.sqrt(len(v))
    z = np.abs(values.mean(axis=0) - forward) / np.maximum(se, 1.0)
    return int((z[1:] > 3).sum()), len(z) - 1


def run(deal: Deal, m: MarketData, p: BankParams) -> Run:
    ccys = deal.client_trade.currencies | deal.hedge_trade.currencies
    ends = {id(tr): m.end_time(tr) for tr in (deal.client_trade, deal.hedge_trade)}
    horizon = max(ends.values())
    local = all(l.discount == "local" for tr in (deal.client_trade, deal.hedge_trade) for l in tr.legs)
    s = simulate(m, ccys, horizon, p, local=local)
    times = s.times
    dt = np.diff(times)
    disc = m.disc["KRW"].df(times)
    trap = _trap(times)
    fund_curve = m.local.get("KRW", m.disc["KRW"]) if p.kva_funding == "krw_irs" else m.disc["KRW"]
    fdf = fund_curve.df(times)
    r = -np.diff(np.log(fdf)) / dt + p.kva_funding_spread
    h = p.cost_of_capital
    w_kva = _kva_weights(times, h, r)

    parties = [("고객", deal.client, deal.client_trade), ("딜러", deal.dealer, deal.hedge_trade)]
    values = {name: trade_value_paths(tr, m, s) for name, _, tr in parties}
    n_paths = values["고객"].shape[0]

    costs, capital, rwa = {}, {}, {}
    path_cost = np.zeros(n_paths)          # 경로별 비용 합 (표준오차용)
    vm_net = np.zeros_like(values["고객"])
    unc = np.zeros_like(values["고객"])
    unc_fwd = np.zeros_like(times)
    im_dealer = np.zeros_like(times)
    for name, cp, tr in parties:
        v = values[name]
        end = ends[id(tr)]
        if cp.csa.ccp:      # 은행이 CCP에 일방 납부, 받는 IM 없음
            ccy = tr.legs[0].ccy
            im_post = ccp_im_profile(tr, times, m, s.sigma[ccy], p.ccp_im_mpor_days, p.ccp_im_conf)
            im_recv = np.zeros_like(times)
        elif cp.csa.vm and cp.csa.im:   # 양방향 SIMM, 같은 금액
            im_post = im_recv = im_profile(tr, times, m, p.simm_version)
        else:
            im_post = im_recv = np.zeros_like(times)
        if name == "딜러":
            im_dealer = im_post
        ead = sa_ccr_ead(v, tr, times, s.fx, cp.csa, im_recv, end)
        cap = capital_paths(ead, cp, times, p, end)
        for kind, key in (("신용", "ccr"), ("CVA", "cva")):
            label = f"{name} {kind}"
            capital[label] = cap[key].mean(axis=0)
            rwa[label] = cap[f"rwa_{key}"].mean(axis=0)
            kva_path = cap[key] @ w_kva
            costs[f"KVA ({label})"] = float(kva_path.mean())
            path_cost += kva_path
        if cp.csa.vm:
            vm_net += -v
            mva = float(im_post * p.im_funding_spread * disc @ trap)
            costs[f"MVA ({name} IM)"] = mva
            path_cost += mva
        else:
            unc += v
            unc_fwd += trade_forward_value(tr, m, times)
            el_path = np.maximum(v, 0) @ _el_weights(times, disc, cp.pd, cp.lgd)
            costs[f"E/L ({name})"] = float(el_path.mean())
            path_cost += el_path

    v0c = trade_value_today(deal.client_trade, m)
    v0h = trade_value_today(deal.hedge_trade, m)
    side, idx = _solve_leg(deal)
    tr = deal.hedge_trade if side == "hedge" else deal.client_trade
    g = (v0c + v0h + min(deal.upfront_krw, 0.0)) * remaining_value_ratio(tr.legs[idx], tr, m, times)
    b = unc - g[None, :]
    need = np.maximum(b, 0).mean(axis=0)
    surplus = np.maximum(-b, 0).mean(axis=0)
    expected_b = unc_fwd - g
    sp = p.ftp_for(deal.dealer.csa.ccy)   # 헤지 담보 통화의 조달 스프레드
    wd = disc * trap * sp
    if p.recognise_fba:    # 대칭 스프레드: FVA = s x int E[B] D dt, 선형이라 해석적으로
        fva = float(expected_b @ wd)
        path_cost += fva
    else:
        fva_path = np.maximum(b, 0) @ wd
        fva = float(fva_path.mean())
        path_cost += fva_path
    costs["FVA (조달)"] = fva

    day1 = v0c + v0h + deal.upfront_krw
    net_path = _pair_mean(day1 - path_cost, p.antithetic)
    client_v = values["고객"]
    mc_check = None
    if p.rate_mean_reversion < 0:   # 음수 평균회귀(M14 허용 범위)일 때만 시뮬레이션 수렴을 점검한다
        checks = [mc_martingale_check(values[name], trade_forward_value(tr, m, times), p.antithetic)
                  for name, _, tr in parties]
        mc_check = max(checks, key=lambda c: c[0] / max(c[1], 1))
    return Run(
        times=times, day1=day1, v0_client=v0c, v0_hedge=v0h,
        costs={k: v for k, v in costs.items() if abs(v) > 0 or k.startswith(("FVA", "KVA"))},
        capital=capital, rwa=rwa,
        ee_client=np.maximum(client_v, 0).mean(axis=0),
        pfe_client=np.quantile(np.maximum(client_v, 0), 0.95, axis=0),
        vm_posted=np.maximum(vm_net, 0).mean(axis=0), vm_received=np.maximum(-vm_net, 0).mean(axis=0),
        fund_need=need, fund_surplus=surplus, fund_expected=expected_b, im=im_dealer, ftp=sp,
        funding_rate=r, disc=disc, net_se=float(net_path.std(ddof=1) / np.sqrt(len(net_path))), params=p, mc_check=mc_check,
    )


# ---------------------------------------------------------------------------
# 손익분기
# ---------------------------------------------------------------------------
def _solve_leg(deal: Deal) -> tuple[str, int]:
    if deal.solve_on == "hedge":
        return "hedge", deal.hedge_trade.leg("pay")
    return "client", deal.client_trade.leg("receive")


def bump(deal: Deal, delta: float) -> Deal:
    """손익분기 조정 다리에 delta만큼 은행에 유리하게 조정한 거래 조합 (Trade.favoured 단위)."""
    if deal.solve_on == "hedge":
        return replace(deal, hedge_trade=deal.hedge_trade.favoured("pay", delta))
    return replace(deal, client_trade=deal.client_trade.favoured("receive", delta))


def fee_unit_pv(deal: Deal, m: MarketData) -> float:
    """조정 단위 1(통화스왑·IRS: 금리 100%, FX 선도: 1원/외화 1단위)의 원화 현재가치."""
    side, idx = _solve_leg(deal)
    tr = deal.hedge_trade if side == "hedge" else deal.client_trade
    if tr.product == "FXFWD":
        fx_leg = next(l for l in tr.legs if l.ccy != "KRW")
        k = next(l for l in tr.legs if l.ccy == "KRW")
        return fx_leg.notional * float(m.disc_for(k).df(m.end_time(tr)))
    return leg_annuity_krw(tr.legs[idx], tr, m)


solve_leg_annuity = fee_unit_pv   # 이전 이름


@dataclass
class Breakeven:
    extra: float                # 조정 다리에 추가로 필요한 폭 (Trade.favoured 단위, 음수 = 여유)
    extra_upfront_krw: float    # 같은 부족분을 upfront로 받을 때 (음수 = 여유)
    unit_pv_krw: float          # 조정 단위 1의 원화 현재가치
    runs: list                  # (delta, Run) 반복 기록
    converged: bool = True      # 마지막 순마진이 허용오차 이내인지

    @property
    def extra_bp_pa(self) -> float:
        """통화스왑: 연 bp."""
        return self.extra * 1e4

    @property
    def extra_se(self) -> float:
        """손익분기 폭의 몬테카를로 표준오차 (Trade.favoured 단위). 마지막 계산의 순마진 표준오차 / 단위 가치."""
        return self.runs[-1][1].net_se / self.unit_pv_krw


def breakeven(deal: Deal, m: MarketData, p: BankParams, base: Run | None = None,
              tol_krw: float = 1e6, max_iter: int = 6) -> Breakeven:
    """순마진 = 0이 되는 조정폭. 조정이 익스포저를 바꾸므로 같은 난수로 다시 계산하며 할선법으로 찾는다.

    첫 추정은 부족분 / 단위 가치, 이후 할선법을 최대 max_iter번 (전체 계산은 최대 max_iter + 2번).
    upfront 환산: upfront는 익스포저를 바꾸지 않으므로 지금 조건의 부족분(-순마진) 그대로.
    """
    base = base or run(deal, m, p)
    unit = fee_unit_pv(deal, m)
    runs = [(0.0, base)]
    d1 = -base.net / unit
    runs.append((d1, run(bump(deal, d1), m, p)))
    for _ in range(max_iter):
        (da, ra), (db, rb) = runs[-2], runs[-1]
        if abs(rb.net) < tol_krw or rb.net == ra.net:
            break
        dc = db - rb.net * (db - da) / (rb.net - ra.net)
        runs.append((dc, run(bump(deal, dc), m, p)))
    return Breakeven(extra=runs[-1][0], extra_upfront_krw=-base.net, unit_pv_krw=unit, runs=runs,
                     converged=abs(runs[-1][1].net) < tol_krw)


def annual_pnl(r: Run) -> list[dict]:
    """관리회계 기준 연도별 손익 (첫날 이익 인식, 이후 FVA 기대 비용, 할인 없음).

    FVA 비용: 구간 [t_i, t_i+1]의 사다리꼴 값을 구간 시작 연도에 넣는다. 조달이익 인정 시 E[B](해석적),
    불인정 시 E[max(B,0)].
    """
    p = r.params
    t, dt = r.times, np.diff(r.times)
    b = r.fund_expected if p.recognise_fba else r.fund_need
    seg = (b[:-1] + b[1:]) / 2 * dt * r.ftp
    rows = []
    years = max(1, int(np.ceil(t[-1] - 0.05)))   # 결제 지연으로 생긴 며칠짜리 끝 구간은 마지막 해에 합친다
    year_of = np.minimum(np.floor(t[:-1] + 1e-9), years - 1)
    for y in range(years):
        sel = year_of == y
        rows.append({
            "연도": y + 1,
            "첫날 이익": r.day1 if y == 0 else 0.0,
            "FVA 비용": -float(seg[sel].sum()),
            "평균 RWA": float(np.mean(sum(r.rwa.values())[:-1][sel])),
            "평균 필요자본": float(np.mean(r.total_capital[:-1][sel])),
        })
    return rows
