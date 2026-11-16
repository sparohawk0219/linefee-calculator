"""금리 평균회귀 kappa의 부호별 엔진 일관성 (L-010): kappa < 0, = 0, > 0 모두에서
(1) 경로 표본분산이 모델 분산 s.var와 같고, (2) 할인계수 martingale (경로 평균 시가 = 오늘 커브 선도가치)이 성립한다.

    python -m tests.test_engine_kappa
"""
import sys

import numpy as np

from linefee.engine import simulate, trade_forward_value, trade_value_paths
from linefee.market import load
from linefee.trade import BankParams
from tests.verify_methodology import irs_trade, usdkrw_client

KAPPAS = (-0.05, 0.0, 0.05)
VAR_TOL = 0.04       # 40,000경로 표본분산의 상대 표준오차는 약 0.7%. 4% 이내


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

    for k in KAPPAS:
        p = BankParams(n_paths=40000, rate_mean_reversion=k)
        s = simulate(m, {"USD", "KRW"}, 5.0, p)
        for c in ("KRW", "USD"):
            idx = slice(4, None, 8)       # 처음 몇 시점은 분산이 너무 작아 건너뛰며 일부 시점만 본다
            ratio = s.x[c][:, idx].var(axis=0, ddof=1) / s.var[c][idx]
            ok(f"kappa={k:+.2f} {c} 경로 분산 / 모델 분산 = 1 ± {VAR_TOL:.0%}", bool(np.all(np.abs(ratio - 1) < VAR_TOL)),
               f"{ratio.min():.3f} ~ {ratio.max():.3f}")
        for name, tr in (("원화 IRS 5년", irs_trade(m, "KRW", 5.0)), ("USD/KRW 5년 CCS", usdkrw_client(m))):
            local = all(l.discount == "local" for l in tr.legs)
            s2 = simulate(m, tr.currencies, m.end_time(tr), p, local=local)
            v = trade_value_paths(tr, m, s2)[:, 1:]
            fwd = trade_forward_value(tr, m, s2.times)[1:]
            se = v.std(axis=0, ddof=1) / np.sqrt(v.shape[0])
            z = np.abs(v.mean(axis=0) - fwd) / se
            ok(f"kappa={k:+.2f} martingale {name}: 표준오차 3배 초과 시점이 5% 이하", (z > 3).mean() <= 0.05,
               f"{int((z > 3).sum())}/{len(z)}")
    return fails


def test_kappa_consistency():
    assert check(verbose=False) == []


if __name__ == "__main__":
    sys.exit(1 if check() else 0)
