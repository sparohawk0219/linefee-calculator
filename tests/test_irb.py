"""기본내부등급법 기업 RW 단정 (스냅샷 없이). python -m tests.test_irb

근거: 은행업감독업무시행세칙 별표3 120(산식), 123(PD 하한 0.05%), 124.나(감독 LGD), 126(유효만기).
"""
import sys

import numpy as np

from linefee.regcap import IRB_PD_FLOOR, MIN_HOLD, capital_paths, credit_rw_profile, irb_rw
from linefee.trade import CSA, BankParams, Counterparty

fails = []


def check(name, ok, detail=""):
    print(("  ok   " if ok else "  FAIL ") + name + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        fails.append(name)


# 바젤 예시값: PD 1%, LGD 45%, M 2.5 → RW 92.32% (1.06 승수 없음)
rw = float(irb_rw(0.01, 0.45, 2.5))
check("PD 1%, LGD 45%, M 2.5 → 92.32%", abs(rw - 0.9232) < 1e-4, f"{rw:.6f}")
check("감독 LGD 40%면 RW도 40/45배", abs(float(irb_rw(0.01, 0.40, 2.5)) - rw * 40 / 45) < 1e-12)
check("PD 하한 0.05%", float(irb_rw(0.0001, 0.4, 2.5)) == float(irb_rw(IRB_PD_FLOOR, 0.4, 2.5)))
ms = np.array([0.5, 1.0, 2.5, 5.0])
r = irb_rw(0.003, 0.4, ms)
check("유효만기가 길수록 RW가 크다", bool(np.all(np.diff(r) > 0)))
check("PD가 클수록 RW가 크다 (0.1% < 1% < 5%)",
      float(irb_rw(0.001, 0.4, 2.5)) < float(irb_rw(0.01, 0.4, 2.5)) < float(irb_rw(0.05, 0.4, 2.5)))
check("부도(PD 100%)는 RW 0 (기본내부등급법 EL_best = LGD)", float(irb_rw(1.0, 0.4, 2.5)) == 0.0)

# 중소기업 상관계수 조정: 매출 70억 이하 최대(-0.04), 700억에서 0, 700억 초과 미적용
big = float(irb_rw(0.01, 0.4, 2.5))
check("매출 700억은 조정 0", abs(float(irb_rw(0.01, 0.4, 2.5, 700)) - big) < 1e-12)
check("매출 701억은 조정 없음", float(irb_rw(0.01, 0.4, 2.5, 701)) == big)
check("매출 70억 미만은 70억으로 봄", float(irb_rw(0.01, 0.4, 2.5, 10)) == float(irb_rw(0.01, 0.4, 2.5, 70)))
check("중소기업은 RW가 작다", float(irb_rw(0.01, 0.4, 2.5, 100)) < big)

# 시점별 RW: 잔존 5년 초과는 5년 상한, 10영업일 하한
cp = Counterparty("기업", 1.0, 0.03, 0.01, 0.6, CSA(), irb=True)
prof = credit_rw_profile(cp, np.array([10.0, 5.0, 0.001]))
check("잔존 10년도 M 5년 상한", abs(prof[0] - prof[1]) < 1e-12)
check("잔존 0.001년은 M 10영업일", abs(prof[2] - float(irb_rw(0.01, 0.4, MIN_HOLD))) < 1e-12)
check("고정 RW 상대방은 credit_rw 그대로",
      credit_rw_profile(Counterparty("x", 0.5, 0.0), np.array([1.0])) == 0.5)

# capital_paths: (경로 x 시점) EAD에 시점별 RW가 곱해진다
times = np.array([0.0, 1.0, 2.0, 3.0])
ead = np.ones((2, 4)) * 100.0
cap = capital_paths(ead, cp, times, BankParams(), end=3.0)
m = np.where(3.0 - times > 1e-9, np.maximum(3.0 - times, MIN_HOLD), 0.0)
want = 100.0 * credit_rw_profile(cp, m)
check("capital_paths rwa_ccr = EAD x RW(M(t))", bool(np.allclose(cap["rwa_ccr"], want[None, :])))

print("실패: " + (", ".join(fails) if fails else "없음"))
sys.exit(1 if fails else 0)
