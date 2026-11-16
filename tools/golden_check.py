"""값 불변 게이트: 고정 거래 세트를 고정 시드로 계산해 로컬 기준값과 비교한다.

    python tools/golden_check.py            # 기준값과 비교 (다르면 전/후 표, 종료 코드 1)
    python tools/golden_check.py --write    # 기준값 새로 쓰기 (사용자 승인 후에만)
    python tools/golden_check.py --ui       # 대시보드 경로(AppTest)도 함께 비교
    python tools/golden_check.py --only CCS_USDKRW_5Y_LINE

기준값은 local/golden_baseline.json (gitignore, 커밋 금지: docs/DECISIONS.md O2).
worktree에서 실행해도 메인 체크아웃의 local/과 시장 스냅샷을 쓴다. 스냅샷이 worktree에 없으면
메인에서 복사한다 (gitignore 대상이라 diff에 잡히지 않는다).

층:
  engine  linefee를 직접 호출. 거래 8건 x 지표(첫날 이익, 비용 항목, 순마진, 손익분기)
  ui      app.py를 AppTest로 실행해 예시 거래 5건의 '계산' 결과를 읽는다 (--ui)
"""
import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import ROOT, local_dir, main_root, utf8_stdout  # noqa: E402

sys.path.insert(0, str(ROOT))

BASELINE = "golden_baseline.json"
SNAPSHOTS = ["market_snapshot.json", "linefee/market_snapshot.json"]   # legacy(엔/원 검토), latest
REL_TOL, ABS_TOL = 1e-9, 1e-3     # 원 단위 값. 같은 코드·시드·스냅샷이면 비트 단위로 같아야 한다
SEED, N_PATHS = 11, 5000          # BankParams 기본값과 같다. 여기서 명시해 기본값이 바뀌어도 고정


def ensure_snapshots():
    """worktree에는 gitignore된 스냅샷이 없다. 메인 체크아웃에서 복사한다."""
    src_root = main_root()
    for rel in SNAPSHOTS:
        dst = ROOT / rel
        if not dst.exists():
            src = src_root / rel
            if not src.exists():
                sys.exit(f"시장 스냅샷이 없습니다: {src} (로컬 전용 파일. python -m linefee.market 으로 받는다)")
            shutil.copy2(src, dst)


def snapshot_hashes() -> dict:
    return {rel: hashlib.sha256((ROOT / rel).read_bytes()).hexdigest()[:16] for rel in SNAPSHOTS}


