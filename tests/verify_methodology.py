"""docs/METHODOLOGY_v1.md 13장 검증 수치를 다시 만든다.

    python -m tests.verify_methodology          # 전부 (수 분)
    python -m tests.verify_methodology lh irs   # 일부만: lh, equiv, hedge, mc, irs, martingale, grid
"""
import sys
import time
from dataclasses import replace

import numpy as np

from linefee.engine import simulate, trade_forward_value, trade_value_paths
from linefee.market import load, load_legacy
from linefee.pricing import breakeven, market_hedge, par_client, run
from linefee.regcap import ccp_im_profile
from linefee.trade import CSA, BankParams, Counterparty, Leg, Trade, build_deal
from tests.regression_jpykrw import hurdle_txt, jpykrw_deal

OKU = 1e8
CORP = Counterparty("기업", 1.0, 0.03, pd=0.002, lgd=0.6)
DEALER = Counterparty("딜러", 0.5, 0.05, csa=CSA(vm=True, im=True))
DEALER_VM = Counterparty("딜러", 0.5, 0.05, csa=CSA(vm=True))


def seeds(f, n=8):
    vals = np.array([f(s) for s in range(1, n + 1)])
    return vals.mean(), vals.std(ddof=1)


def lh():
    m = load_legacy()
    print("== 13.1 엔/원 30년 LH 건 (2026-11-11 스냅샷, 20,000경로)")
    p = BankParams(n_paths=20000)
    r = run(jpykrw_deal(m), m, p)
    for k, v in r.costs.items():
        print(f"  {k:<16}{v / OKU:7.2f}")
    print(f"  첫날 이익 {r.day1 / OKU:.2f} (고객 {r.v0_client / OKU:.2f}, 헤지 {r.v0_hedge / OKU:.2f}), "
          f"순마진 {r.net / OKU:.2f} ± {r.net_se / OKU:.2f}, 허들 {hurdle_txt(r)} (seed 11)")
    mu, sd = seeds(lambda s: run(jpykrw_deal(m), m, replace(p, seed=s)).net / OKU)
    print(f"  seed 1~8: 순마진 평균 {mu:.2f}, 표준편차 {sd:.3f}")
    for label, pp in [("KVA r(t) = KRW CCS 커브 (예전)", replace(p, kva_funding="krw_ccs")),
                      ("KVA r(t) = KRW IRS 커브 (기본)", p)]:
        rr = run(jpykrw_deal(m), m, pp)
        print(f"  {label}: KVA {rr.kva / OKU:.2f}, 순마진 {rr.net / OKU:.2f}, 허들 {hurdle_txt(rr)}")
    rb = run(jpykrw_deal(m, "hedge"), m, p)
    w, h = rb.worst_net()
    print(f"  구조 B (노무라 다리 차감): 순마진 {rb.net / OKU:.2f}, 허들 {hurdle_txt(rb)}, "
          f"0~50% 최저 {w / OKU:.2f} (h {h:.1%})")
    be = breakeven(jpykrw_deal(m), m, p, base=r)
    print(f"  손익분기: 고객 다리 {10 + be.extra_bp_pa:.2f}bp ± {be.extra_se * 1e4:.2f} (수렴 {be.converged})")
    ml = load()
    rl = run(jpykrw_deal(ml), ml, p)
    print(f"  최신 시장(2026-11-12): 첫날 {rl.day1 / OKU:.2f} (고객 {rl.v0_client / OKU:.2f}), 순마진 "
          f"{rl.net / OKU:.2f} ± {rl.net_se / OKU:.2f}, 허들 {hurdle_txt(rl)}")


def equiv():
    m = load_legacy()
    p = BankParams(n_paths=5000)
    d1 = jpykrw_deal(m)
    direct = Trade([replace(d1.client_trade.legs[0]), replace(d1.client_trade.legs[1])], 30)
    hedge = Trade([Leg("KRW", direct.legs[0].notional, "pay", rate=0.0403, day_count="ACT/360"),
                   Leg("JPY", 1e10, "receive", rate=0.0407)], 30)
    d2 = build_deal(direct, d1.client, d1.dealer, hedge=hedge)
    r1, r2 = run(d1, m, p), run(d2, m, p)
    print("== 13.2 미러 + 10bp vs 직접 입력")
    print(f"  첫날 {r1.day1 / OKU:.4f} / {r2.day1 / OKU:.4f}, 순마진 {r1.net / OKU:.4f} / {r2.net / OKU:.4f}")


