"""트랙 B 평가 계약: 과제 사양 → 의도한 입력, 화면 계산 결과 → 실제 입력, 둘의 비교, 정답 항목.

총괄 소유 파일이다 (tools/path_gate.py 전체 금지 목록). 하니스(driver, score)는 이 모듈을 import해서 쓰고 고치지 않는다.

- 과제 파일은 YAML의 JSON 부분집합으로 쓴다 (PyYAML 없이 json으로 읽는다).
- "입력"은 계산 한 번에 들어간 모든 조건을 엔진 단위로 적은 dict다 (inputs_from_deal). 화면 프레임워크와 무관하다.
- 정답 파일은 저장소 밖에 둔다 (스냅샷에서 나온 값: DECISIONS O2). 만드는 법: python -m ui_eval.make_answers
"""
import csv
import json
import os
from dataclasses import asdict, replace
from pathlib import Path

from linefee.pricing import Run, breakeven, market_hedge, par_client, run
from linefee.trade import (BA_CVA_RW, CCP_REG_MPOR_DAYS, CSA, BankParams, Counterparty, Leg, Trade, build_deal,
                           fee_delta)

ROOT = Path(__file__).resolve().parent.parent
PRIVATE_DIR = Path(os.environ.get("LINEFEE_PRIVATE_DIR", r"c:\projects\fun_private"))   # 저장소 밖: 숨긴 과제, 정답, 숨긴 로그
TASKS_PUBLIC = ROOT / "ui_eval" / "tasks_public.yaml"
TASKS_HIDDEN = PRIVATE_DIR / "tasks_hidden.yaml"
ANSWERS = {"public": PRIVATE_DIR / "answers_public.json", "hidden": PRIVATE_DIR / "answers_hidden.json"}
MASTER_SAMPLE = ROOT / "data" / "counterparties.sample.csv"

# 화면이 지켜야 하는 환경 변수·비밀값 이름 (tools/prompts/B_COMMON.md "화면 계약")
ENV_DATA_DIR = "LINEFEE_DATA_DIR"            # 상대방 마스터·은행 파라미터 파일을 읽고 쓰는 디렉터리 (기본 data/)
SECRET_ADMIN = "admin_password"              # .streamlit/secrets.toml 의 키
EVAL_ADMIN_PASSWORD = "eval-admin-pw"        # 평가 세션에만 주입하는 값 (실제 비밀번호 아님)
ADMIN_KEY_PREFIX = "adm_"                    # 관리자 전용 위젯 key 접두어

OKU = 1e8
UNIT = {"KRW": 1e8, "JPY": 1e8, "USD": 1e6}  # 화면 명목 단위: 억원, 억엔, 백만달러
CSA_TYPES = {"없음": CSA(), "VM": CSA(vm=True), "VM+IM": CSA(vm=True, im=True)}
GRADES = ["투자등급", "투기등급·무등급"]
LGD = 0.60                                   # 마스터에 없는 항목: 고정

# 정답 항목 이름 → 페르소나에게 보여 줄 설명 (정의는 answers_from_result)
FIELD_LABELS = {
    "day1_oku": "첫날 이익 (억원)",
    "acct_oku": "회계 손익, E/L만 차감 (억원)",
    "net_oku": "자본비용 반영 손익 (억원)",
    "be_fee_bp": "손익분기 라인피 (연 bp)",
    "be_fee_won": "손익분기 라인피, 선도환율 마진 (원/달러)",
    "fee_verdict": "제안한 라인피가 손익분기에 비해 부족한지 여유인지 (부족 또는 여유)",
    "be_upfront_oku": "upfront로만 받는다면 필요한 금액 = 손익분기 upfront (억원)",
    "be_running_bp": "upfront 없이 running으로 받는다면 필요한 고객 다리 라인피 (연 bp)",
    "be_hurdle_pct": "손익분기 세후 요구수익률 (%. 범위 밖이면 0% 미만 또는 50% 이상)",
}

