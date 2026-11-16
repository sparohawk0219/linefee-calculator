"""채점기 단위 테스트: 화면·엔진 계산·정답 파일 없이 합성 로그로 채점 규칙을 확인한다.  python -m tests.test_score

실패하면 종료 코드 1. 합성 정답을 쓰므로 정답 파일(저장소 밖)을 읽지 않는다.
"""
import copy
import sys

from ui_eval import score as sc

FAILS = []


def check(label, ok):
    print(f"  {'ok  ' if ok else '실패'} {label}")
    if not ok:
        FAILS.append(label)


# ---- 합성 과제·정답 ----
INTENDED = {"product": "IRS", "maturity": 5.0, "fee": 0.0004, "legs": {"receive": {"notional": 1.5e11, "rate": 0.03}}}
FIRST = {**copy.deepcopy(INTENDED), "maturity": 3.0}
TASK = {"id": "T99", "scored": ["net_oku", "be_fee_bp"]}
TRUTH = {"inputs": INTENDED, "answers": {"net_oku": 2.0, "be_fee_bp": 5.0}}
TRUTH_FIRST = {**TRUTH, "first": {"inputs": FIRST, "answers": {"net_oku": 1.0, "be_fee_bp": 4.0}}}
GOOD = {"net_oku": "2.00", "be_fee_bp": "5.0"}


def wrong_inputs(**kw):
    x = copy.deepcopy(INTENDED)
    x["legs"]["receive"]["notional"] = 1.5e10   # 명목 자릿수 오입력
    x.update(kw)
    return x


def rec(cmd, **kw):
    return {"seq": 0, "cmd": cmd, "id": kw.pop("id", "x"), "ok": True, "role": "general", "warnings": [], "dirty": False,
            "admin_visible": {"keys": [], "labels": []}, **kw}


def calc(inputs, summary_ok=False, **kw):
    return rec("click", calc={"inputs": inputs, "answers": {"net_oku": 2.0, "be_fee_bp": 5.0}, "summary_ok": summary_ok}, **kw)


def answer(given=None, dirty=False):
    return rec("answer", answers=GOOD if given is None else given, dirty=dirty)


def S(log, truth=TRUTH):
    for i, r in enumerate(log, 1):
        r["seq"] = i
    return sc.score_session(log, TASK, truth)


def test_session_rules():
    print("세션 판정")
    s = S([rec("set"), calc(INTENDED), answer()])
    check("성공: 성공, 오입력 없음, 놓침 없음, 오래된 결과 아님", s["success"] and not s["mis_entry"] and not s["missed"]
          and not s["mid_fix"] and not s["stale"] and not s["interp_error"])
    check("조작 수는 set + click 횟수", s["ops"] == 2)

    s = S([calc(wrong_inputs()), answer({"net_oku": "0.2", "be_fee_bp": "5.0"})])
    check("놓친 오입력: 오입력 발생, 놓침, 중간 수정 아님, 실패", s["mis_entry"] and s["missed"] and not s["mid_fix"] and not s["success"])
    check("놓친 오입력은 해석 오류가 아니다", s["interp_error"] == [])

    s = S([calc(wrong_inputs()), calc(INTENDED), answer()])
    check("중간 수정: 오입력 발생, 놓침 아님, 중간 수정, 성공", s["mis_entry"] and not s["missed"] and s["mid_fix"] and s["success"])

    s = S([calc(INTENDED), answer({"net_oku": "2.0", "be_fee_bp": "9.0"})])
    check("해석 오류: 입력은 맞고 답이 틀림, 틀린 항목이 나온다", s["interp_error"] == ["be_fee_bp"] and not s["success"]
          and not s["missed"])
    check("해석 오류: 낸 값과 화면 값이 함께 남는다", s["given"]["be_fee_bp"] == "9.0" and s["screen_values"]["be_fee_bp"] == 5.0)

    s = S([calc(INTENDED), answer(dirty=True)])
    check("오래된 결과(1): 답을 낼 때 dirty", s["stale"] and s["success"])

    s = S([calc(FIRST), answer({"net_oku": "1.0", "be_fee_bp": "4.0"})], TRUTH_FIRST)
    check("오래된 결과(2): 마지막 계산이 first 입력", s["stale"] and not s["mis_entry"] and s["missed"] and not s["success"])

    s = S([calc(FIRST), calc(INTENDED), answer()], TRUTH_FIRST)
    check("first 계산 뒤 다시 계산: 오입력·오래된 결과 아님", not s["mis_entry"] and not s["stale"] and s["success"])

    s = S([rec("set"), answer({"net_oku": "2.0", "be_fee_bp": "5.0"})])
    check("계산 없이 답: 놓친 오입력", s["missed"] and not s["mis_entry"])

    s = S([calc(INTENDED), rec("giveup", value="모르겠다")])
    check("포기: 놓친 오입력 포함, 성공 아님, 사유 기록", s["giveup"] and s["missed"] and not s["success"] and s["done"]
          and s["giveup_reason"] == "모르겠다")

    s = S([rec("set")])
    check("answer·giveup 없이 끝난 세션은 done 아님", not s["done"])

    s = S([calc(INTENDED, True), calc(INTENDED, False), calc(INTENDED, True), calc(INTENDED, True), answer()])
    check("입력 확인 가능: summary_ok 계산의 비율", abs(s["checkable"] - 0.75) < 1e-9)
    check("계산이 없으면 입력 확인 가능은 없음(None)", S([rec("set"), answer()])["checkable"] is None)

    s = S([rec("set", admin_visible={"keys": ["adm_x"], "labels": []}), rec("click", admin_visible={"keys": [], "labels": ["세율"]}),
           rec("set", role="admin", admin_visible={"keys": ["adm_x"], "labels": []}), rec("set"), calc(INTENDED), answer()])
    check("관리자 영역 노출: general 역할 행동 중 보인 횟수만", s["admin_exposure"] == 2)

    s = S([rec("note", value="헷갈림"), rec("note", value="또"), calc(INTENDED), answer()])
    check("혼란 보고 수와 원문", s["notes"] == 2 and s["note_texts"] == ["헷갈림", "또"])

    s = S([{**calc(INTENDED), "calc": {"inputs": None, "answers": None, "summary_ok": False}}, answer()])
    check("입력을 읽지 못한 계산은 오입력으로 본다", s["mis_entry"] and s["missed"])


