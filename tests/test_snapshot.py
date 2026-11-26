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
    snap = {k: {str(n): 1.0 for n in g} for k, g in TICKERS.items()} | {"missing": []}
    snap["hist"] = {"labels": [], "sigma": {}, "corr": []}
    return snap


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
    print("스냅샷 값 검사 (L-026), 선택 그룹 누락 시 load (L-027)")
    s = full(); s["usd_ois"] = {k: 0.0437 for k in s["usd_ois"]}
    check("금리가 소수 단위 -> 오류", raises(s, "usd_ois", "소수"))
    s = full(); s["spot"]["USDJPY"] = -8.4
    check("환율 음수 -> 오류", raises(s, "spot.USDJPY"))
    s = full(); s["usd_ois"]["5Y"] = float("nan")
    check("NaN -> 오류", raises(s, "usd_ois.5Y"))
    s = full(); s["usd_ois"]["5Y"] = None
    check("None -> 오류", raises(s, "usd_ois.5Y"))
    s = full(); s["usd_ois"]["5Y"] = "4.37"
    check("문자열 -> 오류", raises(s, "usd_ois.5Y"))
    s = full(); del s["hist"]
    check("hist 누락 -> 오류", raises(s, "hist"))
    s = full(); s["vol_USD"]["3"] = 0
    check("변동성 0 -> 오류", raises(s, "vol_USD.3"))
    s = full(); s["fx_pts_USD"]["1W"] = -0.1
    check("스왑포인트 음수는 정상", raises(s) is False)
    try:
        import json, tempfile
        from pathlib import Path

        from linefee.market import SNAPSHOT, load
        if SNAPSHOT.exists():
            snap = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
            for k in ("fx_pts_USD", "fx_pts_JPY"):
                snap.pop(k, None)
            with tempfile.TemporaryDirectory() as d:
                p = Path(d) / "s.json"
                p.write_text(json.dumps(snap), encoding="utf-8")
                m = load(p)
            check("스왑포인트 키가 파일에 없어도 load -> short_end=False", m.short_end is False)
    except Exception as e:
        check(f"스왑포인트 키 없는 스냅샷 load ({type(e).__name__}: {e})", False)
    sys.exit(1 if FAILS else 0)
