"""리뷰 발견사항 원장.

    python tools/ledger.py merge                      # reviews/findings_*.jsonl → reviews/ledger.jsonl (검증·중복 표시)
    python tools/ledger.py verify L-003 reproduced|refuted|unverifiable [메모]
    python tools/ledger.py render                     # reviews/TRIAGE.md (사용자 분류용)
    python tools/ledger.py decide L-003 accept|reject|defer [메모]
    python tools/ledger.py close  L-003 closed|not-closed [근거]
    python tools/ledger.py status
    python tools/ledger.py show L-003

발견사항 한 줄(JSON) 형식 — 리뷰어가 reviews/findings_<리뷰어>.jsonl 에 쓴다:
  id                  "R1-001" (리뷰어 접두어 + 일련번호)
  severity            "P1" 결과 숫자가 틀리거나 규정 위반 | "P2" 조건에 따라 틀림·문서와 코드 불일치 | "P3" 품질·명확성
  category            methodology | regulatory | numerics | code | tests | docs-mismatch | repo-hygiene | decision-conflict
  file, line          저장소 기준 상대 경로, 줄 번호 (파일 전체면 0)
  claim               무엇이 문제인지 한두 문장
  repro_command       저장소 루트에서 실행하면 문제를 보여 주는 명령 한 줄 (없으면 "" 와 함께 근거를 claim에)
  expected_vs_actual  기대값과 실제값
  touches_methodology true면 고치는 데 방법론 변경이 필요 (사용자 개별 승인 대상)
  suggested_fix       (선택) 고치는 방향
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import ROOT, utf8_stdout  # noqa: E402

REVIEWS = ROOT / "reviews"
LEDGER = REVIEWS / "ledger.jsonl"
TRIAGE = REVIEWS / "TRIAGE.md"

SEVERITIES = ["P1", "P2", "P3"]
CATEGORIES = ["methodology", "regulatory", "numerics", "code", "tests", "docs-mismatch", "repo-hygiene",
              "decision-conflict"]
REQUIRED = ["id", "severity", "category", "file", "line", "claim", "repro_command", "expected_vs_actual"]
VERIFY = ["reproduced", "refuted", "unverifiable"]
DECIDE = ["accept", "reject", "defer"]
CLOSE = ["closed", "not-closed"]


def read_jsonl(path: Path) -> list:
    out = []
    for n, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError as e:
            sys.exit(f"{path.name}:{n} JSON 오류: {e}")
    return out


def load() -> list:
    return read_jsonl(LEDGER) if LEDGER.exists() else []


def save(rows: list):
    REVIEWS.mkdir(exist_ok=True)
    LEDGER.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def validate(f: dict, src: str) -> list:
    errs = [f"{k} 없음" for k in REQUIRED if k not in f]
    if errs:
        return errs
    if f["severity"] not in SEVERITIES:
        errs.append(f"severity {f['severity']!r}")
    if f["category"] not in CATEGORIES:
        errs.append(f"category {f['category']!r}")
    if not isinstance(f["line"], int):
        errs.append("line은 정수")
    if f["file"] and not (ROOT / f["file"]).exists():
        errs.append(f"파일 없음: {f['file']}")
    elif f["file"] and f["line"] and (ROOT / f["file"]).suffix in (".py", ".md", ".txt", ".toml", ".json"):
        n = len((ROOT / f["file"]).read_text(encoding="utf-8", errors="replace").splitlines())
        if f["line"] > n:
            errs.append(f"줄 번호 {f['line']} > 파일 길이 {n}")
    if not str(f["claim"]).strip():
        errs.append("claim 비어 있음")
    return errs


def cmd_merge(_):
    rows = load()
    known = {r["source_id"] for r in rows}
    files = sorted(REVIEWS.glob("findings_*.jsonl"))
    if not files:
        sys.exit("reviews/findings_*.jsonl 이 없습니다.")
    bad, added = [], 0
    for path in files:
        reviewer = path.stem.split("_", 1)[1]
        for f in read_jsonl(path):
            errs = validate(f, path.name)
            if errs:
                bad.append(f"{path.name} {f.get('id', '?')}: {', '.join(errs)}")
                continue
            if f["id"] in known:
                continue
            known.add(f["id"])
            added += 1
            rows.append({
                "id": f"L-{len(rows) + 1:03d}", "source_id": f["id"], "reviewer": reviewer,
                "severity": f["severity"], "category": f["category"], "file": f["file"], "line": f["line"],
                "claim": f["claim"], "repro_command": f["repro_command"],
                "expected_vs_actual": f["expected_vs_actual"],
                "touches_methodology": bool(f.get("touches_methodology", False))
                                       or f["category"] in ("methodology", "decision-conflict"),
                "suggested_fix": f.get("suggested_fix", ""),
                "duplicate_of": None, "verify": None, "verify_note": "",
                "decision": None, "decision_note": "", "closure": None, "closure_note": "",
            })
    # 중복 후보: 같은 파일, 줄 차이 3 이내, 다른 리뷰어. 먼저 들어온 항목을 대표로 둔다 (자동 삭제는 하지 않는다)
    for i, r in enumerate(rows):
        if r["duplicate_of"] or not r["file"]:
            continue
        for q in rows[:i]:
            if (q["file"] == r["file"] and q["reviewer"] != r["reviewer"] and not q["duplicate_of"]
                    and q["line"] and r["line"] and abs(q["line"] - r["line"]) <= 3):
                r["duplicate_of"] = q["id"]
                break
    save(rows)
    print(f"원장 {len(rows)}건 (새로 {added}건, 중복 후보 {sum(1 for r in rows if r['duplicate_of'])}건)")
    if bad:
        print(f"형식 오류로 제외 {len(bad)}건 — 리뷰어가 고쳐 다시 내야 한다:")
        print("\n".join("  " + b for b in bad))
        return 1
    return 0


def find(rows: list, lid: str) -> dict:
    for r in rows:
        if r["id"] == lid or r["source_id"] == lid:
            return r
    sys.exit(f"{lid} 없음")


def setter(field: str, note_field: str, allowed: list):
    def cmd(a):
        if a.value not in allowed:
            sys.exit(f"값은 {' | '.join(allowed)} 중 하나")
        rows = load()
        r = find(rows, a.id)
        r[field], r[note_field] = a.value, " ".join(a.note)
        save(rows)
        print(f"{r['id']} {field} = {a.value}")
        return 0
    return cmd


def cmd_show(a):
    print(json.dumps(find(load(), a.id), ensure_ascii=False, indent=2))
    return 0


def cmd_status(_):
    rows = load()

    def count(field, values):
        return ", ".join(f"{v or '미정'} {sum(1 for r in rows if r[field] == v)}" for v in values)

    print(f"전체 {len(rows)}건")
    print("심각도: " + ", ".join(f"{s} {sum(1 for r in rows if r['severity'] == s)}" for s in SEVERITIES))
    print("검증:   " + count("verify", VERIFY + [None]))
    print("결정:   " + count("decision", DECIDE + [None]))
    acc = [r for r in rows if r["decision"] == "accept"]
    print(f"종결:   accept {len(acc)}건 중 closed {sum(1 for r in acc if r['closure'] == 'closed')}, "
          f"not-closed {sum(1 for r in acc if r['closure'] == 'not-closed')}, "
          f"미점검 {sum(1 for r in acc if r['closure'] is None)}")
    return 0


def cell(s) -> str:
    return str(s).replace("|", "\\|").replace("\n", " ")


def cmd_render(_):
    rows = load()
    if not rows:
        sys.exit("원장이 비어 있습니다. 먼저 merge.")
    live = [r for r in rows if not r["duplicate_of"] and r["verify"] != "refuted"
            and r["category"] != "decision-conflict"]
    conflicts = [r for r in rows if r["category"] == "decision-conflict"]
    refuted = [r for r in rows if r["verify"] == "refuted"]
    dups = [r for r in rows if r["duplicate_of"]]
    out = ["# 리뷰 발견사항 분류 (TRIAGE)", "",
           "`tools/ledger.py render`가 만든 파일이다. 직접 고치지 않는다.", "",
           f"- 분류 대상 {len(live)}건 (검증에서 반박된 {len(refuted)}건, 중복 {len(dups)}건, "
           f"확정 결정과 충돌 {len(conflicts)}건은 아래에 따로)",
           "- 결정 기록: `python tools/ledger.py decide L-001 accept|reject|defer [메모]`",
           "- **방법론** 열이 '예'인 항목은 고치려면 방법론 변경이 필요하다. 항목별로 승인한다.", ""]
    for sev, title in zip(SEVERITIES, ["P1 — 결과 숫자가 틀리거나 규정 위반", "P2 — 조건에 따라 틀림, 문서와 코드 불일치",
                                       "P3 — 품질·명확성"]):
        part = [r for r in live if r["severity"] == sev]
        if not part:
            continue
        out += [f"## {title} ({len(part)}건)", "",
                "| ID | 분류 | 위치 | 내용 | 기대 vs 실제 | 검증 | 방법론 | 결정 |", "|---|---|---|---|---|---|---|---|"]
        for r in part:
            loc = f"`{r['file']}:{r['line']}`" if r["file"] else "-"
            ver = (r["verify"] or "미검증") + (f" ({cell(r['verify_note'])})" if r["verify_note"] else "")
            dec = (r["decision"] or "") + (f" ({cell(r['decision_note'])})" if r["decision_note"] else "")
            out.append(f"| {r['id']} | {r['category']} | {loc} | {cell(r['claim'])} | "
                       f"{cell(r['expected_vs_actual'])} | {ver} | {'예' if r['touches_methodology'] else ''} | {dec} |")
        out.append("")
        out += ["재현 명령과 고치는 방향:", ""]
        for r in part:
            out.append(f"- **{r['id']}** ({r['source_id']}): "
                       + (f"`{r['repro_command']}`" if r["repro_command"] else "재현 명령 없음")
                       + (f" — {cell(r['suggested_fix'])}" if r["suggested_fix"] else ""))
        out.append("")
    if conflicts:
        out += [f"## 확정 결정과 충돌하는 의견 ({len(conflicts)}건)", "",
                "docs/DECISIONS.md의 결정에 대한 이견이다. 구현 대상이 아니며 참고용이다.", ""]
        out += [f"- **{r['id']}** `{r['file']}:{r['line']}` {cell(r['claim'])}" for r in conflicts] + [""]
    if refuted:
        out += [f"## 검증에서 반박된 항목 ({len(refuted)}건)", ""]
        out += [f"- **{r['id']}** {cell(r['claim'])} — {cell(r['verify_note'])}" for r in refuted] + [""]
    if dups:
        out += [f"## 중복 후보 ({len(dups)}건)", ""]
        out += [f"- **{r['id']}** → {r['duplicate_of']}: {cell(r['claim'])}" for r in dups] + [""]
    TRIAGE.write_text("\n".join(out), encoding="utf-8")
    print(f"{TRIAGE.relative_to(ROOT)} 작성: 분류 대상 {len(live)}건")
    return 0


def main():
    utf8_stdout()
    ap = argparse.ArgumentParser(description="리뷰 발견사항 원장")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("merge").set_defaults(fn=cmd_merge)
    sub.add_parser("render").set_defaults(fn=cmd_render)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    s = sub.add_parser("show")
    s.add_argument("id")
    s.set_defaults(fn=cmd_show)
    for name, field, note, allowed in (("verify", "verify", "verify_note", VERIFY),
                                       ("decide", "decision", "decision_note", DECIDE),
                                       ("close", "closure", "closure_note", CLOSE)):
        s = sub.add_parser(name)
        s.add_argument("id")
        s.add_argument("value")
        s.add_argument("note", nargs="*")
        s.set_defaults(fn=setter(field, note, allowed))
    a = ap.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
