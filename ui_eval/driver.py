"""페르소나가 쓰는 텍스트 드라이버. 화면을 글자로 보여 주고, 행동을 로그에 남긴다.

    python -m ui_eval.driver list --persona sales|admin [--set public|hidden|all]
    python -m ui_eval.driver start <run> <persona> <task>
    python -m ui_eval.driver show   <session> [--all]
    python -m ui_eval.driver help   <session> <id>
    python -m ui_eval.driver set    <session> <id> <값>
    python -m ui_eval.driver click  <session> <id>
    python -m ui_eval.driver note   <session> "<글>"
    python -m ui_eval.driver answer <session> 항목=값 ...
    python -m ui_eval.driver giveup <session> "<이유>"

모델이 쓰는 CLI다: 출력은 짧고(80줄 이하), 오류 메시지는 다음에 할 일을 알려 준다.
명령마다 새 프로세스이므로 세션 폴더의 행동 로그(log.jsonl)를 처음부터 다시 실행해(replay) 화면을 되살린다.
같은 프로세스에서 여러 번 부르면(smoke) 화면을 그대로 이어 쓴다.
"""
import argparse
import difflib
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

from ui_eval import backend as bk
from ui_eval import contract as c
from ui_eval.audit_roles import exposure

RUNS_PUBLIC = c.ROOT / "ui_eval" / "runs"
PERSONAS = {"p1": "sales", "p2": "sales", "p3": "admin"}
MAX_LINES = 80
ALERTS = {"warning": "⚠ 경고: ", "error": "✖ 오류: "}
KIND_KO = {"select": "선택", "radio": "선택", "slider": "선택", "number": "숫자", "text_input": "글자", "checkbox": "체크",
           "button": "버튼"}


class DriverError(Exception):
    """사용자(페르소나)에게 그대로 보여 주는 오류."""


# ---------------------------------------------------------------------------
# 세션 폴더
# ---------------------------------------------------------------------------
def session_dir(run: str, persona: str, task: str) -> Path:
    base = c.PRIVATE_DIR / "runs" if c.task_set(task) == "hidden" else RUNS_PUBLIC
    return base / run / persona / task


def parse_session(name: str) -> tuple:
    parts = name.rsplit("-", 2)
    if len(parts) != 3 or parts[1] not in PERSONAS:
        raise DriverError(f"세션 이름 '{name}'이 이상합니다. start가 알려 준 이름(예: r0-p1-T01)을 그대로 쓰세요.")
    return parts[0], parts[1], parts[2]


