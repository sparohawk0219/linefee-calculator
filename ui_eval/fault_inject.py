"""오입력 주입: 페르소나 없이 화면만 보고, 일부러 틀리게 넣은 값을 화면이 알려 주는지 잰다.

    python -m ui_eval.fault_inject <run>      # ui_eval/runs/<run>/faults.json 을 쓴다. 종료 코드 0 (판정은 score --compare가 한다)

예시를 불러온 뒤 화면 계약 3번의 key로 값 하나를 틀리게 넣고, 계산 전과 계산 직후에 경고·오류가 뜨는지 본다 (일반 사용자 화면).
F8은 계산한 뒤 입력을 바꿔 결과가 숨겨지거나 '이전 입력 기준' 경고가 뜨는지 본다.
정상 대조군(고쳐 넣지 않거나 올바르게 바꾼 입력)에서 경고·오류가 뜨면 오경고다.
"""
import json
import re
import sys
import tempfile
from pathlib import Path

from ui_eval import backend as bk
from ui_eval.contract import ROOT

# (id, 예시, 설명, 입력 key, 바꾸는 함수(현재값 → 틀린 값))
FAULTS = [
    ("F1", 4, "irs_amt x1000 (원 단위 혼동)", "irs_amt", lambda v: v * 1000),
    ("F2", 2, "cp_amt x2 (한쪽 다리만, 명목 불일치)", "cp_amt", lambda v: v * 2),
    ("F3", 2, "cr_rate x100 (%를 bp로)", "cr_rate", lambda v: v * 100),
    ("F4", 2, "fee_bp x100 (라인피 단위)", "fee_bp", lambda v: v * 100),
    ("F5", 2, "cp_spread = 2500 (스프레드 단위)", "cp_spread", lambda v: 2500.0),
    ("F6", 3, "cr_amt / 100 (FX 선도 원화 금액 단위)", "cr_amt", lambda v: v / 100),
    ("F7", 1, "cr_rate / 100 (%를 소수로)", "cr_rate", lambda v: v / 100),
]
STALE_KEY = {1: "mat", 2: "mat", 3: "mat", 4: "irs_amt", 5: "irs_amt"}


def _norm(msg: str) -> str:
    """경고 글을 숫자만 다른 것끼리 같게 본다."""
    return re.sub(r"\d[\d,]*(?:\.\d+)?", "#", msg)


def alerts(els: list) -> list:
    return [e["label"] for e in els if e["kind"] in bk.ALERT_KINDS]


def _new(els: list, base: set) -> list:
    return [a for a in alerts(els) if _norm(a) not in base]


def _el(b, key: str):
    return next((e for e in b.elements() if e["key"] == key or e["id"] == key), None)


def _calc(b):
    btn = next((e for e in b.elements() if e["kind"] == "button" and e["label"] == "계산"), None)
    if btn is None:
        raise LookupError("'계산' 버튼이 없다")
    b.click(btn["id"])


def _load(b, k: int):
    pre = _el(b, "preset")
    if pre is None:
        raise LookupError("예시 위젯(preset)이 없다")
    b.set("preset", pre["options"][k - 1])


def _open(data_dir: Path):
    return bk.open_backend(data_dir, None)


def run_fault(data_dir: Path, base0: set, fid, k, desc, key, fn) -> dict:
    out = {"id": fid, "example": k, "desc": desc}
    try:
        b = _open(data_dir)
        _load(b, k)
        base = base0 | {_norm(a) for a in alerts(b.elements())}
        e = _el(b, key)
        if e is None or e["kind"] != "number":
            return {**out, "caught": "not_run", "reason": f"입력 key '{key}'가 화면에 없다"}
        wrong = fn(e["value"])
        b.set(e["id"], wrong)
        out["value"] = [e["value"], wrong]
        if _new(b.elements(), base):
            return {**out, "caught": "before"}
        _calc(b)
        if _new(b.elements(), base):
            return {**out, "caught": "after"}
        return {**out, "caught": "none"}
    except (LookupError, KeyError, ValueError) as ex:
        return {**out, "caught": "not_run", "reason": f"{type(ex).__name__}: {ex}"}


