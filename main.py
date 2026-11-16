"""라인 피 계산 실행.

    python main.py

가정은 assumptions.py에서 바꾼다. 항목별로 나눠서 출력한다.
    1. HF 익스포저와 PFE (라인 사용량)
    2. HF 신용 비용 (HF에서는 받는 것 없음)
    3. 딜러 B 헤지 비용
    4. 담보 유동성
    5. 손익: 딜러 B upfront - 비용
    6. 시나리오별 순마진
    7. 딜러 B 구조 비교 (30년 bullet vs 10년 mutual break)
"""
from dataclasses import replace

import numpy as np

from assumptions import Assumptions
from simm import simm
from xva_model import LineFeeResult, compute_line_fee, coupons, hf_coupon_krw, supervisory_duration


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def print_items(a: Assumptions, r: LineFeeResult, items, subtotal_name: str) -> None:
    n = a.notional_krw_oku
    print(f"{'항목':<26}{'명목 대비':>10}{'억원':>9}{'연 bp':>8}")
    for name, x in items:
        print(f"{name:<26}{x:>10.2%}{x * n:>9.1f}{r.to_running_bp(x):>8.1f}")
    total = sum(x for _, x in items)
    print(f"{subtotal_name:<26}{total:>10.2%}{total * n:>9.1f}{r.to_running_bp(total):>8.1f}")


def print_pfe(a: Assumptions, r: LineFeeResult) -> None:
    ex = r.exposure
    n = a.notional_krw_oku
    q_lo, q_hi = a.pfe_quantiles
    section("1. HF 익스포저와 PFE (명목 대비 / 억원)")
    print(f"{'시점':>5}{'EE':>16}{f'PFE {q_lo:.0%}':>18}{f'PFE {q_hi:.0%}':>18}")
    for year in (1, 3, 5, 10, 15, 20, 25, 29):
        i = int(round(year / a.dt))
        ee, p_lo, p_hi = ex.epe_hf[i], ex.pfe_hf[q_lo][i], ex.pfe_hf[q_hi][i]
        print(f"{year:>4}년{ee:>9.1%}{ee * n:>6.0f}억"
              f"{p_lo:>11.1%}{p_lo * n:>6.0f}억{p_hi:>11.1%}{p_hi * n:>6.0f}억")
    for q in a.pfe_quantiles:
        pfe = ex.pfe_hf[q]
        i_peak = int(np.argmax(pfe))
        print(f"PFE {q:.0%} 최대: {pfe[i_peak]:.1%} ({pfe[i_peak] * n:.0f}억원, {ex.times[i_peak]:.1f}년), "
              f"기간 평균: {pfe.mean():.1%} ({pfe.mean() * n:.0f}억원)")
    print(f"참고) SA-CCR EAD t=0: {r.capital.ead_hf[0]:.1%} ({r.capital.ead_hf[0] * n:.0f}억원), "
          f"CEM 신용환산액 t=0: 7.5% ({0.075 * n:.0f}억원)")


