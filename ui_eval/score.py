"""채점 (LLM을 쓰지 않는다): 행동 로그와 정답 파일로 세션별 지표와 채택 판정을 낸다.

    python -m ui_eval.score <run>                      # 그 실행의 모든 세션을 채점
    python -m ui_eval.score --compare <이전> <이번>      # 채택 판정. 종료 코드 0 = 채택, 1 = 되돌림, 2 = 판정 불가(자료 없음)

출력 (모두 gitignore된 곳):
  ui_eval/runs/<run>/score.json            공개 과제 세션별 상세와 합계. 숨긴 과제는 합계 숫자만
  ui_eval/runs/<run>/feedback_public.md    개선 에이전트(U)가 읽는 글. 공개 과제만
  <PRIVATE_DIR>/runs/<run>/score_hidden.json   숨긴 과제 세션별 상세
  ui_eval/runs/<이번>/verdict.json         --compare 결과
"""
import json
import sys
from pathlib import Path

from ui_eval import contract as c

RUNS = c.ROOT / "ui_eval" / "runs"
SALES = ("p1", "p2")

# ---- 채택 판정 상수 ----
MIN_S_GAIN = 2        # 개선 인정: 성공 수가 이만큼 늘고 놓친 오입력이 늘지 않음
MIN_M_DROP = 2        # 개선 인정: 놓친 오입력이 이만큼 줄고 성공 수가 줄지 않음
HIDDEN_TOL = 1        # 숨김 비악화: 1건은 잡음으로 본다
GOAL_S = 22           # 목표: 공개 성공 수 이상
GOAL_M = 1            # 목표: 공개 놓친 오입력 수 이하
GOAL_HIDDEN_S = 10    # 목표: 숨긴 성공 수 이상
EPS = 1e-9

METRICS = ["success", "mis_entry", "missed", "mid_fix", "interp_error", "stale", "admin_exposure", "giveup"]


# ---------------------------------------------------------------------------
# 세션 하나
# ---------------------------------------------------------------------------
def score_session(log: list, task: dict, truth: dict) -> dict:
    """log: 행동 로그(driver), task: 과제(scored 등), truth: {"inputs", "answers", "first"?} (정답 파일의 한 과제)."""
    end = next((r for r in reversed(log) if r.get("cmd") in ("answer", "giveup")), None)
    done = end is not None
    gave_up = bool(end and end["cmd"] == "giveup")
    answered = bool(end and end["cmd"] == "answer")
    calcs = [r for r in log if r.get("calc")]
    intended = truth["inputs"]
    first = truth.get("first", {}).get("inputs")

    def diff(calc_rec):
        inp = calc_rec["calc"].get("inputs")
        return c.diff_inputs(intended, inp) if inp is not None else [{"field": "(입력을 읽지 못함)", "expected": None,
                                                                         "actual": None}]

    def is_first(calc_rec):
        inp = calc_rec["calc"].get("inputs")
        return first is not None and inp is not None and not c.diff_inputs(first, inp)

    bad_calcs = [r for r in calcs if diff(r) and not is_first(r)]
    mis_entry = bool(bad_calcs)
    last = calcs[-1] if calcs else None
    last_diff = diff(last) if last else None
    missed = gave_up or last is None or bool(last_diff)
    mid_fix = mis_entry and not missed

    given = end.get("answers", {}) if answered else {}
    wrong = [f for f in task["scored"] if not c.answer_ok(f, truth["answers"][f], given.get(f))] if answered else []
    success = answered and not wrong
    interp = [f for f in wrong] if (answered and last is not None and not last_diff) else []
    stale = bool(answered and (end.get("dirty") or (first is not None and last is not None and is_first(last))))
    checkable = (sum(1 for r in calcs if r["calc"].get("summary_ok")) / len(calcs)) if calcs else None
    acts = [r for r in log if r.get("cmd") in ("set", "click")]
    exposure = sum(1 for r in acts if r.get("role") == "general" and _visible(r.get("admin_visible")))
    notes = [r.get("value") for r in log if r.get("cmd") == "note"]
    screen_ans = last["calc"].get("answers") if last and last["calc"].get("answers") else {}
    return {
        "task": task["id"], "done": done, "ended": end["cmd"] if end else None, "success": success, "mis_entry": mis_entry,
        "missed": missed, "mid_fix": mid_fix, "interp_error": interp, "stale": stale, "checkable": checkable,
        "calcs": len(calcs), "ops": len(acts), "admin_exposure": exposure, "notes": len(notes), "giveup": gave_up,
        "wrong_fields": wrong, "given": given,
        "screen_values": {f: screen_ans.get(f) for f in task["scored"]} if answered else {},
        "diffs_last": last_diff or [],
        "diffs_bad": [{"seq": r["seq"], "diffs": diff(r)} for r in bad_calcs[:3]],
        "warnings": sorted({w for r in log for w in (r.get("warnings") or [])}),
        "note_texts": notes, "giveup_reason": end.get("value") if gave_up else None,
    }