def usdkrw_client(m, years=5.0):
    probe = Trade([Leg("KRW", 1e8 * m.spot_quote["USD"], "receive", rate=0.03, freq=4),
                   Leg("USD", 1e8, "pay", kind="float", freq=4, day_count="ACT/360")], years)
    return par_client(probe, m)


def hedge():
    m = load()
    p = BankParams(n_paths=5000)
    c = usdkrw_client(m)
    print(f"== 13.3 USD/KRW 5년 1억달러, 시장가 KRW {c.legs[0].rate:.4%}, 라인피 10bp, 5,000경로")
    btb = run(build_deal(c, CORP, DEALER, fee=10.0), m, p)
    line = run(build_deal(c, CORP, DEALER, hedge=market_hedge(c, m), fee=10.0), m, p)
    for n, r in (("BTB", btb), ("딜러 라인", line)):
        print(f"  {n:<8} 첫날 {r.day1 / OKU:.2f}, FVA {r.costs['FVA (조달)'] / OKU:.3f}, 순마진 {r.net / OKU:.2f}")
    be = breakeven(build_deal(c, CORP, DEALER, hedge=market_hedge(c, m), fee=10.0), m, p, base=line)
    print(f"  손익분기 {10 + be.extra_bp_pa:.2f}bp ± {be.extra_se * 1e4:.2f}, 허들 {line.breakeven_hurdle():.1%}")


def mc():
    m = load_legacy()
    print("== 13.4 몬테카를로 표준오차 (엔/원 LH 건, 순마진 억원)")
    for n in (5000, 20000):
        p = BankParams(n_paths=n)
        mu, sd = seeds(lambda s: run(jpykrw_deal(m), m, replace(p, seed=s)).net / OKU)
        se = run(jpykrw_deal(m), m, p).net_se / OKU
        print(f"  {n:>6}경로: seed 1~8 평균 {mu:.2f}, 표준편차 {sd:.3f}, 계산된 표준오차 {se:.3f}")
    p = BankParams(n_paths=20000, antithetic=False)
    mu, sd = seeds(lambda s: run(jpykrw_deal(m), m, replace(p, seed=s)).net / OKU)
    print(f"  20,000경로 대조 난수 없음: 평균 {mu:.2f}, 표준편차 {sd:.3f}")


def irs_trade(m, ccy, years, recv_fixed=True):
    freq, dc = (4, "ACT/365") if ccy == "KRW" else (1, "ACT/360")
    n = 1000e8 if ccy == "KRW" else 1e8
    fixed = Leg(ccy, n, "receive" if recv_fixed else "pay", "fixed", 0.03, freq, dc, False, "local")
    flt = Leg(ccy, n, "pay" if recv_fixed else "receive", "float", 0.0, freq, dc, False, "local")
    tr = Trade([fixed, flt] if recv_fixed else [flt, fixed], years, "IRS")
    return par_client(tr, m)


def irs():
    m = load()
    p = BankParams(n_paths=5000)
    ccp = Counterparty("CCP", 0.02, 0.0, csa=CSA(vm=True, reg_mpor_days=5, ccp=True))
    print("== 13.5 원화 IRS·달러 OIS (5,000경로, 라인피 5bp, 은행 고정 수취)")
    for ccy, years in (("KRW", 5.0), ("USD", 3.0)):
        c = irs_trade(m, ccy, years)
        fixed = next(l for l in c.legs if l.kind == "fixed")
        dealer = replace(ccp, csa=replace(ccp.csa, ccy=ccy))
        d = build_deal(c, CORP, dealer, hedge=market_hedge(c, m), fee=5.0)
        r = run(d, m, p)
        be = breakeven(d, m, p, base=r)
        print(f"  {ccy} {years:.0f}년: 파 {fixed.rate:.4%}, 첫날 {r.day1 / OKU:.2f}, CCP IM {r.im[0] / OKU:.1f}억, "
              f"FVA {r.costs['FVA (조달)'] / OKU:.3f}, 손익분기 {5 + be.extra_bp_pa:.2f}bp ± {be.extra_se * 1e4:.2f}, "
              f"허들 {r.breakeven_hurdle():.1%}")
        c2 = irs_trade(m, ccy, years, recv_fixed=False)
        d2 = build_deal(c2, CORP, dealer, hedge=market_hedge(c2, m), fee=5.0)
        print(f"    반대 방향(은행 고정 지급) 손익분기 {5 + breakeven(d2, m, p).extra_bp_pa:.2f}bp")


