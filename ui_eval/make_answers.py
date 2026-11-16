"""정답 생성: 과제의 의도한 입력을 엔진에 직접 넣어 계산한다 (화면을 거치지 않는다).

    python -m ui_eval.make_answers                # 공개 + 숨긴 과제 모두
    python -m ui_eval.make_answers --set public
    python -m ui_eval.make_answers --check        # 다시 계산해 저장된 정답과 같은지만 본다 (종료 코드 1 = 다름)

총괄 소유 파일이다 (에이전트 수정 금지). 결과는 저장소 밖 ui_eval.contract.ANSWERS 경로에 쓴다.
정답은 최신 스냅샷(linefee/market_snapshot.json), BankParams 기본값(경로 5000, 시드 11), 가명 샘플 마스터 기준이다.
스냅샷이 바뀌면 다시 만든다. 그 전 라운드 점수와는 비교할 수 없게 된다.

정답 파일 형식: {"meta": {...}, "tasks": {id: {"inputs": 의도한 입력, "answers": {...},
                                              "first": {"inputs", "answers"}  # 조건을 바꾸기 전 계산이 있는 과제만}}}
"""
import argparse
import hashlib
import json
import sys

from linefee.market import SNAPSHOT, load

from ui_eval import contract as c


def solve(spec: dict) -> dict:
    m = load()   # 시장 객체는 QuantLib 평가일(전역)을 바꾼다. 건마다 다시 읽는다
    res = c.compute(c.build_case(spec, m), m)
    return {"inputs": c.inputs_from_result(res), "answers": c.answers_from_result(res)}


def build(which: str) -> dict:
    out = {}
    for t in c.load_tasks(which):
        out[t["id"]] = solve(t["inputs"])
        if "inputs_first" in t:
            out[t["id"]]["first"] = solve(t["inputs_first"])
        a = out[t["id"]]["answers"]
        missing = [f for f in t["scored"] if f not in a]
        if missing:
            sys.exit(f"{t['id']}: 채점 항목 {missing}이 이 거래의 정답 항목에 없다")
        print(f"  {t['id']}: " + ", ".join(f"{f}={a[f]:.3f}" if isinstance(a[f], float) else f"{f}={a[f]}"
                                           for f in t["scored"]), flush=True)
    return out


def same(a, b) -> bool:
    if isinstance(a, dict):
        return isinstance(b, dict) and set(a) == set(b) and all(same(a[k], b[k]) for k in a)
    if isinstance(a, float) or isinstance(b, float):
        return abs(a - b) <= 1e-9 * max(1.0, abs(a), abs(b))
    return a == b


def main():
    for s in (sys.stdout, sys.stderr):
        s.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--set", choices=["public", "hidden"], default=None)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--quiet-values", action="store_true", help="값을 화면에 찍지 않는다 (숨긴 과제용)")
    a = ap.parse_args()
    meta = {"snapshot": hashlib.sha256(SNAPSHOT.read_bytes()).hexdigest()[:16],
            # 마스터 해시는 줄바꿈 변환(CRLF)에 흔들리지 않게 LF로 맞춘 뒤 계산한다
            "master": hashlib.sha256(b"".join(c.MASTER_SAMPLE.read_bytes().splitlines())).hexdigest()[:16]}
    bad = 0
    for which in ([a.set] if a.set else ["public", "hidden"]):
        print(f"{which}:")
        if a.quiet_values:
            import contextlib
            import io
            with contextlib.redirect_stdout(io.StringIO()):
                tasks = build(which)
        else:
            tasks = build(which)
        now = {"meta": meta, "tasks": tasks}
        path = c.ANSWERS[which]
        if a.check:
            old = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
            ok = old is not None and same(old, json.loads(json.dumps(now)))
            print(f"  {'같음' if ok else '다름'}: {path}")
            bad += not ok
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(now, indent=1, ensure_ascii=False, sort_keys=True), encoding="utf-8")
            print(f"  저장 {len(tasks)}건: {path}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