def _visible(av) -> bool:
    if not av:
        return False
    return bool(av.get("keys") or av.get("labels")) if isinstance(av, dict) else True


# ---------------------------------------------------------------------------
# 합계
# ---------------------------------------------------------------------------
def totals(sessions: list) -> dict:
    n = len(sessions)
    chk = [s["checkable"] for s in sessions if s["checkable"] is not None]
    t = {"sessions": n, "success": sum(s["success"] for s in sessions), "mis_entry": sum(s["mis_entry"] for s in sessions),
         "missed": sum(s["missed"] for s in sessions), "mid_fix": sum(s["mid_fix"] for s in sessions),
         "interp_error": sum(bool(s["interp_error"]) for s in sessions), "stale": sum(s["stale"] for s in sessions),
         "checkable": (sum(chk) / len(chk)) if chk else None, "ops": sum(s["ops"] for s in sessions),
         "admin_exposure": sum(s["admin_exposure"] for s in sessions), "notes": sum(s["notes"] for s in sessions),
         "giveup": sum(s["giveup"] for s in sessions)}
    return t


# ---------------------------------------------------------------------------
# 실행 읽기
# ---------------------------------------------------------------------------
def _read_log(d: Path) -> list:
    p = d / "log.jsonl"
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()] if p.exists() else []


def expected_sessions(run: str) -> list:
    """[(persona, task id, 세트)] 기대 세션. p1·p2는 세일즈 과제 전부(공개 + 숨김), p3는 p3 폴더가 있을 때만 관리자 과제."""
    out = []
    pub = c.load_tasks("public")
    hid = c.load_tasks("hidden") if c.TASKS_HIDDEN.exists() else []
    for p in SALES:
        out += [(p, t["id"], "public") for t in pub if t["persona"] == "sales"]
        out += [(p, t["id"], "hidden") for t in hid if t["persona"] == "sales"]
    if (RUNS / run / "p3").exists():
        out += [("p3", t["id"], "public") for t in pub if t["persona"] == "admin"]
    return out


def _dir(run: str, persona: str, task: str, which: str) -> Path:
    return (c.PRIVATE_DIR / "runs" if which == "hidden" else RUNS) / run / persona / task


def score_run(run: str) -> dict:
    truths = {w: json.loads(c.ANSWERS[w].read_text(encoding="utf-8"))["tasks"] for w in ("public", "hidden")
              if c.ANSWERS[w].exists()}
    tasks = {w: {t["id"]: t for t in c.load_tasks(w)} for w in ("public", "hidden") if w in truths}
    res = {"public": {}, "hidden": {}, "admin": {}}
    missing = []
    for persona, tid, which in expected_sessions(run):
        log = _read_log(_dir(run, persona, tid, which))
        if which not in truths:
            sys.exit(f"정답 파일이 없습니다: {c.ANSWERS[which]} (python -m ui_eval.make_answers --check)")
        if not any(r.get("cmd") in ("answer", "giveup") for r in log):
            missing.append((persona, tid, which))
            continue
        s = score_session(log, tasks[which][tid], truths[which][tid])
        s["persona"] = persona
        res["admin" if persona == "p3" else which].setdefault(persona, []).append(s)
    return {"sessions": res, "missing": missing}