def _read_log(d: Path) -> list:
    p = d / "log.jsonl"
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def _append(d: Path, rec: dict, log: list):
    rec = {"seq": len(log) + 1, "ts": datetime.now().isoformat(timespec="seconds"), **rec}
    with open(d / "log.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    log.append(rec)
    return rec


def _task(task_id: str) -> dict:
    for t in c.load_tasks(c.task_set(task_id)):
        if t["id"] == task_id:
            return t
    raise DriverError(f"과제 {task_id}가 없습니다. list 명령으로 과제 id를 확인하세요.")


# ---------------------------------------------------------------------------
# 화면 → 글자
# ---------------------------------------------------------------------------
def _num(v) -> str:
    if v is None:
        return "-"
    s = f"{v:,.4f}".rstrip("0")
    if s.endswith("."):
        s += "00"
    elif len(s.split(".")[1]) < 2:
        s += "0" * (2 - len(s.split(".")[1]))
    return s


def _flat(text: str, n: int = 300) -> str:
    t = re.sub(r"\s*\n\s*", " / ", str(text).replace("**", "").replace("`", "")).strip()
    return t if len(t) <= n else t[:n - 1] + "…"


def element_lines(e: dict, rows: int = 12) -> list:
    """요소 하나를 찍는 줄들. 표만 여러 줄."""
    k, lab, h = e["kind"], e["label"], " (?)" if e.get("help") else ""
    if k == "heading":
        return [f"## {_flat(lab)}"]
    if k == "expander":
        return [f"▸ {_flat(lab)}"]
    if k == "text":
        return [_flat(lab)]
    if k == "caption":
        return [f"· {_flat(lab)}"]
    if k in ALERTS:
        return [ALERTS[k] + _flat(lab)]
    if k == "info":
        return ["ℹ 안내: " + _flat(lab)]
    if k == "success":
        return ["✔ " + _flat(lab)]
    if k == "metric":
        return [f"(지표) {_flat(lab, 80)} = {_flat(e['value'])}{h}"]
    if k == "chart":
        return [f"[차트] {_flat(lab, 80)}"]
    if k == "table":
        v = e["value"]
        out = [f"[표] {_flat(lab, 80)}: " + " | ".join(v["columns"])]
        out += ["   " + " | ".join(r) for r in v["rows"][:rows]]
        if len(v["rows"]) > rows:
            out.append(f"   … 외 {len(v['rows']) - rows}행")
        return out
    ko = KIND_KO.get(k, k)
    if k in ("select", "radio", "slider"):
        opts = ", ".join(f"{i}: {_flat(o, 30)}" for i, o in enumerate(e["options"] or [], 1))
        return [f'[{e["id"]}] {ko} "{_flat(lab, 80)}" = {_flat(e["value"], 60)} {{{opts}}}{h}']
    if k == "number":
        val = _num(e["value"])
    elif k == "checkbox":
        val = "켬" if e["value"] else "끔"
    elif k == "button":
        return [f'[{e["id"]}] {ko} "{_flat(lab, 80)}"{h}']
    else:
        val = repr(e["value"]) if e["value"] != "" else "(비어 있음)"
    return [f'[{e["id"]}] {ko} "{_flat(lab, 80)}" = {val}{h}']


def result_start(els: list) -> "int | None":
    """결과 영역의 시작: '결과'가 들어간 제목 요소."""
    return next((i for i, e in enumerate(els) if e["kind"] == "heading" and "결과" in e["label"]), None)


def fit(lines_for: "callable", limit: int = MAX_LINES) -> list:
    """lines_for(표 행 수) -> 줄 목록. 한도를 넘으면 표 행을 줄이고, 그래도 넘으면 가운데를 줄인다."""
    for rows in (12, 5, 2, 0):
        lines = lines_for(rows)
        if len(lines) <= limit:
            return lines
    head, tail = limit * 3 // 8, limit - limit * 3 // 8 - 1
    cut = len(lines) - head - tail
    return lines[:head] + [f"… {cut}줄 생략 (show <세션> --all 로 전체)"] + lines[len(lines) - tail:]


def screen_lines(els: list, rows: int = 12, sidebar: bool = True, captions: bool = True) -> list:
    """화면 줄. 표·차트 바로 앞의 같은 제목 글은 한 번만 찍는다. sidebar=False면 사이드바는 한 줄로, captions=False면 결과 앞의 설명글을 뺀다."""
    ri = result_start(els)
    out, side = [], 0
    for i, e in enumerate(els):
        nxt = els[i + 1] if i + 1 < len(els) else None
        if e["kind"] == "text" and nxt is not None and nxt["kind"] in ("chart", "table")                 and re.sub(r"[*_`]", "", e["label"]).strip() == nxt["label"]:
            continue
        if not sidebar and e.get("area") == "sidebar":
            side += 1
            continue
        if not captions and e["kind"] == "caption" and (ri is None or i < ri):
            continue
        if side:
            out.append(f"(사이드바 {side}개 요소 생략: show <세션> --all)")
            side = 0
        out += element_lines(e, rows)
    if side:
        out.append(f"(사이드바 {side}개 요소 생략: show <세션> --all)")
    return out


def fit_screen(els: list, limit: int = MAX_LINES) -> list:
    """화면 전체를 한도 안으로: 표 행 줄이기, 사이드바 접기, 설명글 빼기 순으로, 그래도 넘으면 가운데를 줄인다."""
    for rows, side, caps in ((12, True, True), (5, True, True), (5, False, True), (0, False, True), (0, False, False)):
        lines = screen_lines(els, rows, side, caps)
        if len(lines) <= limit:
            return lines
    head, tail = limit * 3 // 8, limit - limit * 3 // 8 - 1
    return lines[:head] + [f"… {len(lines) - head - tail}줄 생략 (show <세션> --all 로 전체)"] + lines[len(lines) - tail:]


def alerts_of(els: list) -> list:
    return [ALERTS[e["kind"]] + _flat(e["label"]) for e in els if e["kind"] in ALERTS]


# ---------------------------------------------------------------------------
# 요소 찾기, 값 해석
# ---------------------------------------------------------------------------
def resolve(els: list, ident: str, kinds=None) -> dict:
    """id → 요소. id가 없으면 라벨(같음, 포함)이 하나로 정해질 때 그 요소."""
    ident = ident.strip().strip("[]")
    pool = [e for e in els if e["kind"] in (kinds or bk.INPUT_KINDS + ("button", "metric"))]
    for e in pool:
        if e["id"] == ident:
            return e
    for test in (lambda e: e["label"] == ident, lambda e: ident in e["label"]):
        hit = [e for e in pool if test(e)]
        if len(hit) == 1:
            return hit[0]
        if len(hit) > 1:
            raise DriverError(f"'{ident}'에 맞는 칸이 여럿입니다: " + ", ".join(f"[{e['id']}]" for e in hit[:6])
                              + ". 대괄호 안의 id를 그대로 쓰세요.")
    near = difflib.get_close_matches(ident, [e["id"] for e in pool], n=4, cutoff=0.3)
    hint = (" 비슷한 것: " + ", ".join(f"[{i}]" for i in near) + ".") if near else ""
    raise DriverError(f"화면에 [{ident}] 칸이 없습니다.{hint} 화면이 바뀌었을 수 있으니 show 로 다시 확인하세요.")


def coerce(e: dict, raw: str):
    """입력 글자를 위젯 값으로. 오류 메시지는 다음에 할 일을 알려 준다."""
    k, lab = e["kind"], e["label"]
    raw = raw.strip()
    if k == "number":
        s = raw.replace(",", "").replace(" ", "").rstrip("%")
        try:
            return float(s)
        except ValueError:
            raise DriverError(f'"{lab}"에는 숫자만 넣을 수 있습니다 (받은 값: {raw}). 예: set <세션> {e["id"]} 1500')
    if k in ("select", "radio", "slider"):
        opts = e["options"] or []
        if raw in opts:
            return raw
        if re.fullmatch(r"\d+", raw) and 1 <= int(raw) <= len(opts):
            return opts[int(raw) - 1]
        hit = [o for o in opts if raw and raw in o]
        if len(hit) == 1:
            return hit[0]
        menu = ", ".join(f"{i}: {_flat(o, 40)}" for i, o in enumerate(opts, 1))
        raise DriverError(f'"{lab}"에서 고를 수 있는 것: {{{menu}}}. 보이는 글자 또는 순번으로 다시 set 하세요 (받은 값: {raw}).')
    if k == "checkbox":
        if raw.lower() in ("1", "true", "t", "yes", "y", "on", "켬", "켜짐", "체크", "참"):
            return True
        if raw.lower() in ("0", "false", "f", "no", "n", "off", "끔", "꺼짐", "해제", "거짓"):
            return False
        raise DriverError(f'"{lab}"은 켬 또는 끔으로 넣으세요 (받은 값: {raw}).')
    if k == "text_input":
        return raw
    raise DriverError(f'"{lab}"은 값을 넣는 칸이 아닙니다. 버튼이면 click 을 쓰세요.')


# ---------------------------------------------------------------------------
# 살아 있는 화면
# ---------------------------------------------------------------------------
_LIVE: dict = {}   # 세션 이름 → Live (같은 프로세스에서 이어 쓸 때만)


class Live:
    def __init__(self, d: Path):
        self.dir = d
        self.backend = bk.open_backend(d / "data", c.EVAL_ADMIN_PASSWORD)
        self.last_result = None
        self.calc_snapshot = None
        self.applied = 0   # 반영한 로그 줄 수

    def snapshot(self, els=None) -> dict:
        return {e["id"]: e["value"] for e in (els or self.backend.elements()) if e["kind"] in bk.INPUT_KINDS}


def reset_data(d: Path):
    """세션 데이터 디렉터리를 처음 상태로: 샘플 마스터만 복사해 둔다."""
    data = d / "data"
    if data.exists():
        shutil.rmtree(data)
    data.mkdir(parents=True)
    shutil.copy(c.MASTER_SAMPLE, data / "counterparties.csv")


def replay(d: Path, log: list) -> Live:
    """로그의 set·click을 처음부터 다시 실행한다. 계산은 마지막 계산 클릭만 다시 한다 (앞의 결과는 덮여 쓰이므로)."""
    reset_data(d)
    live = Live(d)
    calc_idx = [i for i, r in enumerate(log) if r.get("cmd") == "click" and r.get("ok") and r.get("calc")]
    last_calc = calc_idx[-1] if calc_idx else None
    for i, r in enumerate(log):
        if r.get("cmd") in ("set", "click") and r.get("ok"):
            if r["cmd"] == "click" and r.get("calc") and i != last_calc:
                continue
            if r["cmd"] == "set":
                live.backend.set(r["id"], r["arg"])
            else:
                live.backend.click(r["id"])
            if i == last_calc:
                live.last_result = live.backend.result()
                live.calc_snapshot = live.snapshot()
    live.applied = len(log)
    return live


def get_live(name: str, d: Path, log: list) -> Live:
    live = _LIVE.get(name)
    if live is None or live.applied != len(log):
        live = replay(d, log)
    _LIVE[name] = live
    return live


# ---------------------------------------------------------------------------
# 입력 요약
# ---------------------------------------------------------------------------
def summary_text(els: list) -> str:
    """화면에서 '입력 요약'으로 시작하는 묶음의 글. 없으면 빈 글."""
    for i, e in enumerate(els):
        txt = e["label"] if e["kind"] != "metric" else e["label"] + " " + str(e["value"])
        if e["kind"] in ("heading", "text", "caption", "expander", "info", "success", "metric", "table") \
                and re.sub(r"[*_`#\s]", "", txt).startswith("입력요약"):
            parts = [txt]
            if e["kind"] == "expander":
                parts += [_elem_text(x) for x in els[i + 1:] if x.get("group") == e["label"]]
            else:
                for x in els[i + 1:]:
                    if x["kind"] not in ("text", "caption", "metric", "table"):
                        break
                    parts.append(_elem_text(x))
            return "\n".join(parts)
    return ""


def _elem_text(e: dict) -> str:
    if e["kind"] == "table":
        return " ".join(e["value"]["columns"]) + " " + " ".join(" ".join(r) for r in e["value"]["rows"])
    if e["kind"] == "metric":
        return f"{e['label']} {e['value']}"
    return str(e["label"])


def _numbers(text: str) -> list:
    return [float(x.replace(",", "")) for x in re.findall(r"\d[\d,]*(?:\.\d+)?", text)]


def summary_ok(els: list, inputs: dict) -> bool:
    """입력 요약에 만기, 두 다리 명목(화면 단위), 라인피 숫자가 모두 들어 있는가."""
    text = summary_text(els)
    if not text:
        return False
    nums = _numbers(text)
    has = lambda x: any(abs(n - x) <= max(0.006, 1e-3 * abs(x)) for n in nums)   # noqa: E731
    need = [[inputs["maturity"]]]
    for leg in inputs["legs"].values():
        need.append([leg["notional"] / c.UNIT[leg["ccy"]]])
    fee = inputs.get("fee") or 0.0
    if fee:
        need.append([fee * 1e4] if inputs["product"] != "FXFWD" else [fee, fee * 100])
    elif inputs.get("upfront_krw"):
        need.append([inputs["upfront_krw"] / c.OKU])
    return all(any(has(x) for x in alt) for alt in need)


# ---------------------------------------------------------------------------
# 행동 → 로그 한 줄
# ---------------------------------------------------------------------------
def observe(live: Live, els: list) -> dict:
    """행동 뒤 화면에서 로그에 남기는 것: role, warnings, dirty, admin_visible."""
    role = live.backend.role()
    snap = live.snapshot(els)
    return {"role": role, "warnings": alerts_of(els),
            "dirty": live.calc_snapshot is not None and snap != live.calc_snapshot,
            "admin_visible": exposure(els) if role == "general" else {"keys": [], "labels": []}}


def calc_info(live: Live, els: list) -> "dict | None":
    """계산이 새로 일어났으면 그 입력·정답 항목·입력 요약 판정."""
    res = live.backend.result()
    if res is None or res is live.last_result:
        return None
    live.last_result = res
    live.calc_snapshot = live.snapshot(els)
    try:
        inputs = c.inputs_from_result(res)
        info = {"inputs": inputs, "answers": c.answers_from_result(res), "summary_ok": summary_ok(els, inputs)}
    except Exception as ex:   # noqa: BLE001  계약 2를 어긴 결과도 기록은 남긴다
        info = {"inputs": None, "answers": None, "summary_ok": False, "error": f"{type(ex).__name__}: {ex}"}
    return json.loads(json.dumps(info, default=str))


def act(live: Live, d: Path, log: list, cmd: str, ident: str, raw: str = "") -> str:
    """set 또는 click 한 번. 로그에 한 줄 남기고 화면 변화를 글자로 돌려준다."""
    before = live.backend.elements()
    b_lines = {e["id"]: element_lines(e, 999) for e in before}
    b_area = _area(before)
    try:
        e = resolve(before, ident, bk.INPUT_KINDS if cmd == "set" else ("button",))
        if cmd == "click" and e["kind"] != "button":
            raise DriverError(f'"{e["label"]}"은 버튼이 아닙니다. 값을 넣는 칸이면 set 을 쓰세요.')
        arg = coerce(e, raw) if cmd == "set" else None
        try:
            live.backend.set(e["id"], arg) if cmd == "set" else live.backend.click(e["id"])
        except (KeyError, ValueError) as ex:
            raise DriverError(str(ex))
    except DriverError as ex:
        els = live.backend.elements()
        _append(d, {"cmd": cmd, "id": ident, "label": None, "value": raw, "ok": False, "error": str(ex),
                    **observe(live, els)}, log)
        live.applied = len(log)
        return f"오류: {ex}"
    els = live.backend.elements()
    calc = calc_info(live, els)
    rec = {"cmd": cmd, "id": e["id"], "label": e["label"], "value": raw, "arg": arg, "ok": True, "error": None,
           **observe(live, els)}
    if calc is not None:
        rec["calc"] = calc
    _append(d, rec, log)
    live.applied = len(log)
    head = f"ok: {e['id']} = {_flat(raw)}" if cmd == "set" else f"ok: [{e['id']}] 눌렀습니다"
    return head + "\n" + diff_view(before, els, b_lines, b_area, calc is not None)


def _brief(e: dict) -> str:
    """사라진 줄: 값·선택지는 빼고 이름만."""
    if e["kind"] in bk.INPUT_KINDS + ("button",):
        return f'[{e["id"]}] "{_flat(e["label"], 50)}"'
    return _flat(element_lines(e, 0)[0], 70)


def _area(els: list) -> list:
    i = result_start(els)
    return [] if i is None else [(e["id"], element_lines(e, 999)) for e in els[i:]]


def diff_view(before: list, after: list, b_lines: dict, b_area: list, calculated: bool) -> str:
    """바뀐 줄만(+ - ~), 결과 영역은 새로 생기거나 바뀌면 전부, 경고·오류는 항상 모두."""
    a_lines = {e["id"]: element_lines(e, 999) for e in after}
    a_area = _area(after)
    ri = result_start(after)
    area_ids = {i for i, _ in a_area}
    b_area_ids = {i for i, _ in b_area}
    area_changed = (a_area != b_area) and bool(a_area)

    def body(rows: int) -> list:
        out = []
        for e in after:
            if e["id"] in area_ids and ri is not None:
                continue
            old = b_lines.get(e["id"])
            new = element_lines(e, rows)
            if old is None:
                out += ["+ " + new[0]] + ["  " + x for x in new[1:]]
            elif old != a_lines[e["id"]]:
                out += ["~ " + new[0]] + ["  " + x for x in new[1:]]
        for e in before:
            if e["id"] not in a_lines and e["id"] not in b_area_ids:
                out.append("- " + _brief(e))
        if b_area and not a_area:
            out.append("- 결과 영역이 사라졌습니다")
        if area_changed:
            out.append("[결과 영역]")
            out += screen_lines(after[ri:], rows)
        shown = set(out)
        out += [x for x in (ALERTS[e["kind"]] + _flat(e["label"]) for e in after if e["kind"] in ALERTS)
                if x not in shown and "+ " + x not in shown and "~ " + x not in shown]
        return out or ["(바뀐 줄 없음)"]

    return "\n".join(fit(body, MAX_LINES - 1))


# ---------------------------------------------------------------------------
# 명령
# ---------------------------------------------------------------------------
def _load_session(name: str, need_open: bool = True) -> tuple:
    run, persona, task = parse_session(name)
    d = session_dir(run, persona, task)
    log = _read_log(d)
    if not log:
        raise DriverError(f"세션 {name}이 없습니다. 먼저: python -m ui_eval.driver start {run} {persona} {task}")
    if need_open and log[-1].get("cmd") in ("answer", "giveup"):
        raise DriverError(f"세션 {name}은 이미 끝났습니다 ({log[-1]['cmd']}). 다음 과제를 start 하세요.")
    return run, persona, task, d, log


def cmd_list(persona: str, which: str = "all") -> str:
    ids = []
    for s in (["public", "hidden"] if which == "all" else [which]):
        if s == "hidden" and not c.TASKS_HIDDEN.exists():
            continue
        ids += [t["id"] for t in c.load_tasks(s) if t["persona"] == persona]
    return "\n".join(ids)


def cmd_start(run: str, persona: str, task: str) -> str:
    if persona not in PERSONAS:
        raise DriverError(f"페르소나는 p1, p2, p3 중 하나입니다 (받은 값: {persona}).")
    t = _task(task)
    if t["persona"] != PERSONAS[persona]:
        who = "p3" if t["persona"] == "admin" else "p1 또는 p2"
        raise DriverError(f"과제 {task}는 {'관리자' if t['persona'] == 'admin' else '세일즈'} 과제입니다. {who}로 start 하세요.")
    d = session_dir(run, persona, task)
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    name = f"{run}-{persona}-{task}"
    log: list = []
    _LIVE.pop(name, None)
    live = replay(d, log)
    els = live.backend.elements()
    _append(d, {"cmd": "start", "id": None, "label": None, "value": None, "ok": True, "error": None,
                **observe(live, els)}, log)
    live.applied = len(log)
    _LIVE[name] = live
    ex = " ".join(f"{f}=<값>" for f in t["scored"])
    out = [f"세션: {name}", f"과제: {t['instruction']}", "제출 항목 (모두 적는다):"]
    out += [f"  {f}: {c.FIELD_LABELS[f]}" for f in t["scored"]]
    out.append(f"제출: python -m ui_eval.driver answer {name} {ex}")
    if t["persona"] == "admin":
        out.append(f"관리자 비밀번호: {c.EVAL_ADMIN_PASSWORD}")
    pre = "python -m ui_eval.driver"
    out.append(f"명령: {pre} set {name} <id> <값> | click {name} <id> | show {name} | help {name} <id> | "
               f"note {name} \"글\" | giveup {name} \"이유\"")
    out.append("--- 첫 화면 ---")
    head = len(out)
    body = fit_screen(els, MAX_LINES - head)
    return "\n".join(out + body)


def cmd_show(name: str, all_: bool = False) -> str:
    run, persona, task, d, log = _load_session(name, need_open=False)
    live = get_live(name, d, log)
    els = live.backend.elements()
    if all_:
        return "\n".join(screen_lines(els, 40))
    return "\n".join(fit_screen(els))


def cmd_help(name: str, ident: str) -> str:
    run, persona, task, d, log = _load_session(name)
    live = get_live(name, d, log)
    els = live.backend.elements()
    e = resolve(els, ident, bk.INPUT_KINDS + ("button", "metric"))
    _append(d, {"cmd": "help", "id": e["id"], "label": e["label"], "value": None, "ok": True, "error": None,
                **observe(live, els)}, log)
    live.applied = len(log)
    return (f"[{e['id']}] {e['label']}\n" + _flat(e["help"], 600)) if e.get("help") else \
        f"[{e['id']}] 에는 도움말이 없습니다. 칸 이름과 앞뒤 설명글을 보고 정하세요."


def cmd_act(name: str, cmd: str, ident: str, raw: str = "") -> str:
    run, persona, task, d, log = _load_session(name)
    live = get_live(name, d, log)
    return act(live, d, log, cmd, ident, raw)


def cmd_note(name: str, text: str) -> str:
    run, persona, task, d, log = _load_session(name)
    live = get_live(name, d, log)
    _append(d, {"cmd": "note", "id": None, "label": None, "value": text, "ok": True, "error": None,
                **observe(live, live.backend.elements())}, log)
    live.applied = len(log)
    return "기록했습니다."


def parse_answers(tokens: list) -> dict:
    """['a=1', 'b=0%', '미만'] → {'a': '1', 'b': '0% 미만'}: '='가 없는 조각은 앞 항목 값의 이어지는 글자로 본다."""
    out, last = {}, None
    for tok in tokens:
        if "=" in tok and re.match(r"^\w+=", tok):
            last, v = tok.split("=", 1)
            out[last] = v
        elif last is not None:
            out[last] += " " + tok
        else:
            raise DriverError(f"'{tok}'은 항목=값 형식이 아닙니다. 예: answer <세션> net_oku=1.23")
    return {k: v.strip() for k, v in out.items()}


def cmd_answer(name: str, tokens: list) -> str:
    run, persona, task, d, log = _load_session(name)
    t = _task(task)
    given = parse_answers(tokens)
    unknown = [k for k in given if k not in t["scored"]]
    missing = [k for k in t["scored"] if k not in given or given[k] == ""]
    if unknown or missing:
        raise DriverError((f"제출 항목이 아닌 것: {unknown}. " if unknown else "") + (f"빠진 항목: {missing}. " if missing else "")
                          + "제출 항목을 모두 적어서 다시 answer 하세요 (모르면 값에 모름이라고 적는다).")
    live = get_live(name, d, log)
    els = live.backend.elements()
    _append(d, {"cmd": "answer", "id": None, "label": None, "value": None, "answers": given, "ok": True, "error": None,
                **observe(live, els)}, log)
    _LIVE.pop(name, None)
    return "제출했습니다. 이 세션은 끝났습니다. 다음 과제를 start 하세요."


def cmd_giveup(name: str, reason: str) -> str:
    run, persona, task, d, log = _load_session(name)
    live = get_live(name, d, log)
    _append(d, {"cmd": "giveup", "id": None, "label": None, "value": reason, "ok": True, "error": None,
                **observe(live, live.backend.elements())}, log)
    _LIVE.pop(name, None)
    return "포기로 기록했습니다. 이 세션은 끝났습니다. 다음 과제를 start 하세요."


# ---------------------------------------------------------------------------
def main(argv=None) -> int:
    for s in (sys.stdout, sys.stderr):
        s.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="python -m ui_eval.driver", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("list")
    p.add_argument("--persona", choices=["sales", "admin"], required=True)
    p.add_argument("--set", choices=["public", "hidden", "all"], default="all")
    p = sub.add_parser("start")
    for a in ("run", "persona", "task"):
        p.add_argument(a)
    p = sub.add_parser("show")
    p.add_argument("session")
    p.add_argument("--all", action="store_true")
    for n in ("help", "click"):
        p = sub.add_parser(n)
        p.add_argument("session")
        p.add_argument("id", nargs="+")
    p = sub.add_parser("set")
    p.add_argument("session")
    p.add_argument("id")
    p.add_argument("value", nargs="+")
    for n in ("note", "giveup"):
        p = sub.add_parser(n)
        p.add_argument("session")
        p.add_argument("text", nargs="+")
    p = sub.add_parser("answer")
    p.add_argument("session")
    p.add_argument("items", nargs="+")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "list":
            out = cmd_list(a.persona, a.set)
        elif a.cmd == "start":
            out = cmd_start(a.run, a.persona, a.task)
        elif a.cmd == "show":
            out = cmd_show(a.session, a.all)
        elif a.cmd == "help":
            out = cmd_help(a.session, " ".join(a.id))
        elif a.cmd == "click":
            out = cmd_act(a.session, "click", " ".join(a.id))
        elif a.cmd == "set":
            out = cmd_act(a.session, "set", a.id, " ".join(a.value))
        elif a.cmd == "note":
            out = cmd_note(a.session, " ".join(a.text))
        elif a.cmd == "answer":
            out = cmd_answer(a.session, a.items)
        else:
            out = cmd_giveup(a.session, " ".join(a.text))
    except DriverError as ex:
        print(f"오류: {ex}")
        return 1
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