def test_totals_and_feedback():
    print("합계와 feedback")
    a = S([calc(INTENDED), answer()])
    b = S([calc(wrong_inputs()), answer({"net_oku": "0", "be_fee_bp": "0"})])
    a["persona"] = b["persona"] = "p1"
    t = sc.totals([a, b])
    check("합계: 성공 1, 놓친 1, 세션 2", (t["success"], t["missed"], t["sessions"]) == (1, 1, 2))
    summ = {"public": {"sessions": {"p1": [a, b], "p2": []}, "by_persona": {"p1": t, "p2": sc.totals([])}, "total": t}}
    md = sc.feedback_md("t", summ)
    check("feedback에 틀린 입력의 의도값·실제값이 나온다", "legs.receive.notional" in md and "150000000000.0 → 15000000000.0" in md)
    check("feedback에 정답 항목 값은 나오지 않는다", "정답" not in md)
    scored = {"sessions": {"public": {"p1": [a, b]}, "hidden": {"p1": [a, b]}, "admin": {}}, "missing": []}
    sm = sc.summarize("t", scored)
    check("숨긴 과제는 합계 숫자만", set(sm["hidden"]) == {"sessions", "success", "missed", "admin_exposure"})
    check("표 출력이 돈다", "과제 성공" in sc.table("공개", {"p1": t}))


# ---- 채택 판정 ----
def pt(success, missed):
    return {"sessions": 12, "success": success, "missed": missed, "admin_exposure": 0}


def mk(p1=(6, 6), p2=(6, 6), hs=6, hm=2, hexp=0, pexp=0, missing=()):
    total = {"admin_exposure": pexp, "success": p1[0] + p2[0], "missed": p1[1] + p2[1]}
    return {"public": {"by_persona": {"p1": pt(*p1), "p2": pt(*p2)}, "total": total},
            "hidden": {"sessions": 12, "success": hs, "missed": hm, "admin_exposure": hexp}, "missing": list(missing)}


OK_AUDIT = {"passed": True}
OK_FAULT = {"stale_unflagged": 0, "false_alarm_rate": 0.0, "not_run": []}


def verdict(prev, cur, audit=OK_AUDIT, faults=OK_FAULT, pfaults=OK_FAULT):
    return sc.compare(prev, cur, audit, faults, pfaults)