# ---------------------------------------------------------------------------
# engine 층
# ---------------------------------------------------------------------------
def golden_deals():
    """(이름, 시장 종류, Deal 만드는 함수) 목록. app.py 예시 거래와 같은 조건 + 방향·수수료 위치 변형."""
    from linefee.pricing import market_hedge, par_client
    from linefee.trade import BA_CVA_RW, CSA, Counterparty, Leg, Trade, build_deal

    def cva(sector, ig=True):
        return BA_CVA_RW[sector][0 if ig else 1]

    corp = "소재·에너지·산업재·농업·제조·광업"
    fin = "금융기관(정부보증 금융 포함)"
    pub = "지방정부·정부보증 비금융·공공기관"
    lh = Counterparty("LH", 0.0, cva(pub), 0.0, 0.6, CSA())
    nomura = Counterparty("노무라", 0.50, cva(fin), 0.0, 0.6, CSA(vm=True, im=True))
    corp_cl = Counterparty("기업 고객", 1.00, cva(corp), 0.002, 0.6, CSA())
    corp_hy = Counterparty("기업 고객(무등급)", 1.00, cva(corp, False), 0.01, 0.6, CSA())
    dealer_im = Counterparty("헤지 딜러", 0.50, cva(fin), 0.0, 0.6, CSA(vm=True, im=True))
    dealer_vm = Counterparty("헤지 딜러", 0.50, cva(fin), 0.0, 0.6, CSA(vm=True))

    def ccp(ccy):
        return Counterparty("CCP", 0.02, 0.0, 0.0, 0.6, CSA(vm=True, im=False, reg_mpor_days=5, ccy=ccy, ccp=True))

    def jpykrw(m):
        return Trade([Leg("KRW", 1e10 * m.spot_quote["JPY"], "receive", rate=0.0403, day_count="ACT/360"),
                      Leg("JPY", 1e10, "pay", rate=0.0407)], 30.0)

    def usdkrw_ccs(m):   # 은행 KRW 고정 수취 / USD SOFR 지급, 고객 조건은 시장가(화면처럼 % 소수 4자리 반올림)
        t = Trade([Leg("KRW", 1e8 * m.spot_quote["USD"], "receive", rate=0.03, freq=4),
                   Leg("USD", 1e8, "pay", kind="float", freq=4, day_count="ACT/360")], 5.0)
        return t.bumped(0, round(par_client(t, m).legs[0].rate, 6) - 0.03)

    def usdkrw_ccs_rev(m):   # 방향 반대: 은행 USD SOFR 수취 / KRW 고정 지급. 수취 다리(USD 스프레드)를 시장가로
        t = Trade([Leg("USD", 1e8, "receive", kind="float", freq=4, day_count="ACT/360"),
                   Leg("KRW", 1e8 * m.spot_quote["USD"], "pay", rate=0.03, freq=4)], 5.0)
        return t.bumped(0, round(par_client(t, m).legs[0].rate, 8))

    def fxfwd(m):
        t = Trade([Leg("KRW", 1e8 * m.spot_quote["USD"], "receive"), Leg("USD", 1e8, "pay")], 1.0, "FXFWD")
        fwd = par_client(t, m).legs[0].notional / 1e8
        return Trade([Leg("KRW", round(fwd, 2) * 1e8, "receive"), Leg("USD", 1e8, "pay")], 1.0, "FXFWD")

    def irs(m, ccy, n, mat, freq, dc):   # 은행 고정 수취, 고정금리는 파(% 소수 4자리)
        mk = lambda r: Trade([Leg(ccy, n, "receive", "fixed", r, freq, dc, False, "local"),  # noqa: E731
                              Leg(ccy, n, "pay", "float", 0.0, freq, dc, False, "local")], mat, "IRS")
        par = next(l for l in market_hedge(mk(0.03), m).legs if l.kind == "fixed").rate
        return mk(round(par, 6))

    def line(tr, m, cl, dl, fee):
        return build_deal(tr, cl, dl, hedge=market_hedge(tr, m), fee_location="client", fee=fee)

    return [
        ("CCS_JPYKRW_30Y_BTB_CLIENT10", "legacy",
         lambda m: build_deal(jpykrw(m), lh, nomura, fee_location="client", fee=10.0)),
        ("CCS_JPYKRW_30Y_BTB_HEDGE10", "legacy",
         lambda m: build_deal(jpykrw(m), lh, nomura, fee_location="hedge", fee=10.0)),
        ("CCS_JPYKRW_30Y_BTB_UPFRONT5", "legacy",
         lambda m: build_deal(jpykrw(m), lh, nomura, fee_location="upfront", upfront_krw=5e8)),
        ("CCS_USDKRW_5Y_LINE", "latest", lambda m: line(usdkrw_ccs(m), m, corp_cl, dealer_im, 10.0)),
        ("CCS_USDKRW_5Y_LINE_REV_HY", "latest", lambda m: line(usdkrw_ccs_rev(m), m, corp_hy, dealer_im, 10.0)),
        ("FXFWD_USDKRW_1Y_LINE", "latest", lambda m: line(fxfwd(m), m, corp_cl, dealer_vm, 1.0)),
        ("IRS_KRW_5Y_CCP", "latest",
         lambda m: line(irs(m, "KRW", 1e11, 5.0, 4, "ACT/365"), m, corp_cl, ccp("KRW"), 5.0)),
        ("OIS_USD_3Y_CCP", "latest",
         lambda m: line(irs(m, "USD", 1e8, 3.0, 1, "ACT/360"), m, corp_cl, ccp("USD"), 5.0)),
    ]


def run_metrics(r, be) -> dict:
    h = r.breakeven_hurdle()
    out = {"day1": r.day1, "v0_client": r.v0_client, "v0_hedge": r.v0_hedge, "total_cost": r.total_cost,
           "net": r.net, "accounting_net": r.accounting_net, "net_se": r.net_se,
           "breakeven_hurdle": -1.0 if h is None else float(h),
           "be.extra": be.extra, "be.extra_upfront_krw": be.extra_upfront_krw,
           "be.converged": float(be.converged), "be.n_runs": float(len(be.runs))}
    out.update({f"cost.{k}": float(v) for k, v in r.costs.items()})
    return {k: float(v) for k, v in out.items()}


def engine_layer(only=None) -> dict:
    from linefee.market import load, load_legacy
    from linefee.pricing import breakeven, run
    from linefee.trade import BankParams

    p = BankParams(n_paths=N_PATHS, seed=SEED)
    res = {}
    for name, src, make in golden_deals():
        if only and name not in only:
            continue
        # 시장 객체는 QuantLib 평가일(전역)을 바꾼다. 거래마다 다시 읽어 실행 순서의 영향을 없앤다
        m = load_legacy() if src == "legacy" else load()
        deal = make(m)
        base = run(deal, m, p)
        res[name] = run_metrics(base, breakeven(deal, m, p, base=base))
        print(f"  engine {name}: 순마진 {res[name]['net'] / 1e8:+.3f}억", flush=True)
    return res