# 채점 허용오차 (정답 항목 접미어별)
ANSWER_TOL = {"_oku": 0.1, "_bp": 0.1, "_won": 0.02, "_pct": 0.1}


def load_tasks(which: str) -> list:
    """which: "public" | "hidden". 과제 dict 목록."""
    path = TASKS_PUBLIC if which == "public" else TASKS_HIDDEN
    return json.loads(path.read_text(encoding="utf-8"))["tasks"]


def task_set(task_id: str) -> str:
    """과제 id로 세트를 가른다. H로 시작하면 숨긴 과제."""
    return "hidden" if task_id.startswith("H") else "public"


# ---------------------------------------------------------------------------
# 상대방 마스터
# ---------------------------------------------------------------------------
def master_row(row: dict) -> Counterparty:
    """마스터 한 행 → Counterparty. 열: name, grade, pd_pct, csa, dealer_line, credit_rw_pct, ba_cva_sector."""
    cva = BA_CVA_RW[row["ba_cva_sector"]][GRADES.index(row["grade"])]
    return Counterparty(row["name"], float(row["credit_rw_pct"]) / 100, cva, float(row["pd_pct"]) / 100, LGD,
                        replace(CSA_TYPES[row["csa"].replace(" ", "")]))


def load_master(path: Path = MASTER_SAMPLE) -> dict:
    """이름 → 마스터 행(dict). 파일은 UTF-8(BOM 허용) CSV."""
    with open(path, encoding="utf-8-sig", newline="") as f:
        return {r["name"]: r for r in csv.DictReader(f)}


def ccp(ccy: str) -> Counterparty:
    return Counterparty("CCP", 0.02, 0.0, 0.0, LGD,
                        CSA(vm=True, im=False, reg_mpor_days=CCP_REG_MPOR_DAYS, ccy=ccy, ccp=True))


# ---------------------------------------------------------------------------
# 과제 사양 → 거래 조합 (화면의 예시 거래 + 바꿀 조건)
# ---------------------------------------------------------------------------
def _irs(ccy: str, amount: float, mat: float, recv_fixed: bool, rate: float, spread: float) -> Trade:
    freq, dc = (4, "ACT/365") if ccy == "KRW" else (1, "ACT/360")
    n = amount * UNIT[ccy]
    fixed = Leg(ccy, n, "receive" if recv_fixed else "pay", "fixed", rate, freq, dc, False, "local")
    flt = Leg(ccy, n, "pay" if recv_fixed else "receive", "float", spread, freq, dc, False, "local")
    return Trade([fixed, flt] if recv_fixed else [flt, fixed], mat, "IRS")