def print_dealer_b_capital_breakdown(a: Assumptions, r: LineFeeResult) -> None:
    """t=0 기준 딜러 B SA-CCR EAD와 자본이 어떻게 나오는지 단계별로 보여 준다."""
    n = a.notional_krw_oku
    sd = supervisory_duration(np.array(float(a.maturity_years)))
    fx_addon = 0.04
    ir_addon = 0.005 * sd if a.sa_ccr_ccs_treatment == "fx_and_ir" else 0.0
    addon = fx_addon + 2 * ir_addon
    mf = 1.5 * np.sqrt(a.regulatory_mpor_days / 250)
    c = r.capital
    mult = c.multiplier[0]
    ead = 1.4 * mult * mf * addon
    m, dfm = c.m_eff[0], (1 - np.exp(-0.05 * c.m_eff[0])) / (0.05 * c.m_eff[0])

    def line(label, x):
        print(f"  {label:<44}{x:>8.2%}{x * n:>8.1f}억")

    section("3-1. 딜러 B 규제자본 분해 (t=0, 명목 대비 / 억원)")
    print(f"[SA-CCR EAD] = 1.4 x (RC + 승수 x MF x add-on), RC = 0 (VM이 V를 덮고 TH = 0), "
          f"분류: {a.sa_ccr_ccs_treatment}")
    line("FX add-on: 4% x 명목", fx_addon)
    if ir_addon:
        line(f"JPY 금리 add-on: 0.5% x SD({sd:.1f})", ir_addon)
        line(f"KRW 금리 add-on: 0.5% x SD({sd:.1f})", ir_addon)
    line("add-on 합계", addon)
    line(f"x MF {mf:.2f} (규제 MPOR {a.regulatory_mpor_days}영업일)", mf * addon)
    line(f"x 승수 {mult:.3f} (받은 IM {c.im[0]:.2%} x (1-{a.im_haircut:.0%}) 반영)", mult * mf * addon)
    line("x 1.4 = EAD", ead)
    print("[신용위험 자본] = EAD x RW x 목표비율")
    line(f"EAD x {a.rw_dealer_b:.0%} x {a.capital_ratio:.1%}", c.k_ccr_dealer_b[0])
    if a.cva_capital_method == "ba_cva":
        print("[BA-CVA 자본] = DS x RW / 1.4 x M x DF x EAD x 12.5 x 목표비율")
        print(f"  M = {m:.1f}년, DF = {dfm:.3f}, M x DF = {m * dfm:.1f} "
              f"→ EAD에 곱해지는 배수 {a.ba_cva_ds * a.ba_cva_rw_dealer_b / 1.4 * m * dfm:.3f}")
        line("BA-CVA 자본 (8% 기준)", c.k_cva_dealer_b[0] / (12.5 * a.capital_ratio))
        line(f"BA-CVA 자본 (목표비율 {a.capital_ratio:.1%})", c.k_cva_dealer_b[0])
        # 단독 기준 CVA RWA / CCR RWA = 12.5 x DS x RW_cva / 1.4 x M x DF / RW_ccr
        ratio = c.k_cva_dealer_b[0] / c.k_ccr_dealer_b[0]
        print(f"  CVA RWA / CCR RWA = 12.5 x {a.ba_cva_ds} x {a.ba_cva_rw_dealer_b:.0%} / 1.4"
              f" x {m * dfm:.1f} / {a.rw_dealer_b:.0%} = {ratio:.1f}배"
              f" (포트폴리오 한계계수 {a.ba_cva_marginal_factor} 반영)")
        # EAD는 분자·분모에서 지워지므로 비율은 만기와 위험가중치로만 정해진다
        per_m = 12.5 * a.ba_cva_ds * a.ba_cva_rw_dealer_b / a.ba_cva_alpha / a.rw_dealer_b
        cells = []
        for mat in (1, 3, 5, 10, 30):
            mdf = (1 - np.exp(-0.05 * mat)) / 0.05
            cells.append(f"{mat}년 {per_m * mdf * a.ba_cva_marginal_factor:.1f}배")
        print("  참고) 같은 상대방, 만기만 바꾼 비율: " + ", ".join(cells))
    elif a.cva_capital_method == "ccr_ratio":
        line(f"CVA 자본 = 신용위험 자본 x {a.cva_to_ccr_rwa_ratio:.0%} (은행 전체 평균)",
             c.k_cva_dealer_b[0])
    else:
        line("CVA 자본 = 신용위험 자본 (308 특례)", c.k_cva_dealer_b[0])
    total_k = c.k_ccr_dealer_b[0] + c.k_cva_dealer_b[0]
    print(f"  → t=0 연간 자본비용 {total_k * a.cost_of_capital * n:.2f}억원 "
          f"(명목 대비 {total_k * a.cost_of_capital * 1e4:.1f}bp)")
    # KVA는 아래 시점별 자본을 거래 기간 전체로 적분한 값이다.
    # M은 잔존만기로 줄지만, IM이 줄면서 승수와 EAD는 커진다.
    print(f"  시점별 (억원): {'년':>3}{'M':>6}{'IM':>7}{'승수':>7}{'EAD':>7}{'CCR 자본':>9}{'CVA 자본':>9}")
    for y in (0, 5, 10, 15, 20, 25, 29):
        i = int(round(y / a.dt))
        print(f"  {'':>14}{y:>3}{c.m_eff[i]:>6.1f}{c.im[i]:>7.2%}{c.multiplier[i]:>7.3f}"
              f"{c.ead_dealer_b[i] * n:>7.2f}{c.k_ccr_dealer_b[i] * n:>9.3f}{c.k_cva_dealer_b[i] * n:>9.3f}")


