"""docs/METHODOLOGY_v1.md 13장 검증 수치를 다시 만든다.

    python -m tests.verify_methodology          # 전부 (수 분)
    python -m tests.verify_methodology client_a irs   # 일부만: client_a, equiv, hedge, mc, irs, martingale, fva, grid
    python -m tests.verify_methodology --dump         # 지금 값으로 EXPECTED 후보를 출력 (방법론 변경 뒤 표 갱신용)

단정하는 것: 날짜가 고정된 회귀 기준 스냅샷(load_legacy)의 수치(EXPECTED 표)와 구조적 성질(동치, martingale, 표준오차
범위, 격자 수렴, 해석적 FVA 차이). 최신 스냅샷(load)에 따라 달라지는 수치는 출력만 하며 라벨에 m.as_of를 쓴다.
실패하면 종료 코드 1. 방법론을 바꾼 뒤에는 EXPECTED와 METHODOLOGY_v1.md 13장을 함께 갱신한다.
"""
import sys
import time
from dataclasses import replace

import numpy as np

from linefee.engine import simulate, trade_forward_value, trade_value_paths
from linefee.market import load, load_legacy
from linefee.pricing import breakeven, market_hedge, par_client, run
from linefee.regcap import ccp_im_profile
from linefee.trade import CCP_REG_MPOR_DAYS, CSA, BankParams, Counterparty, Leg, Trade, build_deal
from tests._check import Checker
from tests.regression_jpykrw import hurdle_txt, jpykrw_deal

OKU = 1e8
C = Checker()
GOT: dict = {}     # 이번 실행에서 잰 값 (--dump로 EXPECTED 후보를 뽑는다)

# 기대값: 회귀 기준 스냅샷(load_legacy, 날짜 고정)의 값. 억원. 표시 자릿수 반올림만큼(TOL)의 차이는 허용한다.
EXPECTED = {
    "client_a 비용 KVA (고객 신용)": 0.000,
    "client_a 비용 KVA (고객 CVA)": 12.171,
    "client_a 비용 KVA (딜러 신용)": 0.093,
    "client_a 비용 KVA (딜러 CVA)": 0.314,
    "client_a 비용 FVA (조달)": 3.344,
    "client_a 첫날 이익": 17.065,
    "client_a 순마진 (seed 11)": 1.142,
    "client_a seed 1~8 평균": 1.229,
    "client_a seed 1~8 표준편차": 0.041,
    "client_a KVA r(t) CCS 커브 KVA": 13.320,
    "client_a KVA r(t) CCS 커브 순마진": 0.401,
    "client_a KVA r(t) IRS 커브 KVA": 12.578,
    "client_a KVA r(t) IRS 커브 순마진": 1.142,
    "client_a 구조 B 순마진": 1.970,
    "client_a 손익분기 고객 다리 bp": 9.308,
    "equiv 순마진 (5,000경로)": 1.046,
    "mc 5000경로 seed 평균": 1.201,
    "mc 20000경로 seed 평균": 1.229,
}
TOL = 0.006


def expect(key: str, value: float, tol: float = TOL) -> None:
    """GOT에 기록하고, EXPECTED 표에 있으면 단정한다."""
    GOT[key] = value
    if key in EXPECTED:
        C.near(key, value, EXPECTED[key], tol)
CORP = Counterparty("기업", 1.0, 0.03, pd=0.002, lgd=0.6)
DEALER = Counterparty("딜러", 0.5, 0.05, csa=CSA(vm=True, im=True))
DEALER_VM = Counterparty("딜러", 0.5, 0.05, csa=CSA(vm=True))


def seeds(f, n=8):
    vals = np.array([f(s) for s in range(1, n + 1)])
    return vals.mean(), vals.std(ddof=1)


