"""데이터 디렉터리의 거래상대방 마스터와 은행 파라미터 파일 읽기·쓰기.

환경 변수 LINEFEE_DATA_DIR(없으면 저장소의 data/)에서 읽고 쓴다 (tools/prompts/B_COMMON.md 화면 계약 6).
- counterparties.csv: 거래상대방 마스터. 없으면 data/counterparties.sample.csv(가명 샘플)를 읽는다.
- bank_params.json: 관리자가 저장한 은행 파라미터. 없거나 빠진 키는 BankParams 기본값.
Streamlit에 의존하지 않는다.
"""
import csv
import json
import math
import os
import shutil
from dataclasses import asdict, fields, replace
from pathlib import Path

from linefee.trade import BA_CVA_RW, CSA, BankParams, Counterparty

REPO_DATA = Path(__file__).resolve().parent.parent / "data"
SAMPLE = REPO_DATA / "counterparties.sample.csv"
MASTER_COLS = ["name", "grade", "pd_pct", "csa", "dealer_line", "credit_rw_pct", "ba_cva_sector", "vm_ccy", "rw_method", "sales_eok"]
VM_CCYS = ["USD", "KRW"]   # VM 담보 통화 (선택 열, 없으면 USD). FVA 조달 스프레드 선택에 쓴다
RW_METHODS = ["고정", "IRB"]   # 신용 RW: 고정(credit_rw_pct) 또는 기본내부등급법(PD·매출로 계산, 선택 열, 없으면 고정)
GRADES = ["투자등급", "투기등급·무등급"]
CSA_KINDS = ["없음", "VM", "VM+IM"]
LGD = 0.60   # 마스터에 없는 항목: 고정
# 관리자가 저장하는 은행 파라미터 필드 (그 밖의 필드는 BankParams 기본값 그대로)
PARAM_FIELDS = ["capital_ratio", "hurdle_after_tax", "tax_rate", "ftp_spread", "ftp_spread_krw", "kva_funding_spread",
                "kva_funding", "recognise_fba", "ba_cva_marginal_factor", "im_funding_spread", "rate_vol_scale",
                "rate_mean_reversion", "n_paths"]


def data_dir() -> Path:
    d = os.environ.get("LINEFEE_DATA_DIR")
    return Path(d) if d else REPO_DATA


def master_path() -> Path:
    return data_dir() / "counterparties.csv"


def params_path() -> Path:
    return data_dir() / "bank_params.json"


# ---------------------------------------------------------------------------
# 상대방 마스터
# ---------------------------------------------------------------------------
def parse_sales(v):
    """매출(억원) 칸 -> 실수 또는 None(빈 칸). 천 단위 쉼표는 허용한다 ('6,814.6'). 숫자가 아니면 ValueError."""
    t = str(v if v is not None else "").strip().replace(",", "")
    return float(t) if t else None


def check_row(row: dict) -> str:
    """행에 문제가 있으면 이유, 없으면 빈 문자열."""
    if not str(row.get("name", "")).strip():
        return "이름이 비어 있음"
    if row.get("grade") not in GRADES:
        return f"등급은 {' / '.join(GRADES)} 중 하나"
    if str(row.get("csa", "")).replace(" ", "") not in CSA_KINDS:
        return f"CSA는 {' / '.join(CSA_KINDS)} 중 하나"
    if row.get("ba_cva_sector") not in BA_CVA_RW:
        return "BA-CVA 업종이 목록에 없음"
    if (row.get("rw_method") or "고정") not in RW_METHODS:
        return f"RW 방식은 {' / '.join(RW_METHODS)} 중 하나"
    try:
        pd_, rw = float(row["pd_pct"]), float(row["credit_rw_pct"])
    except (KeyError, TypeError, ValueError):
        return "연 PD와 신용 위험가중치는 숫자"
    if not (math.isfinite(pd_) and math.isfinite(rw)):
        return "연 PD와 신용 위험가중치는 유한한 숫자 (nan·inf 불가)"
    if not 0.0 <= pd_ <= 100.0 or rw < 0.0:
        return "연 PD는 0~100%, 신용 위험가중치는 0 이상"
    vm = str(row.get("vm_ccy") or "").strip().upper()
    if vm and vm not in VM_CCYS:
        return f"VM 담보 통화는 {' / '.join(VM_CCYS)} 중 하나 (빈 칸이면 USD)"
    try:
        sales = parse_sales(row.get("sales_eok"))
    except ValueError:
        return "매출(억원)은 숫자 (빈 칸 가능)"
    if sales is not None and not (math.isfinite(sales) and sales >= 0.0):
        return "매출(억원)은 0 이상의 유한한 숫자"
    return ""


def is_dealer(row: dict) -> bool:
    return str(row.get("dealer_line", "")).strip().upper() in ("Y", "YES", "TRUE", "1")