def summarize(run: str, scored: dict) -> dict:
    """score.json의 내용 (숨긴 과제는 합계 숫자만)."""
    ss = scored["sessions"]
    pub_all = [s for p in SALES for s in ss["public"].get(p, [])]
    hid_all = [s for p in SALES for s in ss["hidden"].get(p, [])]
    adm_all = [s for p in ss["admin"] for s in ss["admin"][p]]
    ht = totals(hid_all)
    return {
        "run": run,
        "public": {"sessions": {p: ss["public"].get(p, []) for p in SALES},
                   "by_persona": {p: totals(ss["public"].get(p, [])) for p in SALES}, "total": totals(pub_all)},
        "hidden": {k: ht[k] for k in ("sessions", "success", "missed", "admin_exposure")},
        "admin": {"sessions": adm_all, "total": totals(adm_all)},
        "missing": [f"{p} {t}" for p, t, _ in scored["missing"]],
    }


# ---------------------------------------------------------------------------
# 출력
# ---------------------------------------------------------------------------
def _fmt(v) -> str:
    return "-" if v is None else (f"{v:.0%}" if isinstance(v, float) and v <= 1 else str(v))


ROWS = [("세션 수", "sessions"), ("과제 성공", "success"), ("오입력 발생", "mis_entry"), ("놓친 오입력", "missed"),
        ("중간 수정", "mid_fix"), ("해석 오류", "interp_error"), ("오래된 결과 사용", "stale"),
        ("입력 확인 가능", "checkable"), ("조작 수(합)", "ops"), ("관리자 영역 노출", "admin_exposure"),
        ("혼란 보고(note)", "notes"), ("포기", "giveup")]


def table(title: str, cols: dict) -> str:
    names = list(cols)
    lines = [f"[{title}]", f"{'지표':<16}" + "".join(f"{n:>8}" for n in names)]
    for lab, key in ROWS:
        lines.append(f"{lab:<{16 - sum(1 for ch in lab if ord(ch) > 0x2E80)}}" + "".join(f"{_fmt(cols[n].get(key)):>8}" for n in names))
    return "\n".join(lines)


def feedback_md(run: str, summ: dict) -> str:
    out = [f"# 공개 과제 채점 결과: {run}", "",
           "개선 에이전트(U)용. 공개 과제만 들어 있다. 입력 항목 이름은 `ui_eval/contract.py`의 `inputs_from_result` 기준이다.", ""]
    t = summ["public"]["total"]
    out += [f"합계: 성공 {t['success']}/{t['sessions']}, 놓친 오입력 {t['missed']}, 중간 수정 {t['mid_fix']}, 해석 오류 {t['interp_error']}, "
            f"오래된 결과 사용 {t['stale']}, 포기 {t['giveup']}", ""]
    for p in SALES:
        for s in summ["public"]["sessions"][p]:
            flags = [n for n, k in (("성공", "success"), ("오입력 발생", "mis_entry"), ("놓친 오입력", "missed"),
                                    ("중간 수정", "mid_fix"), ("오래된 결과 사용", "stale"), ("포기", "giveup")) if s[k]]
            out.append(f"## {p} {s['task']}: " + (", ".join(flags) or "실패(분류 없음)") + f" (조작 {s['ops']}, 계산 {s['calcs']}회)")
            if s["diffs_last"]:
                out.append("- 마지막 계산의 입력이 의도와 다름 (항목: 의도값 → 실제값)")
                out += [f"  - {d['field']}: {d['expected']} → {d['actual']}" for d in s["diffs_last"]]
            elif s["mid_fix"]:
                out.append("- 중간에 잘못 넣었다가 고쳤다")
            for b in s["diffs_bad"]:
                if b["diffs"] != s["diffs_last"]:
                    out.append(f"- 잘못된 계산 (행동 {b['seq']}번)의 다른 항목: " + "; ".join(
                        f"{d['field']} {d['expected']} → {d['actual']}" for d in b["diffs"]))
            if s["interp_error"]:
                out.append("- 입력은 맞는데 답이 틀림 (항목: 낸 값 / 화면에 보인 값)")
                out += [f"  - {f}: {s['given'].get(f)} / {s['screen_values'].get(f)}" for f in s["interp_error"]]
            elif s["wrong_fields"]:
                out.append("- 틀린 답 항목: " + ", ".join(s["wrong_fields"]))
            if s["stale"]:
                out.append("- 계산 뒤 입력을 바꾸고 다시 계산하지 않았거나, 조건 변경 전 결과로 답함")
            if s["warnings"]:
                out.append("- 세션 중 보인 경고·오류")
                out += [f"  - {w}" for w in s["warnings"]]
            if s["note_texts"]:
                out.append("- 혼란 보고 원문")
                out += [f"  - {n}" for n in s["note_texts"]]
            if s["giveup_reason"]:
                out.append(f"- 포기 이유: {s['giveup_reason']}")
            out.append("")
    return "\n".join(out)


