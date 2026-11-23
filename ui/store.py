"""데이터 디렉터리의 거래상대방 마스터와 은행 파라미터 파일 읽기·쓰기.

환경 변수 LINEFEE_DATA_DIR(없으면 저장소의 data/)에서 읽고 쓴다 (tools/prompts/B_COMMON.md 화면 계약 6).
- counterparties.csv: 거래상대방 마스터. 없으면 data/counterparties.sample.csv(가명 샘플)를 읽는다.
- bank_params.json: 관리자가 저장한 은행 파라미터. 없거나 빠진 키는 BankParams 기본값.
Streamlit에 의존하지 않는다.
"""
import csv
import json
import os
from dataclasses import asdict, fields, replace
from pathlib import Path

from linefee.trade import BA_CVA_RW, CSA, BankParams, Counterparty

REPO_DATA = Path(__file__).resolve().parent.parent / "data"
SAMPLE = REPO_DATA / "counterparties.sample.csv"
MASTER_COLS = ["name", "grade", "pd_pct", "csa", "dealer_line", "credit_rw_pct", "ba_cva_sector", "vm_ccy"]
VM_CCYS = ["USD", "KRW"]   # VM 담보 통화 (선택 열, 없으면 USD). FVA 조달 스프레드 선택에 쓴다
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
    try:
        pd_, rw = float(row["pd_pct"]), float(row["credit_rw_pct"])
    except (KeyError, TypeError, ValueError):
        return "연 PD와 신용 위험가중치는 숫자"
    if not 0.0 <= pd_ <= 100.0 or rw < 0.0:
        return "연 PD는 0~100%, 신용 위험가중치는 0 이상"
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


def save_master(rows: dict) -> None:
    p = master_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".csv.tmp")
    with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, MASTER_COLS, extrasaction="ignore")
        w.writeheader()
        for r in rows.values():
            w.writerow({**r, "dealer_line": "Y" if is_dealer(r) else "N"})
    os.replace(tmp, p)


def counterparty(row: dict) -> Counterparty:
    """마스터 한 행 → Counterparty."""
    cva = BA_CVA_RW[row["ba_cva_sector"]][GRADES.index(row["grade"])]
    csa = {"없음": CSA(), "VM": CSA(vm=True), "VM+IM": CSA(vm=True, im=True)}[row["csa"].replace(" ", "")]
    ccy = str(row.get("vm_ccy") or "").strip().upper()
    if ccy in VM_CCYS:
        csa = replace(csa, ccy=ccy)
    return Counterparty(row["name"], float(row["credit_rw_pct"]) / 100, cva, float(row["pd_pct"]) / 100, LGD,
                        replace(csa))


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