def run_stale(data_dir: Path, k: int) -> dict:
    key = STALE_KEY[k]
    out = {"id": f"F8-{k}", "example": k, "desc": f"계산 뒤 {key} 변경 (오래된 결과)"}
    try:
        b = _open(data_dir)
        _load(b, k)
        _calc(b)
        e = _el(b, key)
        if e is None or e["kind"] != "number":
            return {**out, "caught": "not_run", "reason": f"입력 key '{key}'가 화면에 없다"}
        new = e["value"] * 0.5 if key == "irs_amt" else (e["value"] - 1 if e["value"] > 1 else e["value"] + 1)
        b.set(e["id"], new)
        els = b.elements()
        hidden = not any(x["kind"] == "metric" for x in els)
        flagged = any("이전 입력 기준" in a for a in alerts(els))
        out["flagged"] = "result_hidden" if hidden else ("warning" if flagged else None)
        return {**out, "caught": "after" if out["flagged"] else "none"}
    except (LookupError, KeyError, ValueError) as ex:
        return {**out, "caught": "not_run", "reason": f"{type(ex).__name__}: {ex}"}


def run_normal(data_dir: Path, base0: set, name: str, k: int, changes: list) -> dict:
    """changes: [(key, 함수)]. 올바른 변경 뒤 경고·오류가 뜨면 오경고."""
    out = {"name": name, "example": k}
    try:
        b = _open(data_dir)
        _load(b, k)
        for key, fn in changes:
            e = _el(b, key)
            if e is None:
                return {**out, "alarm": None, "reason": f"입력 key '{key}'가 화면에 없다"}
            b.set(e["id"], fn(e["value"]))
        _calc(b)
        extra = _new(b.elements(), base0)
        return {**out, "alarm": bool(extra), "messages": extra[:3]}
    except (LookupError, KeyError, ValueError) as ex:
        return {**out, "alarm": None, "reason": f"{type(ex).__name__}: {ex}"}


NORMALS = [("예시 1", 1, []), ("예시 2", 2, []), ("예시 3", 3, []), ("예시 4", 4, []), ("예시 5", 5, []),
           ("예시 4, 만기 7", 4, [("mat", lambda v: 7.0)]), ("예시 5, 명목 200", 5, [("irs_amt", lambda v: 200.0)]),
           ("예시 2, 두 다리 명목 절반", 2, [("cr_amt", lambda v: v / 2), ("cp_amt", lambda v: v / 2)])]


def inject() -> dict:
    with tempfile.TemporaryDirectory(prefix="linefee_fault_") as tmp:
        data = Path(tmp)
        b0 = _open(data)
        base0 = {_norm(a) for a in alerts(b0.elements())}   # 아무것도 하지 않은 첫 화면에 늘 있는 경고는 뺀다
        faults = [run_fault(data, base0, *f) for f in FAULTS]
        stale = [run_stale(data, k) for k in range(1, 6)]
        normals = [run_normal(data, base0, *n) for n in NORMALS]
    ran = [f for f in faults if f["caught"] != "not_run"]
    caught = [f for f in ran if f["caught"] in ("before", "after")]
    nr = [{"id": f["id"], "reason": f.get("reason")} for f in faults + stale if f["caught"] == "not_run"]
    nr += [{"id": f"정상:{n['name']}", "reason": n.get("reason")} for n in normals if n["alarm"] is None]
    stale_ran = [f for f in stale if f["caught"] != "not_run"]
    ran_n = [n for n in normals if n["alarm"] is not None]
    return {"faults": faults, "stale": stale, "normal": normals,
            "catch_rate": (len(caught) / len(ran)) if ran else 0.0,
            "caught_before": sum(f["caught"] == "before" for f in faults),
            "caught_after": sum(f["caught"] == "after" for f in faults),
            "false_alarm_rate": (sum(n["alarm"] for n in ran_n) / len(ran_n)) if ran_n else 1.0,
            "stale_unflagged": sum(f["caught"] == "none" for f in stale_ran),
            "not_run": nr}


def main(argv=None) -> int:
    for s in (sys.stdout, sys.stderr):
        s.reconfigure(encoding="utf-8")
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print(__doc__)
        return 2
    res = inject()
    out = ROOT / "ui_eval" / "runs" / argv[0] / "faults.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    for f in res["faults"] + res["stale"]:
        print(f"  {f['id']:<5} {f['caught']:<8} {f['desc']}" + (f"  ({f['reason']})" if f.get("reason") else ""))
    for n in res["normal"]:
        print(f"  정상  {'오경고' if n['alarm'] else ('실행불가' if n['alarm'] is None else '조용함'):<8} {n['name']}"
              + (f"  {n['messages']}" if n.get("messages") else ""))
    print(f"포착률 {res['catch_rate']:.0%} (계산 전 {res['caught_before']}, 계산 후 {res['caught_after']}), "
          f"오경고율 {res['false_alarm_rate']:.0%}, 오래된 결과 미표시 {res['stale_unflagged']}, 실행 못한 것 {len(res['not_run'])}")
    print(f"저장: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