def print_liquidity(a: Assumptions, r: LineFeeResult) -> None:
    """딜러 B와 주고받을 VM 규모. 낸 VM은 공공기관 A에게서 담보를 못 받으므로 귀사가 조달해야 한다."""
    ex = r.exposure
    n = a.notional_krw_oku
    section("4. 딜러 B와 주고받을 VM (딜러 B 거래 기준)")
    print(f"{'시점':>5}{'기대값':>14}{'공공기관 A 익스포저 95%':>16}{'딜러 B에서 받을 담보 기대값':>24}")
    for year in (5, 10, 20):
        i = int(round(year / a.dt))
        q95 = ex.pfe_hf[0.95][i] if 0.95 in ex.pfe_hf else np.quantile(
            np.maximum(ex.value_vs_hf[:, i], 0), 0.95)
        print(f"{year:>4}년{ex.vm_posted[i]:>9.1%}{ex.vm_posted[i] * n:>5.0f}억"
              f"{q95:>10.1%}{q95 * n:>5.0f}억"
              f"{ex.vm_received[i]:>17.1%}{ex.vm_received[i] * n:>5.0f}억")
    print(f"FCA {r.to_running_bp(r.fca):.1f}bp, FBA {r.to_running_bp(r.fba):.1f}bp "
          f"→ FBA 인정 시 순 FVA {r.to_running_bp(r.fca - r.fba):.1f}bp")


def print_simm(a: Assumptions, r: LineFeeResult) -> None:
    """딜러 B IM (SIMM) 분해와 기간별 추이."""
    n = a.notional_krw_oku
    section(f"3-2. 딜러 B IM (ISDA SIMM v{a.simm_version}, 계산통화 {a.simm_calc_currency}, "
            f"원금 교환 {'포함' if a.simm_include_principal else '제외'})")
    if not a.im_csa:
        print("IM-CSA 없음")
        return
    c_jpy, c_krw = coupons(a)
    d = simm(0.0, a.market, c_jpy, c_krw, a.maturity_years, 1.0, a.simm_version,
             a.simm_calc_currency, a.simm_include_principal)
    for label, x in (("금리 delta: JPY (베이시스 포함)", d["k_jpy"]),
                     ("금리 delta: KRW (베이시스 포함)", d["k_krw"]),
                     ("금리 delta 합산 (통화간 상관 반영)", d["ir"]),
                     ("FX delta", d["fx"]),
                     ("IM (금리-FX 상관 반영)", d["im"])):
        print(f"  {label:<36}{x:>8.2%}{x * n:>8.1f}억")
    cells = [f"{y}년 {r.capital.im[int(round(y / a.dt))]:.2%}" for y in (0, 5, 10, 20, 25)]
    print("  기간별: " + ", ".join(cells))
    print(f"  낸 IM 조달 스프레드 {a.im_funding_spread * 1e4:.0f}bp → MVA "
          f"{r.to_running_bp(dict(r.hedge_cost_items)['IM 조달 (MVA)']):.1f}bp")


