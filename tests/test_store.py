"""마스터 읽기·검사·쓰기 단정 (임시 폴더, 블룸버그·화면 없이). python -m tests.test_store"""
import csv
import os
import sys
import tempfile
from pathlib import Path

D = tempfile.mkdtemp(prefix="store_")
os.environ["LINEFEE_DATA_DIR"] = D

from ui import store  # noqa: E402

FAILS = []


def check(label, ok):
    print(f"  {'ok  ' if ok else '실패'} {label}")
    if not ok:
        FAILS.append(label)


def row(**kw):
    base = dict(name="x", grade="투자등급", pd_pct="0.355", csa="VM", dealer_line="N", credit_rw_pct="0",
                ba_cva_sector="기타", vm_ccy="", rw_method="고정", sales_eok="")
    return base | kw


def write(rows, cols):
    with open(Path(D) / "counterparties.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, cols)
        w.writeheader()
        w.writerows(rows)


def read():
    with open(Path(D) / "counterparties.csv", encoding="utf-8-sig", newline="") as f:
        r = csv.DictReader(f)
        return r.fieldnames, list(r)


if __name__ == "__main__":
    print("check_row 값 검사 (L-048, L-058)")
    check("정상 행 통과", store.check_row(row()) == "")
    check("vm_ccy EUR 거부", "VM 담보 통화" in store.check_row(row(vm_ccy="EUR")))
    check("vm_ccy krw(소문자) 허용", store.check_row(row(vm_ccy="krw")) == "")
    check("매출 비숫자 거부", "매출" in store.check_row(row(sales_eok="abc")))
    check("매출 음수 거부", "매출" in store.check_row(row(sales_eok="-1")))
    check("매출 쉼표 허용", store.check_row(row(sales_eok="6,814.6")) == ""
          and store.counterparty(row(sales_eok="6,814.6")).sales_eok == 6814.6)
    check("RW nan 거부", store.check_row(row(credit_rw_pct="nan")) != "")
    check("PD inf 거부", store.check_row(row(pd_pct="inf")) != "")

    print("save_master가 MASTER_COLS 밖 열을 보존 (L-056)")
    write([row(name="a", note="메모a"), row(name="b", note="메모b")], store.MASTER_COLS + ["note"])
    rows, bad, _ = store.load_master()
    rows["c"] = row(name="c")
    store.save_master(rows)
    cols, out = read()
    check("note 열 유지", "note" in cols and out[0]["note"] == "메모a" and out[2]["note"] == "")
    sys.exit(1 if FAILS else 0)
