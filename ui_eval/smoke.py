"""드라이버 → 로그 → 채점 한 바퀴 (페르소나 없이). 화면을 바꾸는 작업의 게이트로 계속 쓴다.

    python -m ui_eval.smoke      # 종료 코드 0 = 정상

공개 과제 T01, T03, T08을 정해진 조작으로 풀어 성공·오입력 없음을 확인하고, 이어서 일부러 틀린 실행(T03의 명목을 다르게,
T08을 다시 계산하지 않고 제출)이 실패·놓친 오입력·오래된 결과 사용으로 잡히는지 본다. 조작은 화면 계약 3번의 key만 쓴다.
"""
import json
import sys

from ui_eval import driver as d
from ui_eval import score as sc

FAILS = []


def check(label: str, ok: bool):
    print(f"  {'ok  ' if ok else '실패'} {label}")
    if not ok:
        FAILS.append(label)


def last_answers(run: str, task: str) -> list:
    """세션 로그의 마지막 계산에서 scored 항목을 뽑아 answer 인자로 만든다."""
    log = d._read_log(d.session_dir(run, "p1", task))
    calc = [r for r in log if r.get("calc")][-1]["calc"]["answers"]
    out = []
    for f in d._task(task)["scored"]:
        v = calc[f]
        out.append(f"{f}={v}" if isinstance(v, str) else f"{f}={v:.3f}")
    return out


def solve(run: str, task: str, steps: list, answer=None):
    """steps: [("set", id, 값) | ("click", id)]. answer: None이면 마지막 계산의 값으로 제출."""
    name = f"{run}-p1-{task}"
    d.cmd_start(run, "p1", task)
    for st in steps:
        out = d.cmd_act(name, st[0], st[1], st[2] if len(st) > 2 else "")
        if out.startswith("오류"):
            sys.exit(f"{task}: 조작 실패 {st}: {out}")
    d.cmd_answer(name, answer or last_answers(run, task))


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        s.reconfigure(encoding="utf-8")
    calc = ("click", "계산")
    print("정상 풀이 (run smoke)")
    solve("smoke", "T01", [("set", "preset", "1"), calc])
    solve("smoke", "T03", [("set", "preset", "4"), ("set", "mat", "7"), ("set", "irs_amt", "1500"), ("set", "fee_bp", "4"),
                           calc])
    solve("smoke", "T08", [("set", "preset", "4"), calc, ("set", "mat", "10"), calc])
    good = sc.score_run("smoke")["sessions"]["public"]["p1"]
    by = {s["task"]: s for s in good}
    for t in ("T01", "T03", "T08"):
        s = by.get(t)
        check(f"{t} 성공, 오입력 없음, 오래된 결과 아님", bool(s) and s["success"] and not s["mis_entry"] and not s["missed"]
              and not s["stale"])
    print("일부러 틀린 풀이 (run smokebad)")
    solve("smokebad", "T03", [("set", "preset", "4"), ("set", "mat", "7"), ("set", "irs_amt", "150"), ("set", "fee_bp", "4"),
                              calc])
    solve("smokebad", "T08", [("set", "preset", "4"), calc, ("set", "mat", "10")])   # 다시 계산하지 않고 제출
    bad = {s["task"]: s for s in sc.score_run("smokebad")["sessions"]["public"]["p1"]}
    s = bad.get("T03")
    check("T03 명목 오입력: 실패, 놓친 오입력", bool(s) and not s["success"] and s["missed"] and s["mis_entry"])
    s = bad.get("T08")
    check("T08 다시 계산 안 함: 실패, 놓친 오입력, 오래된 결과 사용", bool(s) and not s["success"] and s["missed"] and s["stale"])
    print("실패: " + (", ".join(FAILS) if FAILS else "없음"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