def _read(path: Path) -> tuple[dict, list]:
    """(이름 → 행, 읽지 못한 행의 안내 목록)."""
    rows, bad = {}, []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for i, r in enumerate(csv.DictReader(f), 2):
            r = {k: (v or "").strip() for k, v in r.items() if k}
            r["csa"] = r.get("csa", "").replace(" ", "")
            why = check_row(r)
            if why:
                bad.append(f"{i}행({r.get('name', '')}): {why}")
            else:
                rows[r["name"]] = r
    return rows, bad


def sample_master() -> dict:
    return _read(SAMPLE)[0]


def load_master() -> tuple[dict, list, bool]:
    """(이름 → 행, 읽지 못한 행 안내, 저장된 마스터를 읽었는지). 저장본이 없으면 샘플."""
    p = master_path()
    if p.exists():
        rows, bad = _read(p)
        return rows, bad, True
    rows, bad = _read(SAMPLE)
    return rows, bad, False


def _file_columns(path: Path) -> list:
    """파일 머리줄의 열 이름 (없거나 못 읽으면 빈 목록)."""
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            return [c for c in (next(csv.reader(f), []) or []) if c]
    except OSError:
        return []


def unread_rows(path: Path | None = None) -> list:
    """파일에서 check_row를 통과하지 못한 행의 원문 (열 이름 -> 문자열). 마스터에 올라오지 않는 행이다."""
    p = path or master_path()
    out = []
    try:
        with open(p, encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                r = {k: (v or "") for k, v in r.items() if k}
                if check_row({k: v.strip() for k, v in r.items()} | {"csa": r.get("csa", "").replace(" ", "")}):
                    out.append(r)
    except OSError:
        pass
    return out


def save_master(rows: dict) -> int:
    """마스터를 쓰고, 읽지 못해 rows에 없는 행(check_row 실패)의 원문 개수를 돌려준다.

    - 읽지 못한 행은 지우지 않고 원문 그대로 이어 쓴다. 같은 이름의 정상 행이 rows에 있으면(고친 것이므로) 옛 원문은 뺀다.
    - MASTER_COLS 밖의 열(비고 등)은 파일 머리줄·행에 있는 것을 그대로 이어 쓴다.
    - 기존 파일은 쓰기 전에 counterparties.csv.bak으로 복사해 둔다 (직전 저장본 하나).
    """
    p = master_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    kept = [r for r in unread_rows(p) if r.get("name", "").strip() not in rows]
    extra = [c for c in _file_columns(p) if c not in MASTER_COLS]
    for r in [*rows.values(), *kept]:
        extra += [c for c in r if c and c not in MASTER_COLS and c not in extra]
    tmp = p.with_suffix(".csv.tmp")
    with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, MASTER_COLS + extra, extrasaction="ignore", restval="")
        w.writeheader()
        for r in rows.values():
            w.writerow({**r, "dealer_line": "Y" if is_dealer(r) else "N"})
        for r in kept:
            w.writerow(r)
    if p.exists():
        shutil.copy2(p, p.with_suffix(".csv.bak"))
    os.replace(tmp, p)
    return len(kept)


def counterparty(row: dict) -> Counterparty:
    """마스터 한 행 → Counterparty."""
    cva = BA_CVA_RW[row["ba_cva_sector"]][GRADES.index(row["grade"])]
    csa = {"없음": CSA(), "VM": CSA(vm=True), "VM+IM": CSA(vm=True, im=True)}[row["csa"].replace(" ", "")]
    ccy = str(row.get("vm_ccy") or "").strip().upper()
    if ccy in VM_CCYS:
        csa = replace(csa, ccy=ccy)
    irb = (row.get("rw_method") or "고정") == "IRB"
    sales = parse_sales(row.get("sales_eok"))   # check_row를 통과한 행이므로 숫자 또는 빈 칸
    return Counterparty(row["name"], float(row["credit_rw_pct"]) / 100, cva, float(row["pd_pct"]) / 100, LGD,
                        replace(csa), irb=irb, sales_eok=sales)


# ---------------------------------------------------------------------------
# 은행 파라미터
# ---------------------------------------------------------------------------
def load_params() -> BankParams:
    """저장된 값 위에 BankParams 기본값. 읽을 수 없는 파일·키는 기본값으로 둔다."""
    base = BankParams()
    try:
        saved = json.loads(params_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return base
    kinds = {f.name: type(getattr(base, f.name)) for f in fields(BankParams)}
    upd = {}
    for k, v in (saved.items() if isinstance(saved, dict) else []):
        t = kinds.get(k)
        if t is None or isinstance(v, bool) != (t is bool):
            continue
        try:
            upd[k] = t(v)
        except (TypeError, ValueError):
            pass
    return replace(base, **upd)


def save_params(p: BankParams) -> None:
    path = params_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    d = asdict(p)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({k: d[k] for k in PARAM_FIELDS}, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)
