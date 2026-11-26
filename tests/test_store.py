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
    print("읽지 못한 행 보존·백업 (L-052)")
    bad = row(name="오타행", credit_rw_pct="100%", note="메모오타")
    write([row(name="a", note="메모a"), bad], store.MASTER_COLS + ["note"])
    rows, bads, _ = store.load_master()
    check("오타행은 읽지 못한 행", len(bads) == 1 and "오타행" not in rows)
    rows["a2"] = row(name="a2")
    n = store.save_master(rows)
    cols, out = read()
    check("저장 후에도 오타행 원문 보존", n == 1 and any(r["name"] == "오타행" and r["credit_rw_pct"] == "100%"
                                                       and r["note"] == "메모오타" for r in out))
    check("저장 전 .bak 생성", (Path(D) / "counterparties.csv.bak").exists())
    fixed = {**rows, "오타행": row(name="오타행")}
    store.save_master(fixed)
    cols, out = read()
    check("고친 같은 이름 행이 있으면 옛 원문은 대체", [r["name"] for r in out].count("오타행") == 1
          and next(r for r in out if r["name"] == "오타행")["credit_rw_pct"] == "0")
    print("파라미터 변경 이력 (L-091)")
    from dataclasses import replace as _rep
    from linefee.trade import BankParams
    import json as _json
    lp = store.params_log_path()
    store.save_params(BankParams())
    check("기본값 그대로 저장하면 이력 없음", not lp.exists())
    store.save_params(_rep(BankParams(), hurdle_after_tax=0.12, n_paths=20000))
    lines = [_json.loads(x) for x in lp.read_text(encoding="utf-8").splitlines()]
    check("바뀐 키만 전 -> 후로 기록", len(lines) == 1 and lines[0]["changes"] == {
        "hurdle_after_tax": [0.1, 0.12], "n_paths": [10000, 20000]} and "ts" in lines[0])
    store.save_params(_rep(BankParams(), hurdle_after_tax=0.12, n_paths=20000))
    check("같은 값 다시 저장하면 이력 없음", len(lp.read_text(encoding="utf-8").splitlines()) == 1)
    store.save_params(BankParams())
    last = _json.loads(lp.read_text(encoding="utf-8").splitlines()[-1])["changes"]
    check("되돌리면 12% -> 10%도 기록", last["hurdle_after_tax"] == [0.12, 0.1])
    print("계산 기록 (L-092)")
    from types import SimpleNamespace as NS
    from linefee.trade import CSA, Counterparty, Leg, Trade
    cl = Counterparty("가명 고객 Z", 0.5, 0.05, 0.002)
    dl = Counterparty("가명 딜러 Y", 0.2, 0.05, 0.0003, csa=CSA(vm=True, im=True))
    deal = NS(client_trade=Trade([Leg("USD", 1e9, "receive", "fixed", 0.04), Leg("KRW", 1.4e12, "pay", "float", 0.001)], 5.0, "CCS"),
              client=cl, dealer=dl)
    run = NS(net=-1e8, net_se=1e6, day1=3e8, accounting_net=2e8, costs={"CVA": 1e8}, kappa={"KRW": 0.02},
             breakeven_hurdle=lambda: 0.08)
    rec = store.calc_record(deal, run, NS(extra=0.0003), BankParams(), dict(market_src="x", fee=0.001))
    check("기록에 실명 없음, 가명 키만", "가명 고객 Z" not in _json.dumps(rec, ensure_ascii=False)
          and rec["input"]["client"]["key"] == store.pseudonym("가명 고객 Z"))
    check("입력·파라미터·결과 포함", rec["input"]["maturity"] == 5.0 and rec["params"]["n_paths"] == 10000
          and rec["result"]["breakeven_extra"] == 0.0003 and rec["result"]["costs_krw"] == {"CVA": 1e8})
    cp_ = store.calc_log_path()
    check("기록 성공", store.log_calculation(rec) and store.log_calculation(rec))
    got = [_json.loads(x) for x in cp_.read_text(encoding="utf-8").splitlines()]
    check("두 줄 덧붙임, 시각 포함", len(got) == 2 and "ts" in got[0] and got[0]["result"]["net_krw"] == -1e8)
    cp_.unlink()
    cp_.mkdir()   # 파일 자리에 폴더 -> 쓰기 실패
    check("기록 실패해도 예외 없이 False", store.log_calculation(rec) is False)
    cp_.rmdir()
    check("직렬화 못 하는 값도 False", store.log_calculation({"x": object()}) is False)
    sys.exit(1 if FAILS else 0)