def martingale():
    print("== 13.6 경로 평균 시가 vs 오늘 커브 선도가치 (20,000경로, 억원)")
    m = load()
    p = BankParams(n_paths=20000)
    cases = [("USD/KRW 5년 CCS", usdkrw_client(m)), ("원화 IRS 5년", irs_trade(m, "KRW", 5.0)),
             ("엔/원 30년 CCS", jpykrw_deal(m).client_trade)]
    for name, tr in cases:
        local = all(l.discount == "local" for l in tr.legs)
        s = simulate(m, tr.currencies, m.end_time(tr), p, local=local)
        v = trade_value_paths(tr, m, s)
        fwd = trade_forward_value(tr, m, s.times)
        v, fwd, t = v[:, 1:], fwd[1:], s.times[1:]          # t=0은 확정값(표준오차 0)이라 뺀다
        se = v.std(axis=0, ddof=1) / np.sqrt(v.shape[0])
        diff = np.abs(v.mean(axis=0) - fwd)
        z = diff / se
        i = int(np.argmax(diff))
        print(f"  {name}: 최대 차이 {diff[i] / OKU:.3f} (t={t[i]:.1f}, 그 시점 |V| 평균 {np.abs(v[:, i]).mean() / OKU:.1f}, "
              f"표준오차의 {z[i]:.1f}배), 3배 초과 시점 {int((z > 3).sum())}/{len(z)}")


def grid():
    print("== 13.7 시간 격자 수렴 (5,000경로)")
    m = load()
    fx = Trade([Leg("KRW", 1e8 * 1318.85, "receive"), Leg("USD", 1e8, "pay")], 1.0, "FXFWD")
    fx = par_client(fx, m)
    cases = [("USD/KRW 1년 FX 선도, 마진 1원", fx, DEALER_VM, 1.0),
             ("USD/KRW 5년 CCS, 10bp", usdkrw_client(m), DEALER, 10.0)]
    for name, c, dl, fee in cases:
        out = []
        for dt, steps in ((0.25, 4), (0.25, 24), (0.05, 24), (0.01, 100)):
            p = BankParams(n_paths=5000, dt=dt, min_steps=steps)
            d = build_deal(c, CORP, dl, hedge=market_hedge(c, m), fee=fee)
            r = run(d, m, p)
            be = breakeven(d, m, p, base=r)
            unit = be.extra if c.product == "FXFWD" else be.extra_bp_pa
            out.append(f"dt {dt}/최소 {steps}: E/L {r.el / 1e6:.2f}백만, 손익분기 {fee + unit:.3f}")
        print(f"  {name}\n    " + "\n    ".join(out))


def ccp_im_check():
    m = load()
    c = irs_trade(m, "KRW", 5.0)
    s = simulate(m, c.currencies, m.end_time(c), BankParams(n_paths=1000), local=True)
    im = ccp_im_profile(c, s.times, m, s.sigma["KRW"])
    print(f"  CCP IM t=0 {im[0] / OKU:.2f}억 (명목 대비 {im[0] / 1000e8:.2%})")


def fva_check():
    print("== 13.6b 해석적 FVA - 시뮬레이션 E[B] 기준 FVA (엔/원 LH 건, 20,000경로, seed 1~6)")
    for name, mk in (("2026-11-11", load_legacy), ("2026-11-12", load)):
        m = mk()
        d = jpykrw_deal(m)
        diffs = []
        for sd in range(1, 7):
            r = run(d, m, BankParams(n_paths=20000, seed=sd))
            w = np.zeros_like(r.times)
            w[:-1] += np.diff(r.times) / 2
            w[1:] += np.diff(r.times) / 2
            mc = float(((r.fund_need - r.fund_surplus) * r.disc * w * r.ftp).sum())
            diffs.append(r.costs["FVA (조달)"] - mc)
        diffs = np.array(diffs) / OKU
        print(f"  {name}: 해석적 {r.costs['FVA (조달)'] / OKU:.3f}, 차이 평균 {diffs.mean():+.4f}, "
              f"seed 간 표준편차 {diffs.std(ddof=1):.4f}")


TASKS = {"lh": lh, "equiv": equiv, "hedge": hedge, "mc": mc, "irs": irs, "martingale": martingale, "fva": fva_check, "grid": grid}

if __name__ == "__main__":
    for k in sys.argv[1:] or TASKS:
        t0 = time.time()
        TASKS[k]()
        print(f"  ({time.time() - t0:.0f}초)")