def main() -> None:
    base = Assumptions()
    r = compute_line_fee(base)
    m = base.market
    c_jpy, c_krw = coupons(base)

    print(f"시장 데이터 {m.as_of} (블룸버그), JPYKRW {m.jpykrw_spot:.4f}")
    print(f"명목 {base.notional_jpy_oku:.0f}억엔 = {base.notional_krw_oku:.0f}억원, "
          f"쿠폰 JPY {c_jpy:.3%} / KRW 딜러 B {base.coupon_krw:.2%}, 공공기관 A {hf_coupon_krw(base) * 360 / 365.25:.2%} {base.krw_day_count} "
          f"(연 {c_krw:.3%}), 원화 연금계수 {r.annuity_full:.2f}")
    print(f"선도환율 / 현물: 10년 {r.exposure.fwd_fx[int(10 / base.dt)]:.3f}, "
          f"30년 {r.exposure.fwd_fx[-1]:.3f}, "
          f"환율 변동성 {m.fx_vol(10):.1%}(10년)")

    print_pfe(base, r)

    section("2. HF 신용 비용 (HF에서는 받는 것 없음)")
    print_items(base, r, r.line_fee_items, "HF 신용 비용 소계")

    section("3. 딜러 B 헤지 비용 (중개하면서 귀사가 떠안는 비용)")
    print_items(base, r, r.hedge_cost_items, "헤지 비용 소계")
    print_dealer_b_capital_breakdown(base, r)
    print_simm(base, r)

    print_liquidity(base, r)

    print_pnl(base, r)

    section("6. 시나리오별 순마진 (연 bp: 비용 / 수수료 / 순마진)")
    scenarios = {
        "기본": base,
        "BA-CVA M = 현금흐름 가중": replace(base, ba_cva_maturity="cashflow"),
        "BA-CVA 한계계수 0.6 (분산 덜 됨)": replace(base, ba_cva_marginal_factor=0.6),
        "딜러 B RW 30%": replace(base, rw_dealer_b=0.30),
        "USD FTP SOFR + 25bp": replace(base, usd_funding_spread=0.0025, usd_benefit_spread=0.0025),
        "받은 담보 이익 미인정 (FBA 0)": replace(base, recognise_fba=False),
        "세후 요구수익률 5%": replace(base, hurdle_after_tax=0.05),
        "세후 요구수익률 7%": replace(base, hurdle_after_tax=0.07),
        "세후 요구수익률 9% (ROE 수준)": replace(base, hurdle_after_tax=0.09),
        "참고: 딜러 B upfront 220bp 구조": replace(base, fee_structure="upfront"),
        "KVA: 요구수익률 전액, 조달이익 없음": replace(base, kva_method="gross"),
        "KVA: 첫날 이익 자본 적립 미반영": replace(base, kva_method="net_funding"),
        "공사 CVA RW 5% (HF였다면)": replace(base, ba_cva_rw_hf=0.05),
        "공사와 방향 반대": replace(base, hf_reverse=True),
        "금리 요인 없음 (환율만, 이전 모델)": replace(base, rate_factors=False),
        "금리 변동성 x0.5": replace(base, rate_vol_scale=0.5),
        "금리 변동성 x1.5": replace(base, rate_vol_scale=1.5),
        "금리 평균회귀 5%": replace(base, rate_mean_reversion=0.05),
        "세 요인 상관 0": replace(base, rate_corr_override=np.eye(3).tolist()),
        "IM 없음 (VM만)": replace(base, im_csa=False),
        "SIMM 원금 교환 포함": replace(base, simm_include_principal=True),
        "SIMM v2.9+2606": replace(base, simm_version="2.9+2606"),
        "SIMM 계산통화 KRW": replace(base, simm_calc_currency="KRW"),
        "IM 조달 10bp": replace(base, im_funding_spread=0.001),
    }
    for name, a in scenarios.items():
        s = compute_line_fee(a)
        print(f"{name:<26}{s.to_running_bp(s.total):>7.1f}"
              f"{s.to_running_bp(s.fee_income):>8.1f}{s.to_running_bp(s.net_margin):>8.1f}")




def print_pnl(a: Assumptions, r: LineFeeResult) -> None:
    n = a.notional_krw_oku
    section(f"5. 손익 (수수료 {a.fee_structure}, 명목 대비 / 억원 / 연 bp)")
    for name, x in (("수수료 수입 (공공기관 A 쿠폰 가산 PV)" if a.fee_structure == "running" else "딜러 B upfront 수취", r.fee_income),
                    ("(-) HF 신용 비용", -r.line_fee),
                    ("(-) 딜러 B 헤지 비용", -r.hedge_cost),
                    ("= 순마진", r.net_margin)):
        print(f"{name:<26}{x:>10.2%}{x * n:>9.1f}{r.to_running_bp(x):>8.1f}")


if __name__ == "__main__":
    main()
