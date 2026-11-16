"""화면 접근 한 겹: 하니스(driver, fault_inject, audit_roles, smoke)가 화면 프레임워크에 닿는 곳은 이 파일뿐이다.

화면을 구현하는 쪽(U0, U)이 프레임워크를 바꾸면 `ui/testing.py`에 `Backend` 클래스를 내면 된다 (DECISIONS O11).
`open_backend()`는 그 클래스가 있으면 그것을, 없으면 이 파일의 `StreamlitBackend`(Streamlit AppTest)를 쓴다.

규약 (Backend)
--------------
    Backend(data_dir: Path, admin_password: str | None)
        일반 사용자 화면(로그인하지 않은 상태)을 연다. 환경 변수 LINEFEE_DATA_DIR = data_dir 로 화면을 실행하고,
        관리자 비밀번호는 비밀값 admin_password 로 넣는다 (None이면 비밀값 없음. 일반 화면은 오류 없이 떠야 한다).
        한 프로세스에서 Backend 여러 개를 번갈아 써도 서로 섞이지 않아야 한다 (데이터 디렉터리는 행동마다 자기 것을 쓴다).

    elements() -> list[dict]
        지금 화면의 요소를 화면 순서(사이드바, 본문 순)로 돌려준다. 요소 하나는 dict:
          id       문자열. 위젯 key가 있으면 key, 없으면 "<종류>_<라벨>"(공백은 _). 같은 id가 또 나오면 두 번째부터 _2, _3.
                   다시 실행해도 같은 요소는 같은 id여야 한다. 글자 요소(heading, text, caption, 경고 등)의 라벨은 그 글자다.
          kind     heading, text, caption, warning, error, info, success, metric, table, chart, expander,
                   select, radio, number, text_input, checkbox, slider, button
          label    위젯·지표의 라벨, 글자 요소의 글자, 차트·표는 바로 앞 글자(없으면 "")
          value    number: float / text_input: str / checkbox: bool / select·radio·slider: 지금 고른 항목의 보이는 글자 /
                   button: None / metric: "<값>" 또는 "<값> | <보조 글>" / table: {"columns": [...], "rows": [[...], ...]}
                   (셀은 화면에 보이는 글자) / 그 밖: None
          options  select·radio·slider: 보이는 글자 목록. 그 밖: None
          help     도움말 글자 (없으면 None)
          key      위젯 key (없으면 None)
        선택 항목(없어도 된다): group = 바로 위 expander의 라벨 (없으면 ""), area = "sidebar" | "main".
        관리자 전용 위젯은 일반 사용자 화면에서 그리지 않으므로 이 목록에도 없어야 한다 (계약 5).

    set(elem_id: str, value) -> None
        위젯 값을 넣고 화면을 다시 실행한다. number: float, text_input: str, checkbox: bool,
        select·radio·slider: options에 있는 보이는 글자 그대로. 없는 id나 넣을 수 없는 값은 KeyError / ValueError (메시지는 사람이 읽는 한국어).

    click(elem_id: str) -> None
        버튼을 누르고 화면을 다시 실행한다. 버튼이 아니면 ValueError.

    result() -> dict | None
        session_state["result"]와 같은 항목의 dict (계약 2). 계산을 새로 하면 새 dict 객체여야 한다 (같은 객체면 "계산 안 함"으로 본다).
        아직 없으면 None.

    role() -> str
        "general" | "admin" (session_state["role"]이 "admin"이면 admin, 없으면 general).
"""
import importlib
import importlib.util
import logging
import os
import re
import sys
from pathlib import Path
from typing import Protocol

from ui_eval.contract import ENV_DATA_DIR, ROOT, SECRET_ADMIN

INPUT_KINDS = ("select", "radio", "number", "text_input", "checkbox", "slider")   # 값을 바꿀 수 있는 입력 위젯
TEXT_KINDS = ("heading", "text", "caption", "warning", "error", "info", "success")
ALERT_KINDS = ("warning", "error")


class Backend(Protocol):
    """모듈 docstring의 규약."""

    def __init__(self, data_dir: Path, admin_password: "str | None"): ...

    def elements(self) -> list: ...

    def set(self, elem_id: str, value) -> None: ...

    def click(self, elem_id: str) -> None: ...

    def result(self) -> "dict | None": ...

    def role(self) -> str: ...


