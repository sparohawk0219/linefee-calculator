"""금리 평균회귀 kappa 추정 (DECISIONS M26, L-078). 합성 시계열로 추정기를, 스냅샷으로 엔진의 kappa 선택·sigma 재조정을 확인한다.

    python -m tests.test_kappa_estimate
"""
import sys
from dataclasses import replace

import numpy as np

from linefee.engine import REF_TENOR, hw_sigma_scale, rate_kappas, simulate
from linefee.market import KAPPA_TENORS, estimate_kappa, hw_vol_ratio, kappa_from_levels, load
from linefee.trade import BankParams

FAILS = []


def check(label, ok, detail=""):
    print(f"  {'ok  ' if ok else '실패'} {label}" + (f" ({detail})" if detail else ""))
    if not ok:
        FAILS.append(label)


def synthetic_levels(kappa: float, weeks: int = 520, sigma: float = 0.009, noise: float = 0.0004, seed: int = 1) -> dict:
    """1요인 Hull-White 주간 금리 수준 {만기: 배열}. 만기 tau 금리 변화 = B(tau)/tau x 요인 변화 + 만기별 독립 잡음."""
    rng = np.random.default_rng(seed)
    dx = rng.standard_normal(weeks) * sigma / np.sqrt(52)
    out = {}
    for tau in KAPPA_TENORS:
        d = hw_vol_ratio(tau, kappa) * dx + rng.standard_normal(weeks) * noise / np.sqrt(52)
        out[tau] = 0.03 + np.concatenate([[0.0], np.cumsum(d)])
    return out


def test_estimator():
    print("추정기: 합성 1요인 Hull-White 시계열에서 kappa 복원")
    for true in (0.0, 0.03, 0.10, -0.02):
        k, vols = kappa_from_levels(synthetic_levels(true, noise=0.0))
        check(f"잡음 없음, 참값 {true:+.2f}", abs(k - true) < 1e-4, f"추정 {k:+.5f}")
    ks = [kappa_from_levels(synthetic_levels(0.05, seed=s))[0] for s in range(1, 11)]
    check("잡음 있음(만기별 독립 4bp/년), 참값 +0.05: 10개 표본 평균 오차 0.01 이하", abs(np.mean(ks) - 0.05) < 0.01,
          f"평균 {np.mean(ks):+.4f}, 표준편차 {np.std(ks):.4f}")
    check("변동성이 만기와 무관하면 kappa 0", abs(estimate_kappa({2: 0.01, 5: 0.01, 10: 0.01, 20: 0.01, 30: 0.01})) < 1e-4)


def test_engine():
    print("엔진: hist kappa 기본 사용, 관리자 입력 우선, 10년 변동성 유지")
    try:
        m = load()
    except FileNotFoundError as e:
        sys.exit(str(e))
    p = BankParams(n_paths=20000)
    if "kappa" not in m.hist:
        check("다만기 과거 금리가 없는 스냅샷이면 kappa 0", rate_kappas(m, ["USD", "KRW"], p) == {"USD": 0.0, "KRW": 0.0})
    mk = replace(m, hist={**m.hist, "kappa": {"USD": 0.04, "KRW": 0.02, "KRW_irs": 0.06}})
    check("hist kappa를 기본으로", rate_kappas(mk, ["USD", "KRW", "JPY"], p) == {"USD": 0.04, "KRW": 0.02, "JPY": 0.0})
    check("원화 IRS(local)는 KRW IRS 추정치", rate_kappas(mk, ["KRW"], p, local=True) == {"KRW": 0.06})
    check("관리자 입력이 우선", rate_kappas(mk, ["USD", "KRW"], replace(p, rate_mean_reversion=0.01))
          == {"USD": 0.01, "KRW": 0.01})
    k = 0.04
    check("sigma 배수 x B(10)/10 = 1 (10년 변동성 유지)", abs(hw_sigma_scale(k) * hw_vol_ratio(REF_TENOR, k) - 1) < 1e-12)
    s = simulate(mk, {"USD", "KRW"}, 12.0, p)
    i = int(np.argmin(np.abs(s.times - 1.0)))   # 1년 뒤 10년 제로금리 변화 = B(10)/10 x x(1)
    vol10 = (hw_vol_ratio(REF_TENOR, s.kappa["USD"]) * s.x["USD"][:, i]).std() / np.sqrt(s.times[i])
    target = s.rate_vol["USD"] * np.sqrt((1 - np.exp(-2 * k * s.times[i])) / (2 * k) / s.times[i])
    check("시뮬레이션 10년 금리 변동성 = 과거 10년 변동성 (첫해 평균회귀 감쇠 반영)", abs(vol10 / target - 1) < 0.03,
          f"{vol10 * 1e4:.1f}bp vs {target * 1e4:.1f}bp")


if __name__ == "__main__":
    test_estimator()
    test_engine()
    print("실패: " + (", ".join(FAILS) if FAILS else "없음"))
    sys.exit(1 if FAILS else 0)