def main_score(run: str) -> int:
    scored = score_run(run)
    summ = summarize(run, scored)
    out = RUNS / run
    out.mkdir(parents=True, exist_ok=True)
    (out / "score.json").write_text(json.dumps(summ, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "feedback_public.md").write_text(feedback_md(run, summ), encoding="utf-8")
    hd = c.PRIVATE_DIR / "runs" / run
    hd.mkdir(parents=True, exist_ok=True)
    (hd / "score_hidden.json").write_text(json.dumps(
        {p: scored["sessions"]["hidden"].get(p, []) for p in SALES}, ensure_ascii=False, indent=1), encoding="utf-8")
    ss = scored["sessions"]
    print(table("공개 과제", {**{p: totals(ss["public"].get(p, [])) for p in SALES}, "합계": summ["public"]["total"]}))
    print(table("숨긴 과제", {**{p: totals(ss["hidden"].get(p, [])) for p in SALES},
                            "합계": totals([s for p in SALES for s in ss["hidden"].get(p, [])])}))
    if ss["admin"]:
        print(table("관리자(p3), 채택 판정에 쓰지 않음", {"p3": totals([s for p in ss["admin"] for s in ss["admin"][p]])}))
    if scored["missing"]:
        print("빠진 세션: " + ", ".join(f"{p} {t}" for p, t, _ in scored["missing"]))
    else:
        print("빠진 세션: 없음")
    return 0


# ---------------------------------------------------------------------------
# 채택 판정
# ---------------------------------------------------------------------------
def _sm(score: dict, persona: str = None) -> tuple:
    """공개 성공 수 S, 놓친 오입력 수 M (persona가 없으면 p1 + p2)."""
    ps = [persona] if persona else SALES
    return (sum(score["public"]["by_persona"][p]["success"] for p in ps),
            sum(score["public"]["by_persona"][p]["missed"] for p in ps))


def compare(prev: dict, cur: dict, audit: "dict | None", faults: "dict | None", prev_faults: "dict | None") -> dict:
    """채택 판정 (순수 함수). adopt: True | False | None(자료가 빠져 판정 불가)."""
    lacking = []
    for name, sc in (("이전", prev), ("이번", cur)):
        if sc is None:
            lacking.append(f"{name} score.json 없음")
        elif sc.get("missing"):
            lacking.append(f"{name} 실행에 빠진 세션 {len(sc['missing'])}개")
    for name, v in (("audit.json", audit), ("faults.json", faults), ("이전 faults.json", prev_faults)):
        if v is None:
            lacking.append(f"{name} 없음")
    if lacking:
        return {"adopt": None, "reasons": lacking}

    S0, M0 = _sm(prev)
    S1, M1 = _sm(cur)
    dS, dM = S1 - S0, M1 - M0
    per = {p: (_sm(prev, p), _sm(cur, p)) for p in SALES}
    each_ok = all(s1 >= s0 and m1 <= m0 for (s0, m0), (s1, m1) in per.values())
    each_better = all(s1 > s0 or m1 < m0 for (s0, m0), (s1, m1) in per.values())
    improved_by = ("성공 수 증가" if dS >= MIN_S_GAIN and dM <= 0 else None) or \
                  ("놓친 오입력 감소" if -dM >= MIN_M_DROP and dS >= 0 else None) or \
                  ("두 페르소나 모두 개선" if each_ok and each_better else None)
    hs0, hs1 = prev["hidden"]["success"], cur["hidden"]["success"]
    hm0, hm1 = prev["hidden"]["missed"], cur["hidden"]["missed"]
    hidden_ok = (hs0 - hs1 <= HIDDEN_TOL) and (hm1 - hm0 <= HIDDEN_TOL)
    guards = {
        "audit 통과": bool(audit.get("passed")),
        "관리자 영역 노출 0": cur["public"]["total"]["admin_exposure"] == 0 and cur["hidden"]["admin_exposure"] == 0,
        "오래된 결과 미표시 0": faults.get("stale_unflagged", 1) == 0,
        "오경고율이 이전보다 높지 않음": faults.get("false_alarm_rate", 1.0) <= prev_faults.get("false_alarm_rate", 0.0) + EPS,
        "실행 불가 fault 0": not faults.get("not_run"),
    }
    guard_fail = [k for k, v in guards.items() if not v]
    goal = S1 >= GOAL_S and M1 <= GOAL_M and hs1 >= GOAL_HIDDEN_S
    reasons = []
    if improved_by is None:
        reasons.append(f"개선 없음 (S {S0}→{S1}, M {M0}→{M1})")
    if not hidden_ok:
        reasons.append(f"숨김 악화 (성공 {hs0}→{hs1}, 놓친 오입력 {hm0}→{hm1})")
    reasons += [f"가드 실패: {k}" for k in guard_fail]
    return {"adopt": not reasons, "reasons": reasons, "improved_by": improved_by, "hidden_ok": hidden_ok,
            "guards": guards, "guard_fail": guard_fail, "goal": goal,
            "public": {"S": [S0, S1], "M": [M0, M1], "by_persona": {p: {"S": [a[0], b[0]], "M": [a[1], b[1]]}
                                                                     for p, (a, b) in per.items()}},
            "hidden": {"success": [hs0, hs1], "missed": [hm0, hm1]}}


def _load_json(p: Path):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def main_compare(prev: str, cur: str) -> int:
    v = compare(_load_json(RUNS / prev / "score.json"), _load_json(RUNS / cur / "score.json"),
                _load_json(RUNS / cur / "audit.json"), _load_json(RUNS / cur / "faults.json"),
                _load_json(RUNS / prev / "faults.json"))
    (RUNS / cur).mkdir(parents=True, exist_ok=True)
    (RUNS / cur / "verdict.json").write_text(json.dumps(v, ensure_ascii=False, indent=1), encoding="utf-8")
    if v["adopt"] is None:
        print("판정 불가: " + "; ".join(v["reasons"]))
        return 2
    p, h = v["public"], v["hidden"]
    print(f"공개 성공 S {p['S'][0]}→{p['S'][1]}, 놓친 오입력 M {p['M'][0]}→{p['M'][1]}, "
          f"숨긴 성공 {h['success'][0]}→{h['success'][1]}, 숨긴 놓친 오입력 {h['missed'][0]}→{h['missed'][1]}")
    print(f"가드 실패: {', '.join(v['guard_fail']) if v['guard_fail'] else '없음'} | 목표 달성: {'예' if v['goal'] else '아니오'}")
    print("채택" if v["adopt"] else "되돌림: " + "; ".join(v["reasons"]))
    return 0 if v["adopt"] else 1


def main(argv=None) -> int:
    for s in (sys.stdout, sys.stderr):
        s.reconfigure(encoding="utf-8")
    a = sys.argv[1:] if argv is None else argv
    if len(a) == 3 and a[0] == "--compare":
        return main_compare(a[1], a[2])
    if len(a) == 1 and not a[0].startswith("-"):
        return main_score(a[0])
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