# ---------------------------------------------------------------------------
# ui 층
# ---------------------------------------------------------------------------
def ui_layer(only=None) -> dict:
    """app.py를 AppTest로 돌려 예시 거래의 계산 결과(session_state['result'])를 읽는다.

    위젯 key 'preset'과 버튼 라벨 '계산', session_state['result']의 base·be에 의존한다.
    UI 개편으로 이 셋이 바뀌면 이 함수만 맞춰 고친다 (값 비교 기준은 그대로).
    """
    from streamlit.testing.v1 import AppTest

    res = {}
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=600)
    at.run()
    if at.exception:
        sys.exit(f"app.py 실행 오류: {at.exception[0].value}")
    presets = list(at.selectbox(key="preset").options)
    for name in presets:
        key = "UI:" + name
        if only and key not in only:
            continue
        at.selectbox(key="preset").set_value(name).run()
        next(b for b in at.button if b.label == "계산").click().run()
        if at.exception:
            sys.exit(f"{name}: 계산 중 오류: {at.exception[0].value}")
        r = at.session_state["result"] if "result" in at.session_state else None
        if r is None:
            sys.exit(f"{name}: 계산 결과가 없습니다 (입력 검증 오류: {[e.value for e in at.error]})")
        res[key] = run_metrics(r["base"], r["be"])
        print(f"  ui     {name}: 순마진 {res[key]['net'] / 1e8:+.3f}억", flush=True)
    return res


# ---------------------------------------------------------------------------
# 비교
# ---------------------------------------------------------------------------
def diff_rows(old: dict, new: dict) -> list:
    rows = []
    for trade in sorted(set(old) | set(new)):
        a, b = old.get(trade), new.get(trade)
        if a is None or b is None:
            rows.append((trade, "(거래)", a is not None and "있음" or "없음", b is not None and "있음" or "없음", ""))
            continue
        for k in sorted(set(a) | set(b)):
            x, y = a.get(k), b.get(k)
            if x is None or y is None:
                rows.append((trade, k, x, y, "항목 추가·삭제"))
            elif abs(x - y) > max(ABS_TOL, REL_TOL * max(abs(x), abs(y))):
                rows.append((trade, k, x, y, f"{y - x:+,.6g}"))
    return rows


def fmt(v) -> str:
    return v if isinstance(v, str) else ("-" if v is None else f"{v:,.6f}")


def main():
    utf8_stdout()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--write", action="store_true", help="기준값을 새로 쓴다 (사용자 승인 후)")
    ap.add_argument("--ui", action="store_true", help="대시보드 경로(AppTest)도 계산한다")
    ap.add_argument("--only", nargs="*", help="이 이름의 거래만")
    ap.add_argument("--baseline", default=None, help=f"기준값 파일 (기본 local/{BASELINE})")
    a = ap.parse_args()

    ensure_snapshots()
    path = Path(a.baseline) if a.baseline else local_dir() / BASELINE
    print(f"체크아웃 {ROOT}\n기준값   {path}")
    now = {"meta": {"seed": SEED, "n_paths": N_PATHS, "snapshots": snapshot_hashes()},
           "engine": engine_layer(a.only)}
    if a.ui:
        now["ui"] = ui_layer(a.only)

    if a.write:
        if a.only:
            sys.exit("--write 는 --only 와 함께 쓸 수 없습니다 (기준값은 전체 세트로만 쓴다)")
        if path.exists() and "ui" not in now:   # --ui 없이 다시 쓸 때 기존 ui 기준값은 남긴다
            old = json.loads(path.read_text(encoding="utf-8"))
            if "ui" in old:
                now["ui"] = old["ui"]
        path.write_text(json.dumps(now, indent=1, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        print(f"기준값 저장: engine {len(now['engine'])}건" + (f", ui {len(now['ui'])}건" if "ui" in now else ""))
        return 0

    if not path.exists():
        sys.exit("기준값 파일이 없습니다. 먼저 --write --ui 로 만든다.")
    old = json.loads(path.read_text(encoding="utf-8"))
    if old["meta"] != now["meta"]:
        print("경고: 시드·경로 수·스냅샷이 기준값을 만들 때와 다릅니다. 값 차이는 코드가 아니라 이 때문일 수 있습니다.")
        print(f"  기준값 {old['meta']}\n  지금   {now['meta']}")
    rows = []
    for layer in ("engine", "ui"):
        if layer not in now:
            continue
        ref = old.get(layer, {})
        if a.only:
            ref = {k: v for k, v in ref.items() if k in now[layer]}
        rows += [(layer,) + r for r in diff_rows(ref, now[layer])]
    if not rows:
        n = sum(len(now[k]) for k in ("engine", "ui") if k in now)
        print(f"통과: {n}건 모두 기준값과 같습니다.")
        return 0
    print(f"\n차이 {len(rows)}개 (단위: 원. be.extra는 연 소수 또는 원/외화 1단위)")
    print("| 층 | 거래 | 항목 | 기준값 | 지금 | 차이 |\n|---|---|---|---:|---:|---:|")
    for layer, trade, k, x, y, d in rows:
        print(f"| {layer} | {trade} | {k} | {fmt(x)} | {fmt(y)} | {d} |")
    return 1


if __name__ == "__main__":
    sys.exit(main())