def build_case(spec: dict, m, master: dict = None) -> dict:
    """과제의 inputs 사양으로 계산 한 건을 만든다. 반환: deal 조립에 쓴 조각 (compute()에 넘긴다).

    사양 항목 (없으면 예시 거래 그대로):
      preset          1~5. 화면 예시 목록의 순서 (엔/원 30년 CCS, USD/KRW 5년 CCS, USD/KRW 1년 FX 선도, 원화 IRS 5년, 달러 OIS 3년)
      maturity        만기 (년)
      scale           CCS·FX 선도: 두 다리 명목에 같은 배수
      usd_spread_bp   예시 2: USD 변동 다리 가산 스프레드 (bp)
      reprice         true면 고객 조건을 시장가로 다시 맞춘다 (CCS: 은행 수취 다리 금리, FX 선도: KRW 금액)
      fx_reverse      예시 3: 방향 반대 (은행 USD 수취 / KRW 지급 = 고객이 달러를 판다)
      irs_amount      IRS·OIS 명목 (화면 단위: 억원, 백만달러)
      irs_direction   "receive_fixed"(은행 고정 수취, 기본) | "pay_fixed"(은행 고정 지급 = 고객 고정 수취)
      client, dealer  마스터 이름
      hedge_mode      "btb" | "line"
      fee_location    "client" | "hedge" | "upfront"
      fee             라인피 (화면 단위: 연 bp, FX 선도는 원/달러)
      upfront_oku     upfront 수취 (억원)
      params          BankParams 항목 덮어쓰기 (관리자 과제)
      master_edit     마스터 행 추가·수정 목록 (관리자 과제). 이름이 같으면 덮어쓴다
    """
    master = dict(master or load_master())
    for row in spec.get("master_edit", []):
        master[row["name"]] = {**master.get(row["name"], {}), **row}
    cp = lambda name: master_row(master[name])   # noqa: E731
    k = spec["preset"]
    scale = spec.get("scale", 1.0)
    if k == 1:
        mat = spec.get("maturity", 30.0)
        trade = Trade([Leg("KRW", 100 * OKU * m.spot_quote["JPY"] * scale, "receive", rate=0.0403, day_count="ACT/360"),
                       Leg("JPY", 100 * OKU * scale, "pay", rate=0.0407)], mat)
        d = dict(client="공공기관 A", dealer="딜러 B", hedge_mode="btb", fee=10.0)
    elif k == 2:
        mat = spec.get("maturity", 5.0)
        mk = lambda r: Trade([Leg("KRW", OKU * m.spot_quote["USD"] * scale, "receive", rate=r, freq=4),   # noqa: E731
                              Leg("USD", 1e8 * scale, "pay", kind="float", rate=spec.get("usd_spread_bp", 0.0) / 1e4,
                                  freq=4, day_count="ACT/360")], mat)
        if spec.get("reprice"):
            trade = mk(round(par_client(mk(0.03), m).legs[0].rate * 100, 6) / 100)
        else:   # 예시를 불러올 때의 금리: 5년, 스프레드 0의 시장가 (% 소수 4자리)
            p0 = Trade([Leg("KRW", OKU * m.spot_quote["USD"], "receive", rate=0.03, freq=4),
                        Leg("USD", 1e8, "pay", kind="float", freq=4, day_count="ACT/360")], 5.0)
            trade = mk(round(par_client(p0, m).legs[0].rate * 100, 4) / 100)
        d = dict(client="기업 C", dealer="딜러 B", hedge_mode="line", fee=10.0)
    elif k == 3:
        mat = spec.get("maturity", 1.0)
        usd = 1e8 * scale
        par = lambda t: par_client(Trade([Leg("KRW", usd * m.spot_quote["USD"], "receive"), Leg("USD", usd, "pay")],   # noqa: E731
                                         t, "FXFWD"), m).legs[0].notional
        krw = par(mat) if spec.get("reprice") or mat == 1.0 else par(1.0)
        legs = [Leg("KRW", krw, "receive"), Leg("USD", usd, "pay")]
        if spec.get("fx_reverse"):
            legs = [Leg("USD", usd, "receive"), Leg("KRW", krw, "pay")]
        trade = Trade(legs, mat, "FXFWD")
        d = dict(client="기업 C", dealer="딜러 D", hedge_mode="line", fee=1.0)
    elif k in (4, 5):
        ccy = "KRW" if k == 4 else "USD"
        mat = spec.get("maturity", 5.0 if k == 4 else 3.0)
        amount = spec.get("irs_amount", 1000.0 if k == 4 else 100.0)
        recv = spec.get("irs_direction", "receive_fixed") == "receive_fixed"
        probe = _irs(ccy, amount, mat, recv, 0.03, 0.0)
        parr = next(l for l in market_hedge(probe, m).legs if l.kind == "fixed").rate
        trade = _irs(ccy, amount, mat, recv, round(parr * 100, 4) / 100, 0.0)
        d = dict(client="기업 C", dealer=None, hedge_mode="ccp", fee=5.0)
    else:
        raise ValueError(f"preset {k}")
    hedge_mode = spec.get("hedge_mode", d["hedge_mode"])
    client = cp(spec.get("client", d["client"]))
    dealer = ccp(trade.legs[0].ccy) if hedge_mode == "ccp" else cp(spec.get("dealer", d["dealer"]))
    loc = spec.get("fee_location", "client")
    fee = 0.0 if loc == "upfront" else spec.get("fee", d["fee"])
    return dict(client_no_fee=trade, client=client, dealer=dealer, hedge_kind=hedge_mode, loc=loc,
                fee=fee_delta(trade, fee), fee_ui=fee, upfront=spec.get("upfront_oku", 0.0) * OKU,
                params=BankParams(**spec.get("params", {})))