def test_compare():
    print("채택 판정")
    base = mk()
    v = verdict(base, mk(p1=(8, 6)))
    check("개선 인정(1): S +2, M 같음 → 채택", v["adopt"] is True and v["improved_by"] == "성공 수 증가")
    check("S +2라도 M이 늘면 (두 페르소나 개선도 아니면) 되돌림", verdict(base, mk(p1=(8, 7)))["adopt"] is False)
    v = verdict(base, mk(p1=(6, 4)))
    check("개선 인정(2): M -2, S 같음 → 채택", v["adopt"] is True and v["improved_by"] == "놓친 오입력 감소")
    check("M -2라도 S가 줄면 되돌림", verdict(base, mk(p1=(5, 4)))["adopt"] is False)
    v = verdict(base, mk(p1=(7, 6), p2=(6, 5)))
    check("개선 인정(3): 각자 나아지고 나빠진 것 없음 → 채택", v["adopt"] is True and v["improved_by"] == "두 페르소나 모두 개선")
    check("개선 인정(3): 한 쪽이 나빠지면 되돌림", verdict(base, mk(p1=(7, 6), p2=(6, 7)))["adopt"] is False)
    check("개선 인정(3): 한 쪽이 그대로면 되돌림", verdict(base, mk(p1=(7, 6)))["adopt"] is False)
    check("개선 없음 → 되돌림", verdict(base, mk())["adopt"] is False)
    check("S +1만 → 되돌림", verdict(base, mk(p1=(7, 6), p2=(6, 6)))["adopt"] is False)

    better = dict(p1=(8, 6))
    check("숨김 성공 1 감소는 잡음 → 채택", verdict(base, mk(hs=5, **better))["adopt"] is True)
    v = verdict(base, mk(hs=4, **better))
    check("숨김 성공 2 감소 → 되돌림(숨김 악화)", v["adopt"] is False and any("숨김 악화" in r for r in v["reasons"]))
    check("숨김 놓친 오입력 1 증가는 잡음 → 채택", verdict(base, mk(hm=3, **better))["adopt"] is True)
    check("숨김 놓친 오입력 2 증가 → 되돌림", verdict(base, mk(hm=4, **better))["adopt"] is False)

    good = mk(**better)
    v = verdict(base, good, audit={"passed": False})
    check("가드 실패: audit", v["adopt"] is False and "audit 통과" in v["guard_fail"])
    v = verdict(base, mk(pexp=1, **better))
    check("가드 실패: 공개 세션 관리자 노출", v["adopt"] is False and "관리자 영역 노출 0" in v["guard_fail"])
    v = verdict(base, mk(hexp=1, **better))
    check("가드 실패: 숨긴 세션 관리자 노출", v["adopt"] is False and "관리자 영역 노출 0" in v["guard_fail"])
    v = verdict(base, good, faults={**OK_FAULT, "stale_unflagged": 1})
    check("가드 실패: 오래된 결과 미표시", v["adopt"] is False and "오래된 결과 미표시 0" in v["guard_fail"])
    v = verdict(base, good, faults={**OK_FAULT, "false_alarm_rate": 0.25}, pfaults={**OK_FAULT, "false_alarm_rate": 0.125})
    check("가드 실패: 오경고율 상승", v["adopt"] is False and "오경고율이 이전보다 높지 않음" in v["guard_fail"])
    v = verdict(base, good, faults={**OK_FAULT, "false_alarm_rate": 0.125}, pfaults={**OK_FAULT, "false_alarm_rate": 0.125})
    check("오경고율이 같으면 통과", v["adopt"] is True)
    v = verdict(base, good, faults={**OK_FAULT, "not_run": [{"id": "F1"}]})
    check("가드 실패: 실행 못한 fault", v["adopt"] is False and "실행 불가 fault 0" in v["guard_fail"])

    check("자료 없음: audit.json 없음 → 판정 불가", verdict(base, good, audit=None)["adopt"] is None)
    check("자료 없음: faults.json 없음 → 판정 불가", verdict(base, good, faults=None)["adopt"] is None)
    check("자료 없음: 빠진 세션 → 판정 불가", verdict(base, mk(missing=["p1 T01"], **better))["adopt"] is None)
    check("자료 없음: 이전 score 없음 → 판정 불가", verdict(None, good)["adopt"] is None)

    v = verdict(base, mk(p1=(12, 0), p2=(11, 1), hs=10))
    check("목표 달성: S 23, M 1, 숨김 10", v["goal"] is True)
    check("목표 미달: 숨긴 성공 9", verdict(base, mk(p1=(12, 0), p2=(11, 1), hs=9))["goal"] is False)
    check("목표 미달: M 2", verdict(base, mk(p1=(12, 1), p2=(11, 1), hs=10))["goal"] is False)


if __name__ == "__main__":
    test_session_rules()
    test_totals_and_feedback()
    test_compare()
    print("실패: " + (", ".join(FAILS) if FAILS else "없음"))
    sys.exit(1 if FAILS else 0)
