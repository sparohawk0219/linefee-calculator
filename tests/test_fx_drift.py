"""환율 경로의 드리프트와 분산 증분 일관성 (L-033): 내재변동성 기간구조가 역전되어 총분산 sigma(t)^2 t가 줄어드는
구간이 있어도 경로 평균 E[X(t)] = 선도 F(t)이고, 로그 경로 분산 = 엔진이 쓴 총분산 V(t) (줄지 않음).

    python -m tests.test_fx_drift
"""
import sys
from dataclasses import replace

import numpy as np

from linefee.engine import fx_total_variance, simulate
from linefee.market import load
from linefee.trade import BankParams

INVERTED = {1: 0.20, 3: 0.11, 5: 0.11, 7: 0.11, 10: 0.11}   # 가상: 1년 20%, 3년 이후 11% (총분산이 1~3년에 감소)


def check(verbose: bool = True) -> list[str]:
    try:
        m = load()
    except FileNotFoundError as e:
        sys.exit(str(e))
    fails = []

    def ok(label, cond, detail=""):
        if verbose:
            print(f"    {'ok  ' if cond else '실패'} {label}" + (f" ({detail})" if detail else ""))
        if not cond:
            fails.append(label)

    for name, vol in (("스냅샷 변동성", m.fx_vol["USD"]), ("역전 변동성(가상)", INVERTED)):
        mm = replace(m, fx_vol={**m.fx_vol, "USD": vol})
        s = simulate(mm, {"USD", "KRW"}, 5.0, BankParams(n_paths=40000, antithetic=False))
        x = s.fx["USD"][:, 1:]
        fwd = mm.fwd_fx("USD", s.times)[1:]
        z = np.abs(x.mean(axis=0) / fwd - 1) / (x.std(axis=0, ddof=1) / fwd / np.sqrt(x.shape[0]))
        ok(f"{name}: E[X(t)] = F(t) (모든 시점 4 표준오차 이내)", bool((z < 4).all()), f"최대 z {z.max():.2f}")
        v = fx_total_variance(mm, "USD", s.times)
        ok(f"{name}: 총분산 V(t)는 줄지 않는다", bool((np.diff(v) >= 0).all()))
        lv = np.log(s.fx["USD"][:, 1:]).var(axis=0, ddof=1)
        ratio = lv / v[1:]
        ok(f"{name}: 로그 경로 분산 / V(t) = 1 ± 4%", bool(np.all(np.abs(ratio - 1) < 0.04)),
           f"{ratio.min():.3f} ~ {ratio.max():.3f}")
    tv = np.array([INVERTED[1] ** 2 * 1, INVERTED[3] ** 2 * 3])
    ok("가상 변동성은 실제로 총분산이 줄어드는 경우", bool(tv[1] < tv[0]), f"{tv[0]:.4f} -> {tv[1]:.4f}")
    return fails


def test_fx_drift():
    assert check(verbose=False) == []


if __name__ == "__main__":
    sys.exit(1 if check() else 0)