def compute(case: dict, m) -> dict:
    """build_case()의 조각으로 화면과 같은 순서로 계산한다. 반환은 화면의 session_state["result"]와 같은 항목."""
    tr, p = case["client_no_fee"], case["params"]
    hedge = market_hedge(tr, m) if case["hedge_kind"] in ("line", "ccp") else None
    deal = build_deal(tr, case["client"], case["dealer"], hedge=hedge, fee_location=case["loc"], fee=case["fee_ui"],
                      upfront_krw=case["upfront"])
    base = run(deal, m, p)
    be = breakeven(deal, m, p, base=base)
    zero = be_running = None
    if case["hedge_kind"] == "btb" and case["loc"] != "upfront":
        zero = run(build_deal(tr, case["client"], case["dealer"], fee_location="upfront"), m, p)
    if case["loc"] == "upfront":
        be_running = breakeven(build_deal(tr, case["client"], case["dealer"], hedge=hedge, fee_location="client",
                                          fee=0.0), m, p)
    return dict(case, deal=deal, base=base, be=be, zero=zero, be_running=be_running)


# ---------------------------------------------------------------------------
# 입력 비교
# ---------------------------------------------------------------------------
def _cp(c: Counterparty) -> dict:
    return {"credit_rw": c.credit_rw, "cva_rw": c.cva_rw, "pd": c.pd, "lgd": c.lgd, "vm": c.csa.vm, "im": c.csa.im,
            "ccp": c.csa.ccp}


def inputs_from_result(res: dict) -> dict:
    """계산 한 건의 입력. res는 화면의 session_state["result"] 또는 compute()의 반환.

    쓰는 항목: client_no_fee(라인피 전 고객 거래), deal(client, dealer), hedge_kind, loc, fee, upfront, params.
    상대방은 이름이 아니라 계산에 들어가는 값으로 비교한다 (이름은 결과에 영향이 없다).
    """
    tr, deal = res["client_no_fee"], res["deal"]
    legs = {}
    for l in tr.legs:
        legs[l.direction] = {"ccy": l.ccy, "notional": l.notional, "kind": l.kind, "rate": l.rate}
        if tr.product != "FXFWD":
            legs[l.direction].update(freq=l.freq, day_count=l.day_count, final_exchange=l.final_exchange)
    out = {"product": tr.product, "maturity": tr.maturity, "legs": legs, "client": _cp(deal.client),
           "dealer": _cp(deal.dealer), "hedge_kind": res["hedge_kind"], "fee_location": res["loc"],
           "fee": res["fee"], "upfront_krw": res["upfront"],
           "params": {k: v for k, v in asdict(res["params"]).items()}}
    if res["hedge_kind"] == "direct":   # 직접 입력 헤지는 과제에 없다. 기록만 남긴다
        out["hedge_legs"] = {l.direction: {"ccy": l.ccy, "notional": l.notional, "kind": l.kind, "rate": l.rate}
                             for l in deal.hedge_trade.legs}
    return out


def _flat(d: dict, pre: str = "") -> dict:
    out = {}
    for k, v in d.items():
        out.update(_flat(v, f"{pre}{k}.") if isinstance(v, dict) else {pre + k: v})
    return out


