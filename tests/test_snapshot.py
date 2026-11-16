"""스냅샷 필수 키·테너 검사 단정 (블룸버그 없이, 합성 스냅샷). python -m tests.test_snapshot"""
import copy
import sys

from linefee.market import TICKERS, check_snapshot

FAILS = []


def check(label, ok):
    print(f"  {'ok  ' if ok else '실패'} {label}")
    if not ok:
        FAILS.append(label)


def full():
    return {k: {str(n): 1.0 for n in g} for k, g in TICKERS.items()} | {"missing": []}


def raises(snap, *words):
    try:
        check_snapshot(snap)
    except ValueError as e:
        return all(w in str(e) for w in words)
    return False


if __name__ == "__main__":
    print("스냅샷 필수 키·테너 (L-015)")
    check("완전한 스냅샷 통과", raises(full()) is False)
    s = full(); del s["usd_ois"]["10Y"]
    check("usd_ois 10Y 누락 -> 오류에 usd_ois.10Y", raises(s, "usd_ois.10Y"))
    s = full(); del s["vol_USDJPY"]
    check("vol_USDJPY 통째 누락 -> 오류", raises(s, "vol_USDJPY.1"))
    s = full(); del s["spot"]["USDJPY"]
    check("spot.USDJPY 누락 -> 오류", raises(s, "spot.USDJPY"))
    s = full(); s["fx_pts_USD"] = {}; s["fx_pts_JPY"] = {}
    check("스왑포인트는 선택 (없어도 통과)", raises(s) is False)
    sys.exit(1 if FAILS else 0)