def open_backend(data_dir, admin_password=None) -> Backend:
    """ui/testing.py의 Backend가 있으면 그것, 없으면 StreamlitBackend."""
    root = str(ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    if (ROOT / "ui" / "testing.py").exists():
        mod = importlib.import_module("ui.testing")
        if hasattr(mod, "Backend"):
            return mod.Backend(Path(data_dir), admin_password)
    return StreamlitBackend(Path(data_dir), admin_password)


def slug(text: str, n: int = 30) -> str:
    """id에 쓰는 이름: 글자·숫자·밑줄만 남기고 공백은 _로."""
    s = re.sub(r"[^\w]+", "_", str(text)).strip("_")
    return s[:n].strip("_")


def _quiet_logs():
    """AppTest가 내는 'missing ScriptRunContext' 경고를 지운다."""
    class Drop(logging.Filter):
        def filter(self, record):
            return "ScriptRunContext" not in record.getMessage()

    flt = Drop()
    for name in ["streamlit", *[n for n in logging.root.manager.loggerDict if n.startswith("streamlit")]]:
        lg = logging.getLogger(name)
        if not any(isinstance(f, Drop) for f in lg.filters):
            lg.addFilter(flt)
        for h in lg.handlers:
            if not any(isinstance(f, Drop) for f in h.filters):
                h.addFilter(flt)


def _cell(x) -> str:
    if isinstance(x, float):
        return f"{x:,.2f}"
    return str(x)


_KIND_OF_TYPE = {"title": "heading", "header": "heading", "subheader": "heading", "markdown": "text", "text": "text",
                 "code": "text", "caption": "caption", "warning": "warning", "error": "error", "exception": "error",
                 "info": "info", "success": "success", "metric": "metric", "dataframe": "table", "table": "table",
                 "selectbox": "select", "radio": "radio", "number_input": "number", "text_input": "text_input",
                 "text_area": "text_input", "checkbox": "checkbox", "toggle": "checkbox", "button": "button",
                 "select_slider": "slider", "slider": "slider"}


class StreamlitBackend:
    """Streamlit AppTest로 app.py를 실행한다."""

    def __init__(self, data_dir: Path, admin_password: "str | None"):
        from streamlit.testing.v1 import AppTest
        _quiet_logs()
        self.data_dir = Path(data_dir)
        self._at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=600)
        if admin_password is not None:
            self._at.secrets[SECRET_ADMIN] = admin_password
        self._run()

    def _run(self):
        os.environ[ENV_DATA_DIR] = str(self.data_dir)
        self._at.run()
        _quiet_logs()

    # ---- 화면 읽기 ----
    def _scan(self) -> list:
        """[(요소 dict, AppTest 노드)]. elements()와 set·click이 같은 id를 쓰도록 한곳에서 만든다."""
        out, seen = [], {}
        prev_text = [""]

        def add(node, kind, label, value=None, options=None, group="", area=""):
            key = getattr(node, "key", None) or None
            base = key or f"{kind}_{slug(label)}"
            seen[base] = seen.get(base, 0) + 1
            eid = base if seen[base] == 1 else f"{base}_{seen[base]}"
            h = getattr(node, "help", None) or None
            out.append(({"id": eid, "kind": kind, "label": label, "value": value, "options": options, "help": h,
                         "key": key, "group": group, "area": area}, node))

        def walk(block, group, area):
            for node in block.children.values():
                t = getattr(node, "type", None)
                if hasattr(node, "children"):
                    if t == "expander":
                        add(node, "expander", node.label, group=group, area=area)
                        prev_text[0] = node.label
                        walk(node, node.label, area)
                    else:
                        walk(node, group, area)
                    continue
                kind = _KIND_OF_TYPE.get(t)
                if kind is None and t and "chart" in t:
                    kind = "chart"
                if kind is None:
                    continue
                if kind in ("heading", "text", "caption", "warning", "error", "info", "success"):
                    text = str(node.value)
                    add(node, kind, text, group=group, area=area)
                    if kind in ("heading", "text"):
                        prev_text[0] = re.sub(r"[*_`]", "", text).strip()
                elif kind == "metric":
                    v = node.value + (f" | {node.delta}" if getattr(node, "delta", "") else "")
                    add(node, kind, node.label, v, group=group, area=area)
                elif kind == "table":
                    df = node.value
                    rows = [[_cell(x) for x in r] for r in df.itertuples(index=False)]
                    add(node, kind, prev_text[0], {"columns": [str(c) for c in df.columns], "rows": rows},
                        group=group, area=area)
                elif kind == "chart":
                    add(node, kind, prev_text[0], group=group, area=area)
                elif kind in ("select", "radio"):
                    opts = list(node.options)
                    idx = node.index
                    add(node, kind, node.label, opts[idx] if idx is not None and opts else None, opts, group, area)
                elif kind == "slider":
                    add(node, kind, node.label, str(node.value), [str(o) for o in node.options], group, area)
                elif kind == "number":
                    add(node, kind, node.label, None if node.value is None else float(node.value), group=group, area=area)
                elif kind == "checkbox":
                    add(node, kind, node.label, bool(node.value), group=group, area=area)
                elif kind == "text_input":
                    add(node, kind, node.label, node.value, group=group, area=area)
                elif kind == "button":
                    add(node, kind, node.label, group=group, area=area)

        walk(self._at.sidebar, "", "sidebar")
        walk(self._at.main, "", "main")
        return out

    def elements(self) -> list:
        return [e for e, _ in self._scan()]

    def _find(self, elem_id: str):
        for e, node in self._scan():
            if e["id"] == elem_id:
                return e, node
        raise KeyError(f"화면에 [{elem_id}] 요소가 없습니다")

    # ---- 행동 ----
    def set(self, elem_id: str, value) -> None:
        e, node = self._find(elem_id)
        kind, label = e["kind"], e["label"]
        if kind == "number":
            try:
                v = float(str(value).replace(",", "")) if not isinstance(value, (int, float)) else float(value)
            except ValueError:
                raise ValueError(f'"{label}"에는 숫자를 넣어야 합니다 (받은 값: {value})')
            if (node.min is not None and v < node.min) or (node.max is not None and v > node.max):
                lo = "제한 없음" if node.min is None else f"{node.min:g}"
                hi = "제한 없음" if node.max is None else f"{node.max:g}"
                raise ValueError(f'"{label}"은 {lo} 이상 {hi} 이하만 넣을 수 있습니다 (받은 값: {value})')
            node.set_value(v)
        elif kind == "text_input":
            node.set_value(str(value))
        elif kind == "checkbox":
            node.set_value(value if isinstance(value, bool) else str(value).strip().lower() in
                           ("1", "true", "t", "yes", "y", "on", "켜짐", "켬", "체크", "참"))
        elif kind in ("select", "radio", "slider"):
            opts = e["options"]
            if value not in opts:
                raise ValueError(f'"{label}"에서 고를 수 있는 것: {opts} (받은 값: {value})')
            if kind == "slider":
                raw = next((o for o in list(node.options) if str(o) == str(value)), value)
                try:
                    raw = int(raw)
                except (TypeError, ValueError):
                    pass
                node.set_value(raw)
            else:
                idx = opts.index(value)
                node.set_value(value)
                # 보이는 글자에서 원래 값을 되짚을 수 없는 위젯(format_func)이 있어 순번을 직접 지정한다
                node.__class__ = type(type(node).__name__, (type(node),), {"index": property(lambda self, i=idx: i)})
        else:
            raise ValueError(f'"{label}"은 값을 넣는 요소가 아닙니다 (종류: {kind})')
        self._run()

    def click(self, elem_id: str) -> None:
        e, node = self._find(elem_id)
        if e["kind"] != "button":
            raise ValueError(f'"{e["label"]}"은 버튼이 아닙니다 (종류: {e["kind"]})')
        node.click()
        self._run()

    def result(self):
        ss = self._at.session_state
        return ss["result"] if "result" in ss and ss["result"] is not None else None

    def role(self) -> str:
        ss = self._at.session_state
        return "admin" if "role" in ss and ss["role"] == "admin" else "general"
