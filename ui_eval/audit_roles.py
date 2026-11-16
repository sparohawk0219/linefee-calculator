"""관리자 영역 노출 점검: 일반 사용자 화면에 은행 파라미터·마스터 편집 위젯이 없는지, 로그인하면 생기는지 본다.

    python -m ui_eval.audit_roles [<run>]     # 통과면 종료 코드 0. run을 주면 ui_eval/runs/<run>/audit.json 에도 쓴다

화면 계약 4, 5 (tools/prompts/B_COMMON.md). 역할이 아직 없는 화면에서는 "관리자 영역이 보인다"로 실패하는 것이 정상이다.
"""
import json
import sys
import tempfile
from pathlib import Path

from ui_eval import backend as bk
from ui_eval.contract import ADMIN_KEY_PREFIX, EVAL_ADMIN_PASSWORD, ROOT

# 은행 파라미터, 시뮬레이션, 마스터 편집 위젯의 라벨 조각 (일반 사용자 화면에 입력 위젯으로 있으면 안 된다)
# 시장 데이터 새로 받기(블룸버그)는 일반 사용자도 한다 (사용자 결정 2026-11-16, DECISIONS O5)
FORBIDDEN_LABELS = ["CET1 목표비율", "세후 요구수익률", "세율", "FTP", "KVA 조달", "한계계수", "IM 조달", "경로 수", "변동성 배수",
                    "평균회귀", "신용 위험가중치", "연 PD", "LGD", "BA-CVA 업종"]
_WIDGETS = bk.INPUT_KINDS + ("button",)


def exposure(els: list) -> dict:
    """화면 요소 목록에서 관리자 영역으로 보이는 것: adm_ key 목록과 금지 라벨에 걸린 입력 위젯 라벨 목록."""
    keys = [e["key"] for e in els if (e.get("key") or "").startswith(ADMIN_KEY_PREFIX)]
    labels = [e["label"] for e in els if e["kind"] in _WIDGETS and any(f in e["label"] for f in FORBIDDEN_LABELS)]
    return {"keys": keys, "labels": labels}


def _clean(x: dict) -> bool:
    return not x["keys"] and not x["labels"]


def _adm(els: list) -> list:
    return [e for e in els if (e.get("key") or "").startswith(ADMIN_KEY_PREFIX)]


def _login(b, pw: str):
    """로그인 화면: 비밀번호 칸(key admin_pw)에 넣고 '관리자 로그인'을 누른다."""
    b.set("admin_pw", pw)
    ids = [e["id"] for e in b.elements() if e["kind"] == "button" and e["label"] == "관리자 로그인"]
    if not ids:
        raise LookupError("'관리자 로그인' 버튼이 없다")
    b.click(ids[0])


def audit() -> dict:
    checks = []

    def check(name: str, ok: bool, detail: str = ""):
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    with tempfile.TemporaryDirectory(prefix="linefee_audit_") as d:
        b = bk.open_backend(Path(d), EVAL_ADMIN_PASSWORD)
        seen = exposure(b.elements())
        pre = next((e for e in b.elements() if e["key"] == "preset"), None)
        prod = next((e for e in b.elements() if e["key"] == "product"), None)
        if pre is None or prod is None:
            check("예시·상품 위젯(preset, product)이 있다", False, "계약 2, 3 위반: 화면을 돌릴 수 없다")
        else:
            calc = lambda: b.click(next(e["id"] for e in b.elements() if e["kind"] == "button" and e["label"] == "계산"))  # noqa: E731
            for opt in pre["options"]:
                b.set("preset", opt)
                x = exposure(b.elements())
                seen = {"keys": sorted(set(seen["keys"]) | set(x["keys"])),
                        "labels": sorted(set(seen["labels"]) | set(x["labels"]))}
            calc()
            x = exposure(b.elements())
            seen = {"keys": sorted(set(seen["keys"]) | set(x["keys"])), "labels": sorted(set(seen["labels"]) | set(x["labels"]))}
            for opt in prod["options"]:
                b.set("product", opt)
                x = exposure(b.elements())
                seen = {"keys": sorted(set(seen["keys"]) | set(x["keys"])),
                        "labels": sorted(set(seen["labels"]) | set(x["labels"]))}
            check("일반 사용자 화면(예시 5개, 상품 4개, 계산)에 관리자 영역이 보이지 않는다", _clean(seen),
                  "관리자 영역이 보인다: adm_ key " + str(seen["keys"][:6]) + ", 금지 라벨 " + str(seen["labels"][:6]))
        check("일반 사용자 화면의 역할은 general", b.role() == "general", f"role={b.role()}")

        # 틀린 비밀번호
        try:
            b2 = bk.open_backend(Path(d), EVAL_ADMIN_PASSWORD)
            _login(b2, "wrong-password")
            check("틀린 비밀번호로는 관리자 영역이 안 생긴다", not _adm(b2.elements()) and b2.role() == "general",
                  f"role={b2.role()}, adm_ {len(_adm(b2.elements()))}개")
            _login(b2, EVAL_ADMIN_PASSWORD)
            els = b2.elements()
            check("맞는 비밀번호로 role이 admin이 된다", b2.role() == "admin", f"role={b2.role()}")
            check("로그인하면 adm_ 요소가 1개 이상 생긴다", len(_adm(els)) >= 1, f"adm_ {len(_adm(els))}개")
            out = next((e["id"] for e in els if e["kind"] == "button" and e["label"] == "로그아웃"), None)
            if out is None:
                check("로그아웃하면 관리자 영역이 다시 사라진다", False, "'로그아웃' 버튼이 없다")
            else:
                b2.click(out)
                check("로그아웃하면 관리자 영역이 다시 사라진다", not _adm(b2.elements()) and b2.role() == "general",
                      f"role={b2.role()}, adm_ {len(_adm(b2.elements()))}개")
        except (LookupError, KeyError, ValueError) as ex:
            check("로그인 흐름(admin_pw, '관리자 로그인', '로그아웃')이 동작한다", False, f"{type(ex).__name__}: {ex}")

        # 비밀값 없이
        try:
            b3 = bk.open_backend(Path(d), None)
            els = b3.elements()
            ok_open = not any(e["kind"] == "error" for e in els)
            check("비밀값 없이 일반 화면이 오류 없이 뜬다", ok_open and bool(els),
                  "" if ok_open else "오류: " + "; ".join(e["label"][:80] for e in els if e["kind"] == "error"))
            try:
                _login(b3, EVAL_ADMIN_PASSWORD)
            except (LookupError, KeyError, ValueError):
                pass
            check("비밀값이 없으면 로그인되지 않는다", b3.role() == "general" and not _adm(b3.elements()),
                  f"role={b3.role()}")
        except Exception as ex:   # noqa: BLE001  화면이 죽는 것도 점검 결과다
            check("비밀값 없이 일반 화면이 오류 없이 뜬다", False, f"{type(ex).__name__}: {ex}")
    return {"passed": all(c["ok"] for c in checks), "checks": checks}


def main(argv=None) -> int:
    for s in (sys.stdout, sys.stderr):
        s.reconfigure(encoding="utf-8")
    argv = sys.argv[1:] if argv is None else argv
    res = audit()
    for c in res["checks"]:
        print(f"  {'통과' if c['ok'] else '실패'} {c['name']}" + ("" if c["ok"] or not c["detail"] else f"  ({c['detail']})"))
    if argv:
        out = ROOT / "ui_eval" / "runs" / argv[0] / "audit.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print("관리자 영역 점검: " + ("통과" if res["passed"] else "실패"))
    return 0 if res["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
