"""회귀 테스트: 엔/원 30년 CCS 중개(공공기관 A 4.13% / 딜러 B 4.03%, 연 10bp)를 새 엔진으로 재현한다.

기존 모델(xva_model.py, 2026-11-11 스냅샷) 결과와 나란히 출력한다.
    python -m tests.regression_jpykrw
"""
import sys
import time

from assumptions import Assumptions
from linefee.market import load_legacy
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


def main():
    m = load_legacy()
    p = BankParams(n_paths=20000)
    t0 = time.time()
    r = run(jpykrw_deal(m), m, p)
    print(f"새 엔진 ({time.time() - t0:.1f}초, {p.n_paths}경로)")
    print(f"  첫날 이익 {r.day1 / OKU:6.1f}억  (고객 V0 {r.v0_client / OKU:.1f}, 헤지 V0 {r.v0_hedge / OKU:.1f})")
    for k, v in r.costs.items():
        print(f"  {k:<22}{v / OKU:6.1f}억")
    print(f"  비용 합계 {r.total_cost / OKU:6.1f}억, 순마진 {r.net / OKU:6.2f}억, "
          f"손익분기 세후 허들 {hurdle_txt(r)}")
    be = breakeven(jpykrw_deal(m), m, p, base=r)
    print(f"  손익분기: 고객 다리 추가 {be.extra_bp_pa:+.2f}bp (반복 {len(be.runs)}회)")

    old = compute_line_fee(Assumptions())
    n = Assumptions().notional_krw_oku
    print("기존 모델")
    for name, v in old.line_fee_items + old.hedge_cost_items:
        print(f"  {name:<22}{v * n:6.1f}억")
    print(f"  비용 합계 {old.total * n:6.1f}억, 수수료 {old.fee_income * n:.1f}억, 순마진 {old.net_margin * n:6.2f}억")

    rb = run(jpykrw_deal(m, "hedge"), m, p)
    print(f"구조 B(수수료 딜러 B 다리): 순마진 {rb.net / OKU:.2f}억, 허들 {hurdle_txt(rb)} "
          f"(기존 +0.5억, 11.5%)")


if __name__ == "__main__":
    try:
        main()
    except FileNotFoundError as e:   # 스냅샷은 로컬 전용 (L-018)
        sys.exit(str(e))