def diff_inputs(expected: dict, actual: dict) -> list:
    """의도한 입력과 실제 입력이 다른 항목 목록. [{"field", "expected", "actual"}]. 빈 목록이면 같다.

    허용오차: 명목 상대 1e-4, 금리·스프레드 0.05bp, FX 선도 마진 0.005원, 그 밖의 수는 1e-9. hedge_legs는 비교하지 않는다.
    """
    a, b = _flat(expected), _flat(actual)
    fx = expected.get("product") == "FXFWD"
    bad = []
    for k in sorted(set(a) | set(b)):
        if k.startswith("hedge_legs."):
            continue
        x, y = a.get(k), b.get(k)
        if isinstance(x, bool) or isinstance(y, bool) or isinstance(x, str) or isinstance(y, str) or x is None or y is None:
            same = x == y
        elif k.endswith(".notional"):
            same = abs(x - y) <= 1e-4 * max(abs(x), abs(y))
        elif k.endswith(".rate"):
            same = abs(x - y) <= 5e-6
        elif k == "fee":
            same = abs(x - y) <= (5e-3 if fx else 1e-7)
        elif k == "upfront_krw":
            same = abs(x - y) <= 1e4
        else:
            same = abs(x - y) <= 1e-9
        if not same:
            bad.append({"field": k, "expected": x, "actual": y})
    return bad


# ---------------------------------------------------------------------------
# 정답 항목 (화면에 보이는 숫자와 같은 정의)
# ---------------------------------------------------------------------------
def answers_from_result(res: dict) -> dict:
    """채점 항목 전체. 과제마다 그중 scored에 적힌 것만 채점한다.

      day1_oku        첫날 이익 (억원)
      acct_oku        회계 손익, E/L만 차감 (억원)
      net_oku         자본비용 반영 손익 (억원)
      be_fee_bp       손익분기 라인피 (연 bp). 통화스왑·IRS·OIS, running일 때
      be_fee_won      손익분기 라인피 (선도환율 마진, 원/달러). FX 선도
      fee_verdict     제안 라인피가 손익분기보다 "부족" | "여유"
      be_upfront_oku  BTB: upfront로만 받는다면 (억원). upfront 방식이면 손익분기 upfront
      be_running_bp   upfront 방식: running으로 대신 받는다면 (연 bp)
      be_hurdle_pct   손익분기 세후 요구수익률 (%). 범위 밖이면 "0% 미만" | "50% 이상"
    """
    r, be, tr = res["base"], res["be"], res["client_no_fee"]
    assert isinstance(r, Run)
    fx = tr.product == "FXFWD"
    out = {"day1_oku": r.day1 / OKU, "acct_oku": r.accounting_net / OKU, "net_oku": r.net / OKU}
    if res["loc"] != "upfront":
        need = res["fee"] + be.extra
        out["be_fee_won" if fx else "be_fee_bp"] = need if fx else need * 1e4
        out["fee_verdict"] = "부족" if be.extra > 0 else "여유"
        if res["hedge_kind"] == "btb" and res.get("zero") is not None:
            out["be_upfront_oku"] = -res["zero"].net / OKU
    else:
        out["be_upfront_oku"] = (res["upfront"] - r.net) / OKU
        if res.get("be_running") is not None and not fx:
            out["be_running_bp"] = res["be_running"].extra * 1e4
    hb = r.breakeven_hurdle()
    out["be_hurdle_pct"] = "0% 미만" if hb is None else ("50% 이상" if hb >= 0.5 else hb * 100)
    return out


def answer_ok(field: str, expected, given) -> bool:
    """제출한 답이 정답과 맞는지. 숫자는 접미어별 허용오차, 문자는 공백을 뺀 일치."""
    if isinstance(expected, str):
        return str(given).replace(" ", "") == expected.replace(" ", "")
    try:
        g = float(str(given).replace(",", "").rstrip("%억원bp "))
    except ValueError:
        return False
    tol = next(t for suf, t in ANSWER_TOL.items() if field.endswith(suf))
    return abs(g - expected) <= tol + 1e-12
