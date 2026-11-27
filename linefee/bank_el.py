"""은행 관리회계 방식 E/L (비교용, DECISIONS M30). 라인피 계산(pricing.run)에는 쓰지 않는다.

은행 관리회계가 장외파생 신용비용을 매기는 방식을 재현한다.
  - 노출: CEM(현재노출방식). EAD = 재조달비용 max(시가, 0) + 명목(원화) x 가산율(잔존만기 구간)
  - 부과: 매월 그 달 EAD의 연 E/L(PD x LGD x EAD)을 일수/365로 일할해 떼고, 거래 기간 동안 더한다 (할인 없음)
  - PD: 내부등급 PD, 하한 0.03%. LGD: 상대방 LGD
  - 은행간(금융기관 상대방): E/L의 30%만 부과 (70% 감면)
  - 자본비용 없음 (KVA 해당 없음)
미래 시가는 몬테카를로 기대 익스포저 E[max(V(t), 0)]로 둔다. 실제 부과액은 실현 경로를 따르므로 이 합계는 기대값이다.
VM 담보 상대방은 재조달비용을 0으로 보고 가산율만 남긴다 (일일 정산으로 현재 노출이 담보로 덮인다고 가정. 받은 IM은 인정하지 않는다).
"""
from dataclasses import dataclass

import numpy as np
import QuantLib as ql

from . import curves as cv

# CEM 가산율 (바젤II 92(i), 옛 감독규정 별표3 CEM 표). (잔존만기 상한(년), 가산율)
CEM_ADDON = {
    "금리": ((1.0, 0.0), (5.0, 0.005), (float("inf"), 0.015)),
    "외환": ((1.0, 0.01), (5.0, 0.05), (float("inf"), 0.075)),
}
PD_FLOOR = 0.0003            # 내부등급 PD 하한 0.03%
INTERBANK_WEIGHT = 0.30      # 은행간 거래는 E/L의 30%만 부과


def addon_rate(product: str, remaining: float) -> float:
    """잔존만기(년) 구간의 CEM 가산율. IRS는 금리, 통화스왑·FX 선도는 외환 (M2)."""
    table = CEM_ADDON["금리" if product == "IRS" else "외환"]
    return next(rate for cap, rate in table if remaining <= cap + 1e-9)


def notional_krw(trade, spot: dict) -> float:
    """거래 명목(원화). 통화스왑·FX 선도는 외화 다리 중 큰 쪽(SA-CCR 조정명목과 같은 방식), 그 밖은 첫 다리. spot: 통화 -> 1단위당 원화."""
    foreign = [l for l in trade.legs if l.ccy != "KRW"]
    legs = foreign if foreign and trade.product != "IRS" else trade.legs[:1]
    return max(l.notional * spot[l.ccy] for l in legs)


def month_grid(as_of: str, end: float) -> tuple[np.ndarray, np.ndarray]:
    """(월말 시점(년), 그 달 일수). 거래일부터 1개월 간격, 마지막 달은 만기에서 끊는다. 시점 = 경과 일수 / 365."""
    today = cv.qdate(as_of)
    ts = [0.0]
    k = 1
    while ts[-1] < end - 1e-9:
        t = ((today + ql.Period(k, ql.Months)) - today) / 365.0
        ts.append(min(t, end))
        k += 1
    t = np.array(ts)
    return t[1:], np.diff(t) * 365.0


@dataclass
class PartyEL:
    name: str
    interbank: bool
    pd: float                    # 하한 적용 후
    lgd: float
    weight: float                # 은행간 30%, 그 밖 100%
    times: np.ndarray            # 월말 시점 (년)
    days: np.ndarray
    rc: np.ndarray               # 기대 재조달비용 (원)
    addon: np.ndarray            # 명목 x 가산율 (원)
    charge: np.ndarray           # 그 달 부과액 (원)
    disc: np.ndarray             # 월말 KRW 할인계수 (비교용 현재가치)

    @property
    def total(self) -> float:
        """거래 기간 부과액 합계 (할인 없음, 관리회계 손익에 잡히는 금액)."""
        return float(self.charge.sum())

    @property
    def pv(self) -> float:
        return float(self.charge @ self.disc)


def party_el(name: str, cp, trade, ee_times: np.ndarray, ee: np.ndarray, end: float, as_of: str,
             spot: dict, disc_curve) -> PartyEL:
    """상대방 한 곳의 월별 부과액. ee: 시뮬레이션 격자의 E[max(V, 0)] (원)."""
    t, days = month_grid(as_of, end)
    rc = np.zeros_like(t) if cp.csa.vm else np.interp(t, ee_times, ee)
    n = notional_krw(trade, spot)
    addon = np.array([n * addon_rate(trade.product, end - x) for x in t])
    pd = max(cp.pd, PD_FLOOR)
    weight = INTERBANK_WEIGHT if cp.interbank else 1.0
    charge = weight * pd * cp.lgd * (rc + addon) * days / 365.0
    return PartyEL(name, cp.interbank, pd, cp.lgd, weight, t, days, rc, addon, charge, disc_curve.df(t))


@dataclass
class BankEL:
    parties: list                # PartyEL (고객, 딜러)

    @property
    def total(self) -> float:
        return sum(p.total for p in self.parties)

    @property
    def pv(self) -> float:
        return sum(p.pv for p in self.parties)

    def annual(self) -> list[dict]:
        """연도별 부과액 (원, 할인 없음). 월말 시점이 속한 해 (마지막 며칠짜리 달은 마지막 해에 합친다)."""
        years = max(1, int(np.ceil(max(p.times[-1] for p in self.parties) - 0.05)))
        rows = [{"연도": y + 1} for y in range(years)]
        for p in self.parties:
            yr = np.minimum(np.ceil(p.times - 1e-9) - 1, years - 1).astype(int)
            for y in range(years):
                rows[y][p.name] = float(p.charge[yr == y].sum())
        return rows


def bank_el(deal, m, r) -> BankEL:
    """거래 조합(고객 거래 + 헤지)의 은행 관리회계 E/L. r: pricing.run 결과 (상대방별 기대 익스포저 r.ee)."""
    out = []
    for name, cp, tr in (("고객", deal.client, deal.client_trade), ("딜러", deal.dealer, deal.hedge_trade)):
        out.append(party_el(name, cp, tr, r.times, r.ee[name], m.end_time(tr), m.as_of, m.spot, m.disc["KRW"]))
    return BankEL(out)
