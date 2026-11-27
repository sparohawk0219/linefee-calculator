"""은행 관리회계 E/L(CEM, 매월 일할, 은행간 30%, PD 하한) 단정 (합성 값). python -m tests.test_bank_el"""
from types import SimpleNamespace as NS

import numpy as np

from linefee import bank_el as B
from linefee.trade import CSA, Counterparty
from tests._check import Checker

c = Checker()
c.ok("가산율 외환 1년 이하 1%", B.addon_rate("CCS", 1.0) == 0.01)
c.ok("가산율 외환 5년 이하 5%", B.addon_rate("FXFWD", 5.0) == 0.05)
c.ok("가산율 외환 5년 초과 7.5%", B.addon_rate("CCS", 5.01) == 0.075)
c.ok("가산율 금리 1년 이하 0%, 5년 초과 1.5%", B.addon_rate("IRS", 0.5) == 0.0 and B.addon_rate("IRS", 30) == 0.015)

t, d = B.month_grid("2026-11-25", 2.0)
c.ok("2년 = 24개월", len(t) == 24 and abs(t[-1] - 2.0) < 1e-12)
c.ok("일수 합 = 만기 x 365", abs(d.sum() - 730.0) < 1e-9)
t2, d2 = B.month_grid("2026-11-25", 1.5 / 12)
c.ok("끝 달은 만기에서 끊는다", len(t2) == 2 and abs(t2[-1] - 1.5 / 12) < 1e-12)

flat = NS(df=lambda x: np.ones_like(np.asarray(x, dtype=float)))
tr = NS(product="CCS", legs=[NS(ccy="USD", notional=100.0), NS(ccy="KRW", notional=1e5)])
spot = {"USD": 1000.0, "KRW": 1.0}
times = np.array([0.0, 10.0])
ee = np.array([5000.0, 5000.0])
corp = Counterparty("기업", 1.0, 0.03, pd=0.01, lgd=0.6)
p = B.party_el("고객", corp, tr, times, ee, 2.0, "2026-11-25", spot, flat)
# 2년 CCS: 처음 1년은 잔존 1~2년이라 5%, 나머지는 1%. 합 = PD x LGD x sum((EE + addon) x 일수/365)
want = 0.01 * 0.6 * float(np.sum((5000.0 + 1e5 * np.where(2.0 - p.times > 1.0 + 1e-9, 0.05, 0.01)) * p.days / 365))
c.ok("기업 부과액 합계", abs(p.total - want) < 1e-6 and p.weight == 1.0)
c.ok("할인 1이면 현재가치 = 합계", abs(p.pv - p.total) < 1e-9)

bank = Counterparty("은행", 0.2, 0.05, pd=0.0, lgd=0.6, csa=CSA(vm=True), interbank=True)
q = B.party_el("딜러", bank, tr, times, ee, 2.0, "2026-11-25", spot, flat)
want_q = 0.3 * 0.0003 * 0.6 * float(np.sum(q.addon * q.days / 365))
c.ok("은행간 30%, PD 하한 0.03%, VM 담보면 재조달비용 0", abs(q.total - want_q) < 1e-9 and q.rc.max() == 0 and q.pd == 0.0003)

bk = B.BankEL([p, q])
ann = bk.annual()
c.ok("연도별 합 = 합계", len(ann) == 2 and abs(sum(r["고객"] + r["딜러"] for r in ann) - bk.total) < 1e-6)
c.finish()
