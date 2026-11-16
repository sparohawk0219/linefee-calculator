"""10영업일 하한 (L-004: 267.카.(1) MF, 267.자.(1) 감독 듀레이션 E). 하한은 잔존만기가 10/250년보다 짧은 노드에만 걸리고
만기 시점 노드(잔존 0)는 0으로 남는다. 기본 격자 간격은 min(0.25년, 만기/24)라 만기 약 0.96년 미만 거래에서는 마지막 노드 직전에 하한이 걸린다.

    python -m tests.test_regcap_floor
"""
import sys

import numpy as np

from linefee.regcap import MIN_HOLD, floor_remaining


def check() -> list[str]:
    r = np.array([0.0, 1e-12, 0.01, MIN_HOLD, 0.5, 3.0])
    f = floor_remaining(r)
    want = np.array([0.0, 0.0, MIN_HOLD, MIN_HOLD, 0.5, 3.0])
    rows = [("만기 시점 노드는 0, 짧은 잔존은 10/250년으로, 긴 잔존은 그대로", np.allclose(f, want, atol=0, rtol=0))]
    for label, ok in rows:
        print(f"    {'ok  ' if ok else '실패'} {label}")
    return [label for label, ok in rows if not ok]


def test_floor():
    assert check() == []


if __name__ == "__main__":
    sys.exit(1 if check() else 0)
