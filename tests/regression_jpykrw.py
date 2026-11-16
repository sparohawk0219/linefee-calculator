"""회귀 테스트: 엔/원 30년 CCS 중개(공공기관 A 4.13% / 딜러 B 4.03%, 연 10bp)를 새 엔진으로 재현한다.

기존 모델(xva_model.py, 2026-11-11 스냅샷) 결과와 나란히 출력한다.
    python -m tests.regression_jpykrw
"""
import sys
import time

from assumptions import Assumptions
from linefee.market import load_legacy
from tests._check import Checker
from linefee.pricing import breakeven, run
from linefee.trade import CSA, BankParams, Counterparty, Leg, Trade, build_deal
from xva_model import compute_line_fee

OKU = 1e8


def hurdle_txt(r) -> str:
    h = r.breakeven_hurdle()
    return "0% 미만" if h is None else ("50% 이상" if h >= 0.5 else f"{h:.1%}")


def jpykrw_deal(m, fee_location="client"):
    krw_notional = 1e10 * m.spot_quote["JPY"]
    client = Trade(legs=[
        Leg("KRW", krw_notional, "receive", rate=0.0403, day_count="ACT/360"),
        Leg("JPY", 1e10, "pay", rate=0.0407),
    ], maturity=30)
    client_a = Counterparty("공공기관 A", credit_rw=0.0, cva_rw=0.01, csa=CSA())
    dealer_b = Counterparty("딜러 B", credit_rw=0.50, cva_rw=0.05, csa=CSA(vm=True, im=True))
    return build_deal(client, client_a, dealer_b, fee_location=fee_location, fee=10.0)


# 기대값 (엔/원 검토 기준 스냅샷 = 날짜 고정, 20,000경로, seed 11, 억원). 지금 코드가 내는 값이다.
# 방법론을 바꾼 뒤에는 이 표와 METHODOLOGY_v1.md 13장을 함께 갱신한다. 허용오차는 TOL (표시 자릿수 반올림).
TOL = 0.006
EXPECTED = {
    "day1": 17.065,           # 첫날 이익
    "v0_client": 96.392,
    "v0_hedge": -79.327,
    "net": 1.142,             # 순마진
    "total_cost": 15.922,
    "be_extra_bp": -0.692,    # 손익분기까지 고객 다리 추가 bp
    "net_hedge_fee": 1.970,   # 구조 B (수수료를 딜러 B 다리에서 차감) 순마진
    "old_total": 17.447,      # 기존 모델 (xva_model.py)
    "old_fee_income": 17.134,
    "old_net": -0.313,
}
COST_EXPECTED = {"KVA (고객 신용)": 0.0, "KVA (고객 CVA)": 12.171, "KVA (딜러 신용)": 0.093, "KVA (딜러 CVA)": 0.314,
                 "FVA (조달)": 3.344}


def measure(m, p):
    """새 엔진과 기존 모델 결과 (억원, bp)."""
    r = run(jpykrw_deal(m), m, p)
    be = breakeven(jpykrw_deal(m), m, p, base=r)
    rb = run(jpykrw_deal(m, "hedge"), m, p)
    old = compute_line_fee(Assumptions())
    n = Assumptions().notional_krw_oku
    got = {"day1": r.day1 / OKU, "v0_client": r.v0_client / OKU, "v0_hedge": r.v0_hedge / OKU, "net": r.net / OKU,
           "total_cost": r.total_cost / OKU, "be_extra_bp": be.extra_bp_pa, "net_hedge_fee": rb.net / OKU,
           "old_total": old.total * n, "old_fee_income": old.fee_income * n, "old_net": old.net_margin * n}
    return r, be, rb, old, n, got, {k: v / OKU for k, v in r.costs.items()}


def main():
    m = load_legacy()
    p = BankParams(n_paths=20000)
    t0 = time.time()
    r, be, rb, old, n, got, costs = measure(m, p)
    print(f"새 엔진 ({time.time() - t0:.1f}초, {p.n_paths}경로, 엔/원 검토 기준 {m.as_of})")
    print(f"  첫날 이익 {r.day1 / OKU:6.1f}억  (고객 V0 {r.v0_client / OKU:.1f}, 헤지 V0 {r.v0_hedge / OKU:.1f})")
    for k, v in r.costs.items():
        print(f"  {k:<22}{v / OKU:6.1f}억")
    print(f"  비용 합계 {r.total_cost / OKU:6.1f}억, 순마진 {r.net / OKU:6.2f}억, "
          f"손익분기 세후 허들 {hurdle_txt(r)}")
    print(f"  손익분기: 고객 다리 추가 {be.extra_bp_pa:+.2f}bp (반복 {len(be.runs)}회)")
    print("기존 모델")
    for name, v in old.line_fee_items + old.hedge_cost_items:
        print(f"  {name:<22}{v * n:6.1f}억")
    print(f"  비용 합계 {old.total * n:6.1f}억, 수수료 {old.fee_income * n:.1f}억, 순마진 {old.net_margin * n:6.2f}억")
    print(f"구조 B(수수료 딜러 B 다리): 순마진 {rb.net / OKU:.2f}억, 허들 {hurdle_txt(rb)} "
          f"(기존 +0.5억, 11.5%)")

    print("단정 (엔/원 검토 기준 스냅샷 고정값)")
    c = Checker()
    if "--dump" in sys.argv:
        print("EXPECTED =", {k: round(v, 3) for k, v in got.items()})
        print("COST_EXPECTED =", {k: round(v, 3) for k, v in costs.items()})
        return
    for k, want in EXPECTED.items():
        c.near(k, got[k], want, TOL)
    for k, want in COST_EXPECTED.items():
        c.near(f"비용 {k}", costs[k], want, TOL)
    c.ok("비용 항목 구성 불변", set(costs) == set(COST_EXPECTED), ", ".join(costs))
    # 구조적 성질
    c.near("순마진 = 첫날 이익 - 비용 합계 (항등)", r.net / OKU, (r.day1 - r.total_cost) / OKU, 1e-9)
    c.near("비용 합계 = 비용 항목의 합 (항등)", r.total_cost / OKU, sum(costs.values()), 1e-6)
    c.ok("구조 B 순마진이 구조 A보다 크다 (수수료를 딜러 B 다리에서 차감)", got["net_hedge_fee"] > got["net"])
    c.finish()


if __name__ == "__main__":
    try:
        main()
    except FileNotFoundError as e:   # 스냅샷은 로컬 전용 (L-018)
        sys.exit(str(e))
