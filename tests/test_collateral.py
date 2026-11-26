"""다리 할인 = 담보 통화 커브 (DECISIONS M24, L-081). 스냅샷 필요. python -m tests.test_collateral

- KRW 현금 담보: 원화 환산 현금흐름 전체를 CD(원화 IRS) 커브로 할인한 값과 같다 (외화 다리 = 선도환율 x CD 할인)
- KRW 담보 딜러의 Par 헤지는 그 커브에서 가치 0이고, USD 담보 Par 헤지와 금리가 다르다
- USD 담보·무담보 상대방, CCP(원화 IRS의 KRW, 달러 OIS의 USD)는 예전과 같은 커브
"""
import sys

import numpy as np

from linefee.engine import leg_amounts_today, leg_value_today, trade_value_today
from linefee.market import load
from linefee.pricing import market_hedge, par_client
from linefee.trade import CSA, Counterparty, Leg, Trade, build_deal, collateralised

FAILS = []


def check(label, ok, detail=""):
    print(f"  {'ok  ' if ok else '실패'} {label}" + (f" ({detail})" if detail else ""))
    if not ok:
        FAILS.append(label)


def main():
    try:
        m = load()
    except FileNotFoundError as e:
        sys.exit(str(e))
    krw_dealer = Counterparty("딜러 K", 0.2, 0.05, csa=CSA(vm=True, im=True, ccy="KRW"))
    usd_dealer = Counterparty("딜러 U", 0.2, 0.05, csa=CSA(vm=True, im=True, ccy="USD"))
    corp = Counterparty("기업", 1.0, 0.03, pd=0.002)
    t = par_client(Trade([Leg("KRW", 1e8 * m.spot_quote["USD"], "receive", rate=0.03, freq=4),
                          Leg("USD", 1e8, "pay", kind="float", freq=4, day_count="ACT/360")], 5.0), m)

    usd_leg = collateralised(t, krw_dealer).legs[1]
    pay, amt = leg_amounts_today(usd_leg, t, m)
    direct = -float((amt * m.fwd_fx("USD", pay) * m.local["KRW"].df(pay)).sum())   # 선도환율 x CD 할인
    check("KRW 담보 외화 다리 = 원화 환산 현금흐름의 CD 커브 할인", abs(leg_value_today(usd_leg, t, m) / direct - 1) < 1e-12)
    check("KRW 담보 KRW 다리 할인 = CD 커브", m.disc_for(collateralised(t, krw_dealer).legs[0]) is m.local["KRW"])

    hk, hu = market_hedge(t, m, krw_dealer), market_hedge(t, m, usd_dealer)
    check("KRW 담보 딜러 Par 헤지는 KRW 담보 커브에서 가치 0", abs(trade_value_today(hk, m)) < 1.0)
    gap = (hk.legs[0].rate - hu.legs[0].rate) * 1e4
    check("KRW 담보와 USD 담보 Par 헤지 금리가 다르다 (5년 USD/KRW, 0.5bp 이상)", abs(gap) > 0.5, f"{gap:+.3f}bp")
    check("USD 담보 딜러 Par 헤지 = 예전(딜러 없이) Par 헤지", hu == market_hedge(t, m))

    d = build_deal(t, corp, krw_dealer, hedge=hk, fee=10.0)
    check("고객(무담보) 다리 할인은 그대로, 헤지는 KRW 담보",
          [l.discount for l in d.client_trade.legs] == ["csa", "csa"]
          and [l.discount for l in d.hedge_trade.legs] == ["krw", "krw"])

    irs = Trade([Leg("KRW", 1e11, "receive", "fixed", 0.035, 4, "ACT/365", False, "local"),
                 Leg("KRW", 1e11, "pay", "float", 0.0, 4, "ACT/365", False, "local")], 5.0, "IRS")
    ccp_krw = Counterparty("CCP", 0.02, 0.0, csa=CSA(vm=True, ccy="KRW", ccp=True))
    ccp_usd = Counterparty("CCP", 0.02, 0.0, csa=CSA(vm=True, ccy="USD", ccp=True))
    di = build_deal(irs, corp, ccp_krw, hedge=market_hedge(irs, m))
    check("CCP 원화 IRS 헤지(KRW 담보)는 예전과 같은 CD 커브", all(m.disc_for(l) is m.local["KRW"] for l in di.hedge_trade.legs))
    ois = Trade([Leg("USD", 1e8, "receive", "fixed", 0.04, 1, "ACT/360", False, "local"),
                 Leg("USD", 1e8, "pay", "float", 0.0, 1, "ACT/360", False, "local")], 3.0, "IRS")
    do = build_deal(ois, corp, ccp_usd, hedge=market_hedge(ois, m))
    check("CCP 달러 OIS 헤지(USD 담보)는 예전과 같은 SOFR 커브", all(m.disc_for(l) is m.local["USD"] for l in do.hedge_trade.legs))
    v = np.array([trade_value_today(collateralised(t, c), m) for c in (None, corp, usd_dealer)])
    check("무담보·USD 담보 상대방의 다리 가치는 예전과 같다", bool(np.all(v == v[0])))


if __name__ == "__main__":
    main()
    print("실패: " + (", ".join(FAILS) if FAILS else "없음"))
    sys.exit(1 if FAILS else 0)