def client_a():
    m = load_legacy()
    print("== 13.1 엔/원 30년 공공기관 A 건 (2026-11-11 스냅샷, 20,000경로)")
    p = BankParams(n_paths=20000)
    r = run(jpykrw_deal(m), m, p)
    for k, v in r.costs.items():
        print(f"  {k:<16}{v / OKU:7.2f}")
        expect(f"client_a 비용 {k}", v / OKU)
    expect("client_a 첫날 이익", r.day1 / OKU)
    expect("client_a 순마진 (seed 11)", r.net / OKU)
    print(f"  첫날 이익 {r.day1 / OKU:.2f} (고객 {r.v0_client / OKU:.2f}, 헤지 {r.v0_hedge / OKU:.2f}), "
          f"순마진 {r.net / OKU:.2f} ± {r.net_se / OKU:.2f}, 허들 {hurdle_txt(r)} (seed 11)")
    mu, sd = seeds(lambda s: run(jpykrw_deal(m), m, replace(p, seed=s)).net / OKU)
    print(f"  seed 1~8: 순마진 평균 {mu:.2f}, 표준편차 {sd:.3f}")
    expect("client_a seed 1~8 평균", mu)
    expect("client_a seed 1~8 표준편차", sd)
    C.ok("순마진 표준오차가 seed 간 표준편차와 같은 규모 (0.5~2배)", 0.5 <= sd / (r.net_se / OKU) <= 2.0,
         f"표준편차 {sd:.3f}, 표준오차 {r.net_se / OKU:.3f}")
    for label, pp in [("KVA r(t) = KRW CCS 커브 (예전)", replace(p, kva_funding="krw_ccs")),
                      ("KVA r(t) = KRW IRS 커브 (기본)", p)]:
        rr = run(jpykrw_deal(m), m, pp)
        print(f"  {label}: KVA {rr.kva / OKU:.2f}, 순마진 {rr.net / OKU:.2f}, 허들 {hurdle_txt(rr)}")
        tag = "CCS 커브" if "CCS" in label else "IRS 커브"
        expect(f"client_a KVA r(t) {tag} KVA", rr.kva / OKU)
        expect(f"client_a KVA r(t) {tag} 순마진", rr.net / OKU)
    rb = run(jpykrw_deal(m, "hedge"), m, p)
    w, h = rb.worst_net()
    print(f"  구조 B (딜러 B 다리 차감): 순마진 {rb.net / OKU:.2f}, 허들 {hurdle_txt(rb)}, "
          f"0~50% 최저 {w / OKU:.2f} (h {h:.1%})")
    expect("client_a 구조 B 순마진", rb.net / OKU)
    C.ok("구조 B 순마진이 구조 A보다 크다", rb.net > r.net)
    be = breakeven(jpykrw_deal(m), m, p, base=r)
    print(f"  손익분기: 고객 다리 {10 + be.extra_bp_pa:.2f}bp ± {be.extra_se * 1e4:.2f} (수렴 {be.converged})")
    expect("client_a 손익분기 고객 다리 bp", 10 + be.extra_bp_pa, 0.02)
    C.ok("손익분기 반복이 수렴", bool(be.converged))
    ml = load()
    rl = run(jpykrw_deal(ml), ml, p)
    print(f"  최신 시장({ml.as_of}): 첫날 {rl.day1 / OKU:.2f} (고객 {rl.v0_client / OKU:.2f}), 순마진 "
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
    C.near("미러+라인피와 직접 입력의 첫날 이익 동치", r1.day1 / OKU, r2.day1 / OKU, 1e-6)
    C.near("미러+라인피와 직접 입력의 순마진 동치", r1.net / OKU, r2.net / OKU, 1e-6)
    expect("equiv 순마진 (5,000경로)", r1.net / OKU)
    # 손익분기 bp는 명목과 무관해야 한다 (비용이 명목에 선형). 멈춤 기준이 절대 금액이면 작은 명목에서 첫 추정에 멈춘다 (L-030)
    small = replace(d1, client_trade=replace(d1.client_trade, legs=[replace(l, notional=l.notional / 1000)
                                                                     for l in d1.client_trade.legs]),
                    hedge_trade=replace(d1.hedge_trade, legs=[replace(l, notional=l.notional / 1000)
                                                               for l in d1.hedge_trade.legs]))
    be_big, be_small = breakeven(d1, m, p, base=r1), breakeven(small, m, p)
    print(f"  손익분기 bp: 100억엔 {be_big.extra_bp_pa:+.4f}, 1천만엔 {be_small.extra_bp_pa:+.4f} "
          f"(계산 {len(be_big.runs)}번 / {len(be_small.runs)}번)")
    C.near("손익분기 bp가 명목과 무관 (100억엔 vs 1천만엔)", be_small.extra_bp_pa, be_big.extra_bp_pa, 0.002)


def usdkrw_client(m, years=5.0):
    probe = Trade([Leg("KRW", 1e8 * m.spot_quote["USD"], "receive", rate=0.03, freq=4),
                   Leg("USD", 1e8, "pay", kind="float", freq=4, day_count="ACT/360")], years)
    return par_client(probe, m)


def hedge():
    m = load()
    p = BankParams(n_paths=5000)
    c = usdkrw_client(m)
    print(f"== 13.3 [최신 시장 {m.as_of}] USD/KRW 5년 1억달러, 시장가 KRW {c.legs[0].rate:.4%}, 라인피 10bp, 5,000경로")
    btb = run(build_deal(c, CORP, DEALER, fee=10.0), m, p)
    line = run(build_deal(c, CORP, DEALER, hedge=market_hedge(c, m), fee=10.0), m, p)
    for n, r in (("BTB", btb), ("딜러 라인", line)):
        print(f"  {n:<8} 첫날 {r.day1 / OKU:.2f}, FVA {r.costs['FVA (조달)'] / OKU:.3f}, 순마진 {r.net / OKU:.2f}")
    be = breakeven(build_deal(c, CORP, DEALER, hedge=market_hedge(c, m), fee=10.0), m, p, base=line)
    print(f"  손익분기 {10 + be.extra_bp_pa:.2f}bp ± {be.extra_se * 1e4:.2f}, 허들 {line.breakeven_hurdle():.1%}")
    C.near("시장가 거래는 BTB와 딜러 라인의 순마진이 같다 (헤지가 Par)", btb.net / OKU, line.net / OKU, 0.02)


def mc():
    m = load_legacy()
    print("== 13.4 몬테카를로 표준오차 (엔/원 공공기관 A 건, 순마진 억원)")
    ses = []
    for n in (5000, 20000):
        p = BankParams(n_paths=n)
        mu, sd = seeds(lambda s: run(jpykrw_deal(m), m, replace(p, seed=s)).net / OKU)
        se = run(jpykrw_deal(m), m, p).net_se / OKU
        print(f"  {n:>6}경로: seed 1~8 평균 {mu:.2f}, 표준편차 {sd:.3f}, 계산된 표준오차 {se:.3f}")
        expect(f"mc {n}경로 seed 평균", mu)
        C.ok(f"mc {n}경로: 계산된 표준오차가 seed 간 표준편차의 0.5~2배", 0.5 <= se / sd <= 2.0, f"{se:.3f} / {sd:.3f}")
        ses.append(se)
        if n == 20000:
            sd_anti = sd
    C.ok("경로 4배 -> 표준오차 약 1/2 (0.4~0.6)", 0.4 <= ses[1] / ses[0] <= 0.6, f"{ses[1] / ses[0]:.2f}")
    p = BankParams(n_paths=20000, antithetic=False)
    mu, sd = seeds(lambda s: run(jpykrw_deal(m), m, replace(p, seed=s)).net / OKU)
    print(f"  20,000경로 대조 난수 없음: 평균 {mu:.2f}, 표준편차 {sd:.3f}")
    C.ok("대조 난수를 쓰면 표준편차가 줄거나 같다", sd_anti <= sd * 1.05, f"{sd_anti:.3f} vs {sd:.3f}")


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
    ccp = Counterparty("CCP", 0.02, 0.0, csa=CSA(vm=True, reg_mpor_days=CCP_REG_MPOR_DAYS, ccp=True))
    print(f"== 13.5 [최신 시장 {m.as_of}] 원화 IRS·달러 OIS (5,000경로, 라인피 5bp, 은행 고정 수취)")
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
    m = load()
    print(f"== 13.6 [최신 시장 {m.as_of}] 경로 평균 시가 vs 오늘 커브 선도가치 (20,000경로, 억원)")
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
        C.ok(f"martingale {name}: 표준오차 3배 초과 시점이 5% 이하", (z > 3).mean() <= 0.05, f"{int((z > 3).sum())}/{len(z)}")
        C.ok(f"martingale {name}: 최대 차이가 그 시점 |V| 평균의 2% 이하", diff[i] <= 0.02 * np.abs(v[:, i]).mean(),
             f"{diff[i] / np.abs(v[:, i]).mean():.2%}")


def grid():
    m = load()
    print(f"== 13.7 [최신 시장 {m.as_of}] 시간 격자 수렴 (5,000경로)")
    fx = Trade([Leg("KRW", 1e8 * 1318.85, "receive"), Leg("USD", 1e8, "pay")], 1.0, "FXFWD")
    fx = par_client(fx, m)
    cases = [("USD/KRW 1년 FX 선도, 마진 1원", fx, DEALER_VM, 1.0),
             ("USD/KRW 5년 CCS, 10bp", usdkrw_client(m), DEALER, 10.0)]
    for name, c, dl, fee in cases:
        out, res = [], []
        for dt, steps in ((0.25, 4), (0.25, 24), (0.05, 24), (0.01, 100)):
            p = BankParams(n_paths=5000, dt=dt, min_steps=steps)
            d = build_deal(c, CORP, dl, hedge=market_hedge(c, m), fee=fee)
            r = run(d, m, p)
            be = breakeven(d, m, p, base=r)
            unit = be.extra if c.product == "FXFWD" else be.extra_bp_pa
            out.append(f"dt {dt}/최소 {steps}: E/L {r.el / 1e6:.2f}백만, 손익분기 {fee + unit:.3f}")
            res.append((r.el, fee + unit))
        print(f"  {name}\n    " + "\n    ".join(out))
        C.ok(f"격자 수렴 {name}: 기본(0.25/24)과 가장 촘촘한 격자의 E/L 차이 2% 이하",
             abs(res[1][0] / res[3][0] - 1) <= 0.02, f"{res[1][0] / res[3][0] - 1:+.2%}")
        C.ok(f"격자 수렴 {name}: 손익분기 차이 0.5% 이하", abs(res[1][1] / res[3][1] - 1) <= 0.005,
             f"{res[1][1] / res[3][1] - 1:+.2%}")


def ccp_im_check():
    m = load()
    c = irs_trade(m, "KRW", 5.0)
    s = simulate(m, c.currencies, m.end_time(c), BankParams(n_paths=1000), local=True)
    im = ccp_im_profile(c, s.times, m, s.sigma["KRW"])
    print(f"  CCP IM t=0 {im[0] / OKU:.2f}억 (명목 대비 {im[0] / 1000e8:.2%})")


def fva_check():
    print("== 13.6b 해석적 FVA - 시뮬레이션 E[B] 기준 FVA (엔/원 공공기관 A 건, 20,000경로, seed 1~6)")
    for mk in (load_legacy, load):
        m = mk()
        name = m.as_of + ("" if mk is load_legacy else " [최신]")
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
        if mk is load_legacy:
            C.ok("해석적 FVA - 시뮬레이션 FVA 평균 차이가 FVA의 1% 이하",
                 abs(diffs.mean()) <= 0.01 * r.costs["FVA (조달)"] / OKU, f"{diffs.mean():+.4f}")
            C.ok("차이 평균이 표준오차(seed 6개)의 3배 이내", abs(diffs.mean()) <= 3 * diffs.std(ddof=1) / np.sqrt(6))


TASKS = {"client_a": client_a, "equiv": equiv, "hedge": hedge, "mc": mc, "irs": irs, "martingale": martingale, "fva": fva_check, "grid": grid}

if __name__ == "__main__":
    try:
        dump = "--dump" in sys.argv
        for k in [a for a in sys.argv[1:] if a != "--dump"] or TASKS:
            t0 = time.time()
            TASKS[k]()
            print(f"  ({time.time() - t0:.0f}초)")
        if dump:
            print("EXPECTED = {")
            for k, v in GOT.items():
                print(f'    "{k}": {v:.3f},')
            print("}")
        else:
            C.finish()
    except FileNotFoundError as e:   # 스냅샷은 로컬 전용 (L-018)
        sys.exit(str(e))
