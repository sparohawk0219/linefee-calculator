"""라인피 계산기 대시보드.

    streamlit run app.py

거래 1건(고객)과 헤지(미러 자동 또는 직접 입력)를 넣으면 비용과 손익분기 수수료를 계산한다.
방법론과 근사 사항: docs/METHODOLOGY_v1.md
"""
import hmac
import importlib
import sys
from dataclasses import replace
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

import linefee.curves
import linefee.engine
import linefee.market
import linefee.pricing
import linefee.regcap
import linefee.trade

# 서버 실행 중 엔진 파일이 바뀌면 다시 읽는다. Streamlit은 app.py만 다시 실행하고
# 이미 import한 하위 모듈은 그대로 둘 수 있어, 바뀐 계산 코드가 반영되지 않는 경우가 있었다.
_ENGINE = ["linefee.curves", "linefee.market", "linefee.trade", "linefee.engine", "simm", "linefee.regcap",
           "linefee.pricing"]   # simm은 linefee.regcap이 from simm import ...로 묶으므로 그보다 먼저


@st.cache_resource
def _engine_mtimes() -> dict:
    return {}


def _reload_engine_if_changed():
    seen = _engine_mtimes()
    now = {n: Path(sys.modules[n].__file__).stat().st_mtime for n in _ENGINE}
    if seen and now != seen:
        for n in _ENGINE:   # 의존 순서대로
            importlib.reload(sys.modules[n])
    seen.clear()
    seen.update(now)


_reload_engine_if_changed()

from linefee.market import (LEGACY_SNAPSHOT, SNAPSHOT, fetch, load, load_legacy,  # noqa: E402  (다시 읽은 뒤 이름을 묶는다)
                            snapshot_guide)
from linefee.engine import trade_value_today  # noqa: E402
from linefee.pricing import (Run, annual_pnl, breakeven, fee_unit_pv, market_gap,  # noqa: E402
                             market_hedge, par_client, run)
from linefee.trade import (BA_CVA_RW, CCP_REG_MPOR_DAYS, CSA, BankParams, Counterparty, Leg, Trade,  # noqa: E402
                           build_deal, fee_delta)
from ui import store  # noqa: E402

OKU = 1e8
CCYS = ["KRW", "USD", "JPY"]
UNIT = {"KRW": (1e8, "억원"), "JPY": (1e8, "억엔"), "USD": (1e6, "백만달러")}
CCP_NAME, CCP_RW = "CCP (KRX·LCH)", 0.02   # 원화 IRS·달러 OIS의 헤지 상대: 적격 CCP 고정 (거래 익스포저 위험가중치 2%)
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]   # 범주색 1~4번 (고정 순서)
BTB, LINE, DIRECT = "BTB (헤지도 같은 오프마켓, 라인피 제외)", "딜러 라인 (Par 헤지)", "직접 입력 (BTB 헤지 조건)"
HEDGE_MODES = {BTB: "btb", LINE: "line", DIRECT: "direct"}
IRS_HEDGES = {"CCP": "ccp", "BTB (딜러)": "btb", "딜러 라인 (Par)": "line"}   # 원화 IRS·달러 OIS 헤지 상대


PRODUCTS = {"CCS": "통화스왑", "FXFWD": "FX 선도", "KRWIRS": "원화 IRS", "USDOIS": "달러 OIS"}
# 원화 IRS·달러 OIS 관행: (통화, 연 지급 횟수, 일수 계산, 변동금리). 고정·변동 다리 같은 관행, CCP 청산
IRS_CONV = {"KRWIRS": ("KRW", 4, "ACT/365", "CD 91일"), "USDOIS": ("USD", 1, "ACT/360", "SOFR 후취 복리")}
FLOAT_INDEX = {"USD": "SOFR", "JPY": "TONA", "KRW": "CD 91일"}
IRS_DIRS = ["은행 고정 수취 (고객 고정 지급)", "은행 고정 지급 (고객 고정 수취)"]
PRESETS = ["엔/원 30년 CCS (공공기관 A 중개)", "USD/KRW 5년 CCS (기업)", "USD/KRW 1년 FX 선도 (기업)",
           "원화 IRS 5년 (기업, 고정 지급)", "달러 OIS 3년 (기업, 고정 지급)"]


def fee_loc_label(loc: str, fx: bool) -> str:
    if loc == "hedge":
        return "헤지 선도환율 마진" if fx else "딜러에게 주는 다리에서 차감"
    return "고객 선도환율 마진" if fx else "고객이 내는 다리에 가산"


def money_ko(amount: float, ccy: str) -> str:
    """금액을 사람이 읽는 말로 (예: 2억 달러, 3,000만 달러, 1조 2,000억원). 입력 칸 옆 확인용."""
    name = {"KRW": "원", "USD": "달러", "JPY": "엔"}[ccy]
    v = round(abs(amount))
    if v < 10_000:
        return f"{v:,}{name}"
    parts = []
    for size, unit in ((10**12, "조"), (10**8, "억"), (10**4, "만")):
        n, v = divmod(v, size)
        if n:
            parts.append(f"{n:,}{unit}")
    return " ".join(parts[:2]) + (name if ccy == "KRW" else f" {name}" if ccy == "USD" else name)


def leg_words(leg: Leg) -> str:
    """다리 하나를 한 문장 조각으로: '엔 100억엔 고정 4.0700%' 처럼."""
    amt = f"{leg.ccy} {money_ko(leg.notional, leg.ccy)}"
    if leg.kind == "fixed" and leg.rate != 0:
        return f"{amt} 고정 {leg.rate * 100:.4f}%"
    if leg.kind == "float":
        return f"{amt} {FLOAT_INDEX[leg.ccy]} + {leg.rate * 1e4:.2f}bp"
    return amt


MARKETS = {"최신 (블룸버그 스냅샷)": "latest", "엔/원 검토 기준 (2026-11-11, KRW·JPY만)": "legacy"}

st.set_page_config(page_title="라인피 계산기", layout="wide")

if not SNAPSHOT.exists():   # 스냅샷은 로컬 전용: 트레이스백 대신 안내하고 멈춘다
    st.error("시장 스냅샷이 없어 계산할 수 없습니다.")
    st.code(snapshot_guide(SNAPSHOT), language=None)
    st.stop()
if not LEGACY_SNAPSHOT.exists():   # 회귀 기준 스냅샷이 없으면 그 선택지만 뺀다
    MARKETS.pop("엔/원 검토 기준 (2026-11-11, KRW·JPY만)")


# ---------------------------------------------------------------------------
# 시장 데이터
# ---------------------------------------------------------------------------
@st.cache_resource
def _load_market(source: str, engine_version: tuple, snapshot_mtime: float):
    # engine_version: 엔진 파일 수정 시각. 엔진이 바뀌면 키가 달라져 시장 객체도 새 클래스로 다시 만든다
    # snapshot_mtime: 스냅샷 파일 수정 시각. 앱 밖에서 스냅샷을 새로 받아도 키가 바뀌어 다시 읽는다
    return load_legacy() if source == "legacy" else load()


def get_market(source: str):
    path = LEGACY_SNAPSHOT if source == "legacy" else SNAPSHOT
    mtime = path.stat().st_mtime if path.exists() else 0.0
    return _load_market(source, tuple(sorted(_engine_mtimes().items())), mtime)


get_market.clear = _load_market.clear


# ---------------------------------------------------------------------------
# 예시 거래
# ---------------------------------------------------------------------------
def _set_leg(prefix, ccy, amt, kind="fixed", rate=0.0, spread=0.0, freq=1, dc="ACT/365", fx_end=True):
    ss = st.session_state
    ss[prefix + "ccy"], ss[prefix + "amt"], ss[prefix + "kind"] = ccy, float(amt), kind
    ss[prefix + "rate"], ss[prefix + "spread"] = float(rate), float(spread)
    ss[prefix + "freq"], ss[prefix + "dc"], ss[prefix + "fx_end"] = freq, dc, fx_end


def _set_names(client, dealer=None):
    """예시의 상대방: 마스터 이름 (로컬 마스터에 없으면 화면을 그릴 때 샘플 행을 쓴다)."""
    ss = st.session_state
    ss["cl_name"] = ss["_preset_cl_name"] = client
    if dealer:
        ss["dl_name"] = ss["_preset_dl_name"] = dealer


def load_preset():
    ss = st.session_state
    name = ss["preset"]
    m = get_market(MARKETS[ss["market_src"]])
    ss["upfront_oku"] = 0.0
    if name == "엔/원 30년 CCS (공공기관 A 중개)":
        ss["product"], ss["mat"] = "CCS", 30.0
        _set_leg("cr_", "KRW", 100 * m.spot_quote["JPY"], rate=4.03, dc="ACT/360")
        _set_leg("cp_", "JPY", 100, rate=4.07)
        _set_names("공공기관 A", "딜러 B")
        ss["h_mode"], ss["fee_loc"], ss["fee_bp"] = BTB, "고객 다리 running", 10.0
    elif name == "USD/KRW 5년 CCS (기업)" and "USD" in m.spot:
        ss["product"], ss["mat"] = "CCS", 5.0
        # 시장가 KRW 금리 (KRW 고정 분기 ACT/365 vs SOFR 분기 ACT/360, 현물 호가 기준 명목)
        probe = Trade([Leg("KRW", 1e8 * m.spot_quote["USD"], "receive", rate=0.03, freq=4),
                       Leg("USD", 1e8, "pay", kind="float", freq=4, day_count="ACT/360")], 5.0)
        par = par_client(probe, m).legs[0].rate
        _set_leg("cr_", "KRW", 100 * m.spot_quote["USD"] / 100, rate=round(par * 100, 4), freq=4, dc="ACT/365")
        _set_leg("cp_", "USD", 100, kind="float", spread=0.0, freq=4, dc="ACT/360")
        _set_names("기업 C", "딜러 B")
        ss["h_mode"], ss["fee_loc"], ss["fee_bp"] = LINE, "고객 다리 running", 10.0
    elif name == "USD/KRW 1년 FX 선도 (기업)" and "USD" in m.spot:
        ss["product"], ss["mat"] = "FXFWD", 1.0
        probe = Trade([Leg("KRW", 1e8 * m.spot_quote["USD"], "receive"), Leg("USD", 1e8, "pay")], 1.0, "FXFWD")
        fwd = par_client(probe, m).legs[0].notional / 1e8      # 시장 선도환율 (스왑포인트 기준)
        _set_leg("cr_", "KRW", 100 * fwd / 100, fx_end=True)
        _set_leg("cp_", "USD", 100, fx_end=True)
        _set_names("기업 C", "딜러 D")
        ss["h_mode"], ss["fee_loc"], ss["fee_fx"] = LINE, "고객 선도환율 마진", 1.0
    elif name.startswith(("원화 IRS", "달러 OIS")):
        krw = name.startswith("원화")
        if not krw and "USD" not in m.local:   # 엔/원 검토 기준 스냅샷에는 USD 커브가 없다
            mirror_to_hedge()
            return
        ss["product"], ss["mat"] = ("KRWIRS", 5.0) if krw else ("USDOIS", 3.0)
        ss["irs_amt"], ss["irs_dir"], ss["irs_spread"] = (1000.0 if krw else 100.0), IRS_DIRS[0], 0.0
        _set_names("기업 C")
        ss["fee_loc"], ss["fee_bp"] = "고객 다리 running", 5.0
    if ss.get("product") in IRS_CONV:   # 시장 기준이 바뀌어도 고정금리를 그 시장의 파 금리로
        set_irs_par()
    mirror_to_hedge()


def make_irs(product: str) -> Trade:
    """화면 입력으로 원화 IRS·달러 OIS 거래를 만든다 (원금 교환 없음, 자국 통화 담보 커브)."""
    ss = st.session_state
    ccy, freq, dc, _ = IRS_CONV[product]
    unit = UNIT[ccy][0]
    recv_fixed = ss.get("irs_dir", IRS_DIRS[0]) == IRS_DIRS[0]
    n = ss.get("irs_amt", 0.0) * unit
    fixed = Leg(ccy, n, "receive" if recv_fixed else "pay", "fixed", ss.get("irs_rate", 0.0) / 100,
                freq, dc, False, "local")
    flt = Leg(ccy, n, "pay" if recv_fixed else "receive", "float", ss.get("irs_spread", 0.0) / 1e4,
              freq, dc, False, "local")
    return Trade([fixed, flt] if recv_fixed else [flt, fixed], ss.get("mat", 5.0), "IRS")


def set_irs_par():
    """고정금리를 시장 파 금리로 채운다 (변동 스프레드 반영)."""
    ss = st.session_state
    m = get_market(MARKETS[ss["market_src"]])
    ccy = IRS_CONV[ss["product"]][0]
    if ccy not in m.local or ss.get("irs_amt", 0) <= 0:
        return
    hedge = market_hedge(make_irs(ss["product"]), m)
    ss["irs_rate"] = round(next(l for l in hedge.legs if l.kind == "fixed").rate * 100, 4)


def _leg_from_state(prefix: str, direction: str, product: str) -> Leg:
    """화면 입력(session_state)으로 다리를 만든다. 콜백에서 쓴다 (leg_inputs와 같은 규칙)."""
    ss = st.session_state
    ccy = ss[prefix + "ccy"]
    amt = ss[prefix + "amt"] * UNIT[ccy][0]
    if product == "FXFWD":
        return Leg(ccy, amt, direction, rate=0.0, final_exchange=True)
    kind = ss.get(prefix + "kind", "fixed")
    rate = ss[prefix + "rate"] / 100 if kind == "fixed" else ss[prefix + "spread"] / 1e4
    return Leg(ccy, amt, direction, kind, rate, ss[prefix + "freq"], ss[prefix + "dc"], ss[prefix + "fx_end"])


def set_client_par():
    """고객 거래(라인피 전)를 시장가로: 통화스왑은 은행 수취 다리 금리(스프레드), FX 선도는 KRW 금액."""
    ss = st.session_state
    prod = ss["product"]
    m = get_market(MARKETS[ss["market_src"]])
    tr = Trade([_leg_from_state("cr_", "receive", prod), _leg_from_state("cp_", "pay", prod)], ss["mat"], prod)
    par = par_client(tr, m)
    if prod == "FXFWD":
        k = next(i for i, l in enumerate(par.legs) if l.ccy == "KRW")
        pre = "cr_" if k == 0 else "cp_"
        ss[pre + "amt"] = par.legs[k].notional / UNIT["KRW"][0]
    else:
        leg = par.legs[0]
        if leg.kind == "fixed":
            ss["cr_rate"] = round(leg.rate * 100, 6)
        else:
            ss["cr_spread"] = round(leg.rate * 1e4, 4)


def on_mat_change():
    if st.session_state.get("product") in IRS_CONV:
        set_irs_par()


def fx_quote_unit(trade: Trade) -> int:
    """FX 선도 화면 단위: 엔은 100엔당 원, 그 외 1단위당 원."""
    fx = next((l for l in trade.legs if l.ccy != "KRW"), None)
    return 100 if fx is not None and fx.ccy == "JPY" else 1


def clear_counterparties():
    """상품·통화를 예시에서 바꾸면 예시의 상대방은 더 이상 맞지 않을 수 있다. 비워 두고 거래에 맞게 직접 고르게 한다."""
    for k in ("cl_name", "dl_name", "_preset_cl_name", "_preset_dl_name"):
        st.session_state[k] = None


def on_product_change():
    """상품을 바꾸면 기본값을 맞춘다. FX 선도는 딜러 라인 헤지, IRS·OIS는 CCP 헤지와 파 금리."""
    ss = st.session_state
    clear_counterparties()
    prod = ss["product"]
    if prod == "FXFWD":
        ss["h_mode"] = LINE
    elif prod in IRS_CONV:
        ccy = IRS_CONV[prod][0]
        ss.setdefault("irs_dir", IRS_DIRS[0])
        ss["irs_amt"] = 1000.0 if ccy == "KRW" else 100.0
        ss.setdefault("irs_spread", 0.0)
        set_irs_par()


def mirror_to_hedge():
    """헤지 직접 입력 칸을 고객 거래의 미러로 채운다 (은행 수취 다리 ↔ 지급 다리)."""
    ss = st.session_state
    for src, dst in (("cp_", "hr_"), ("cr_", "hp_")):
        for f in ("ccy", "amt", "kind", "rate", "spread", "freq", "dc", "fx_end"):
            if src + f in ss:
                ss[dst + f] = ss[src + f]


if "initialized" not in st.session_state:
    st.session_state["initialized"] = True
    st.session_state["market_src"] = list(MARKETS)[0]
    st.session_state["preset"] = "엔/원 30년 CCS (공공기관 A 중개)"
    load_preset()
for _k, _v in {"irs_amt": 1000.0, "irs_dir": IRS_DIRS[0],
               "irs_rate": 0.0, "irs_spread": 0.0, "fee_bp": 10.0, "fee_fx": 1.0}.items():
    st.session_state.setdefault(_k, _v)

# 화면에 그리지 않은 위젯의 session_state는 Streamlit이 지운다 (예: IRS·OIS 화면에서는 두 다리 위젯이 없다).
# 통화스왑으로 돌아왔을 때 입력이 사라지지 않게 다리 입력은 따로 보관했다가 없어졌으면 되돌려 놓는다 (L-012).
_KEEP_PREFIXES = ("cr_", "cp_", "hr_", "hp_", "dl_name")
_keep = st.session_state.setdefault("_leg_keep", {})
for _k, _v in _keep.items():
    st.session_state.setdefault(_k, _v)
_keep.update({_k: _v for _k, _v in st.session_state.items() if _k.startswith(_KEEP_PREFIXES)})


# ---------------------------------------------------------------------------
# 입력 위젯
# ---------------------------------------------------------------------------
def leg_inputs(prefix: str, title: str, direction: str, product: str) -> Leg:
    ss = st.session_state
    st.markdown(f"**{title}**")
    ccy = st.selectbox("통화", CCYS, key=prefix + "ccy",
                       on_change=clear_counterparties if prefix in ("cr_", "cp_") else None)
    unit, label = UNIT[ccy]
    amt = st.number_input(f"명목 ({label})", key=prefix + "amt", min_value=0.0, step=10.0, format="%.2f")
    who = "고객" if prefix in ("cr_", "cp_") else "헤지 딜러"
    st.caption(f"= {money_ko(amt * unit, ccy)} ({who + '이 은행에 지급' if direction == 'receive' else '은행이 ' + who + '에게 지급'})")
    if product == "FXFWD":
        return Leg(ccy, amt * unit, direction, rate=0.0, final_exchange=True)
    kinds = ["fixed"] if ccy == "KRW" else ["fixed", "float"]
    if ss.get(prefix + "kind") not in kinds:
        ss[prefix + "kind"] = "fixed"
    kind = st.radio("금리 형태", kinds, key=prefix + "kind", horizontal=True,
                    format_func=lambda k: "고정" if k == "fixed" else ("SOFR" if ccy == "USD" else "TONA") + " 변동")
    if kind == "fixed":
        rate = st.number_input("고정금리 (%)", key=prefix + "rate", step=0.01, format="%.4f") / 100
    else:
        rate = st.number_input("가산 스프레드 (bp)", key=prefix + "spread", step=1.0, format="%.2f") / 1e4
    c1, c2 = st.columns(2)
    freq = c1.selectbox("연 지급 횟수", [1, 2, 4], key=prefix + "freq")
    dc = c2.selectbox("일수 계산", ["ACT/365", "ACT/360"], key=prefix + "dc")
    fx_end = st.checkbox("만기 원금 교환", key=prefix + "fx_end")
    return Leg(ccy, amt * unit, direction, kind, rate, freq, dc, fx_end)


def irs_inputs(product: str) -> Trade:
    ccy, freq, dc, index = IRS_CONV[product]
    st.caption(f"{PRODUCTS[product]}: 고정 vs {index}, 연 {freq}회 지급, {dc}, 원금 교환 없음, "
               f"{ccy} 담보 커브로 평가 (CCP 청산 기준). 만기를 바꾸면 고정금리가 그 만기의 시장 파 금리로 다시 채워진다")
    a, b = st.columns(2)
    irs_amt = a.number_input(f"명목 ({UNIT[ccy][1]})", key="irs_amt", min_value=0.0, step=100.0, format="%.2f")
    a.caption(f"= {money_ko(irs_amt * UNIT[ccy][0], ccy)}")
    irs_dir = b.radio("방향", IRS_DIRS, key="irs_dir")
    b.caption("고객이 고정금리를 " + ("지급한다 (은행이 고정을 받는다)" if irs_dir == IRS_DIRS[0]
                                   else "받는다 (은행이 고정을 낸다)"))
    a.number_input("고정금리 (%)", key="irs_rate", step=0.0001, format="%.4f",
                   help="만기·스프레드를 바꾸면 시장 파 금리로 다시 채운다. 라인피는 아래에서 따로 넣는다")
    b.number_input(f"{index} 가산 스프레드 (bp)", key="irs_spread", step=1.0, format="%.2f", on_change=set_irs_par)
    a.button("시장 파 금리로 채우기", on_click=set_irs_par)
    return make_irs(product)


def cp_pick(key: str, title: str, names: list, master: dict) -> Counterparty:
    """마스터에서 이름으로 상대방을 고른다. 등급·PD·위험가중치는 보여 주지 않는다 (관리자가 마스터에서 관리)."""
    name = st.selectbox(title, names, key=key, index=None, placeholder=f"{title} 선택 (필수)")
    if name is None:
        st.warning(f"{title}: 아직 고르지 않았습니다. 이 거래에 맞게 직접 고른 뒤 계산하세요.")
        return None
    st.caption(f"{title} = {name}. CSA: {master[name]['csa']}. 이 거래의 {title}이(가) 맞는지 확인하세요.")
    flags = {"등급 갱신 필요": "옛 등급 체계(세분 전)라 가운데 등급의 PD로 임시 계산합니다. 거래 전 재평가 등급으로 바꾸세요.",
             "부도 등급": "부도 등급(D) 상대방입니다.",
             "32조 구분 확인 필요": "공공기관 위험가중치 구분(별표3 32조)이 확인되지 않아 보수적으로 20%를 씁니다.",
             "외부등급 없음": "외부 신용등급이 없어 무등급 위험가중치를 씁니다."}
    for tag, msg in flags.items():
        if f"[{tag}]" in name:
            st.warning(f"{title}: {msg}")
    return store.counterparty(master[name])


# ---------------------------------------------------------------------------
# 차트
# ---------------------------------------------------------------------------
def line_chart(df: pd.DataFrame, y_title: str, stacked: bool = False) -> alt.Chart:
    """df: 년, 구분, 값(억원). 십자선 + 툴팁."""
    domain = list(dict.fromkeys(df["구분"]))
    color = alt.Color("구분:N", scale=alt.Scale(domain=domain, range=SERIES[:len(domain)]),
                      legend=alt.Legend(orient="top", title=None))
    base = alt.Chart(df).encode(x=alt.X("년:Q", title="경과 기간 (년)"))
    if stacked:
        marks = base.mark_area(stroke="#fcfcfb", strokeWidth=2).encode(
            y=alt.Y("값:Q", title=y_title, stack="zero"), color=color, order=alt.Order("순서:Q"))
    else:
        marks = base.mark_line(strokeWidth=2).encode(y=alt.Y("값:Q", title=y_title), color=color)
    wide = df.pivot_table(index="년", columns="구분", values="값").reset_index()
    hover = alt.selection_point(fields=["년"], nearest=True, on="pointerover", empty=False)
    rule = alt.Chart(wide).mark_rule(color="#8a8984").encode(
        x="년:Q",
        opacity=alt.condition(hover, alt.value(0.6), alt.value(0)),
        tooltip=[alt.Tooltip("년:Q", format=".2f")] + [alt.Tooltip(f"{c}:Q", format=",.1f") for c in domain],
    ).add_params(hover)
    return (marks + rule).properties(height=300)


def profile_df(times, series: dict) -> pd.DataFrame:
    rows = []
    for order, (name, vals) in enumerate(series.items()):
        rows += [{"년": t, "구분": name, "값": v / OKU, "순서": order} for t, v in zip(times, vals)]
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 화면
# ---------------------------------------------------------------------------
st.title("라인피 계산기")
st.caption("장외파생 1건 + 헤지의 E/L·FVA·MVA·KVA와 손익분기 수수료 (v1, 단독 기준). "
           "방법론: docs/METHODOLOGY_v1.md")

ss = st.session_state
admin = ss.get("role") == "admin"
NEW_ROW = "(새 상대방)"
saved_params = store.load_params()


def _admin_secret():
    """비밀값의 관리자 비밀번호. 비밀값 파일이나 키가 없으면 None (st.secrets 접근이 예외를 낼 수 있다)."""
    try:
        pw = st.secrets["admin_password"]
    except Exception:  # noqa: BLE001  FileNotFoundError, KeyError 등
        return None
    return pw if isinstance(pw, str) and pw else None


def do_login():
    typed, secret = ss.get("admin_pw", ""), _admin_secret()
    ss["admin_pw"] = ""
    if secret is None:
        ss["_login_msg"] = ("warning", "관리자 비밀번호가 설정되지 않았습니다. 관리자 로그인을 쓸 수 없습니다.")
    elif hmac.compare_digest(typed.encode("utf-8"), secret.encode("utf-8")):
        ss["role"] = "admin"
        ss["adm_master"] = {k: dict(v) for k, v in store.load_master()[0].items()}
        ss.pop("_login_msg", None)
    else:
        ss["_login_msg"] = ("error", "비밀번호가 맞지 않습니다.")


def do_logout():
    for k in ("role", "adm_master", "_adm_msg"):
        ss.pop(k, None)
    if ss.get("market_src") != list(MARKETS)[0]:   # 일반 사용자는 기본 시장 기준
        ss["market_src"] = list(MARKETS)[0]
        load_preset()


def on_market_change():
    ss["market_src"] = ss["adm_market_src"]
    load_preset()


with st.sidebar:
    st.header("시장 데이터")
    if admin:
        st.selectbox("기준", list(MARKETS), index=list(MARKETS).index(ss["market_src"]), key="adm_market_src",
                     on_change=on_market_change,
                     help="바꾸면 선택한 예시 거래를 그 시장 기준(현물환율)으로 다시 불러온다")
    m = get_market(MARKETS[ss["market_src"]])
    if admin:
        st.caption(f"{m.source} · 현물 호가 " + ", ".join(f"{c}KRW {v:,.4f}" for c, v in m.spot_quote.items()
                                                        if c != "KRW")
                   + ("" if m.short_end else " · 1년 미만 상품 없음 (1년물부터 부트스트랩)"))
        if m.missing:
            st.warning(f"스냅샷에서 값을 받지 못한 티커 {len(m.missing)}개: {', '.join(m.missing)}")
    else:
        st.caption(f"시장 기준: {ss['market_src']} · 기준일 {m.as_of}")
    # 시장 데이터 새로 받기는 일반 사용자도 한다 (거래 때마다 최신 시장으로 계산). 기준 선택은 관리자만
    if st.button("블룸버그에서 새로 받기", key="fetch",
                 help="블룸버그 단말이 연결된 PC에서만 된다. 받은 뒤에는 계산을 다시 눌러야 새 시장으로 계산된다"):
        with st.spinner("블룸버그 조회 중 (현재값 + 과거 5년)"):
            try:
                fetch()
                get_market.clear()
                st.success(f"저장: {SNAPSHOT.name}")
            except Exception as e:  # 블룸버그 미접속 등
                st.error(f"실패: {e}")

    draft_params = None   # 관리자가 고치는 중인 은행 파라미터 (저장하기 전에는 계산에 쓰이지 않는다)
    if admin:
        sp = saved_params
        st.header("은행 파라미터")
        st.caption("저장해야 모든 사용자의 계산에 쓰입니다 (저장 버튼: 본문의 관리자 영역).")
        cap_ratio = st.number_input("CET1 목표비율 (%)", value=round(sp.capital_ratio * 100, 6), min_value=0.0,
                                    step=0.5, key="adm_capital_ratio")
        hurdle = st.number_input("세후 요구수익률 (%)", value=round(sp.hurdle_after_tax * 100, 6), min_value=0.0,
                                 step=0.5, key="adm_hurdle")
        tax = st.number_input("세율 (%)", value=round(sp.tax_rate * 100, 6), min_value=0.0, max_value=99.9, step=0.5,
                              key="adm_tax",
                              help="0% 이상 100% 미만 (요구수익률을 세전으로 환산할 때 1 - 세율로 나눈다)")
        ftp = st.number_input("USD 담보 조달 FTP 스프레드 (bp)", value=round(sp.ftp_spread * 1e4, 6), step=1.0,
                              key="adm_ftp", help="FTP - CSA 담보 이자(SOFR). USD 현금 담보 VM의 FVA에 쓴다")
        ftp_krw = st.number_input("KRW 담보 조달 FTP 스프레드 (bp)", value=round(sp.ftp_spread_krw * 1e4, 6),
                                  step=1.0, key="adm_ftp_krw",
                                  help="KRW FTP - KRW 현금 담보 이자. CCP 원화 IRS의 VM 조달(FVA)에 쓴다 (확인 필요)")
        kva_sp = st.number_input("KVA 조달 스프레드 alpha (bp)", value=round(sp.kva_funding_spread * 1e4, 6),
                                 step=1.0, key="adm_kva_sp",
                                 help="KVA 조달금리 r(t) = CD(원화 IRS) 커브 선도 + alpha")
        kva_curve = st.radio("KVA 조달금리 기준 커브", ["원화 IRS (CD)", "원화 CCS (USD 담보, 비교용)"],
                             index=0 if sp.kva_funding == "krw_irs" else 1, horizontal=True, key="adm_kva_curve",
                             help="자본이 대체하는 KRW 조달의 기준금리. 원화 IRS(CD)가 확정 방식")
        fba = st.checkbox("받은 담보 조달이익(FBA) 인정", value=sp.recognise_fba, key="adm_fba")
        marginal = st.number_input("BA-CVA 한계계수", value=sp.ba_cva_marginal_factor, min_value=0.0, step=0.05,
                                   key="adm_marginal",
                                   help="은행 CVA 포트폴리오 대비 증분. 완전 분산이면 0.5(rho)")
        im_spread = st.number_input("낸 IM 조달 스프레드 (bp)", value=round(sp.im_funding_spread * 1e4, 6), step=1.0,
                                    key="adm_im_spread")
        st.header("시뮬레이션")
        n_opts = [2000, 5000, 10000, 20000]
        n_paths = st.select_slider("경로 수", n_opts, value=sp.n_paths if sp.n_paths in n_opts else 5000,
                                   key="adm_n_paths")
        vol_scale = st.number_input("금리 변동성 배수", value=sp.rate_vol_scale, min_value=0.0, step=0.1,
                                    key="adm_vol_scale")
        kappa = st.number_input("금리 평균회귀 (연)", value=sp.rate_mean_reversion, step=0.01, key="adm_kappa")
        if kappa < 0:
            st.warning("음수 평균회귀는 금리 요인이 시간이 갈수록 발산하는 모형입니다. 계산은 그대로 하지만 비현실적인 값일 수 있습니다.")
        draft_params = replace(
            sp, capital_ratio=cap_ratio / 100, hurdle_after_tax=hurdle / 100, tax_rate=tax / 100,
            ftp_spread=ftp / 1e4, ftp_spread_krw=ftp_krw / 1e4, kva_funding_spread=kva_sp / 1e4,
            kva_funding="krw_irs" if kva_curve.startswith("원화 IRS") else "krw_ccs", recognise_fba=fba,
            ba_cva_marginal_factor=marginal, im_funding_spread=im_spread / 1e4, rate_vol_scale=vol_scale,
            rate_mean_reversion=kappa, n_paths=n_paths)

    st.divider()
    if admin:
        st.caption("관리자로 로그인했습니다.")
        st.button("로그아웃", key="adm_logout", on_click=do_logout)
    else:
        with st.expander("관리자 로그인"):
            st.text_input("관리자 비밀번호", type="password", key="admin_pw")
            st.button("관리자 로그인", on_click=do_login)
            if "_login_msg" in ss:
                kind, text = ss["_login_msg"]
                (st.error if kind == "error" else st.warning)(text)


# ---------------------------------------------------------------------------
# 관리자: 거래상대방 마스터 편집과 저장
# ---------------------------------------------------------------------------
def _blank_row() -> dict:
    return {"name": "", "grade": store.GRADES[0], "pd_pct": 0.0, "csa": store.CSA_KINDS[0], "dealer_line": "N",
            "credit_rw_pct": 100.0, "ba_cva_sector": list(BA_CVA_RW)[0]}


def on_pick():
    pick = ss["adm_m_pick"]
    row = ss["adm_master"].get(pick) if pick != NEW_ROW else None
    row = row or _blank_row()
    ss["adm_m_name"], ss["adm_m_grade"], ss["adm_m_csa"] = row["name"], row["grade"], row["csa"]
    ss["adm_m_pd"], ss["adm_m_rw"] = float(row["pd_pct"]), float(row["credit_rw_pct"])
    ss["adm_m_sector"], ss["adm_m_dealer"] = row["ba_cva_sector"], store.is_dealer(row)


def on_apply():
    row = {"name": ss["adm_m_name"].strip(), "grade": ss["adm_m_grade"], "pd_pct": ss["adm_m_pd"],
           "csa": ss["adm_m_csa"], "dealer_line": "Y" if ss["adm_m_dealer"] else "N",
           "credit_rw_pct": ss["adm_m_rw"], "ba_cva_sector": ss["adm_m_sector"]}
    why = store.check_row(row)
    if why:
        ss["_adm_msg"] = ("error", why)
        return
    prev = ss["adm_master"].get(row["name"], {})
    ss["adm_master"][row["name"]] = {**prev, **row}   # 화면에 없는 열(vm_ccy 등)은 그대로 둔다
    ss["_adm_msg"] = ("success", f"{row['name']}을(를) 마스터 초안에 반영했습니다. 저장해야 계산에 쓰입니다.")


def on_delete():
    pick = ss["adm_m_pick"]
    if pick != NEW_ROW and pick in ss["adm_master"]:
        del ss["adm_master"][pick]
        ss["adm_m_pick"] = NEW_ROW
        on_pick()
        ss["_adm_msg"] = ("success", f"{pick}을(를) 마스터 초안에서 지웠습니다. 저장해야 계산에 쓰입니다.")


def on_revert():
    ss["adm_master"] = {k: dict(v) for k, v in store.load_master()[0].items()}
    ss["adm_m_pick"] = NEW_ROW
    on_pick()
    ss["_adm_msg"] = ("success", "저장된 마스터로 되돌렸습니다.")


if admin:
    ss.setdefault("adm_master", {k: dict(v) for k, v in store.load_master()[0].items()})
    with st.expander("관리자: 거래상대방 마스터와 저장", expanded=True):
        draft = ss["adm_master"]
        if "adm_m_name" not in ss:   # 입력 칸의 처음 값 (새 상대방)
            ss["adm_m_pick"] = NEW_ROW
            on_pick()
        st.caption("저장한 마스터가 없으면 가명 샘플 마스터를 씁니다. 여기서 고친 것은 `저장`을 눌러야 계산에 쓰입니다 "
                   "(사이드바의 은행 파라미터·시뮬레이션도 함께 저장). LGD는 60% 고정.")
        st.dataframe(pd.DataFrame([{"이름": r["name"], "등급": r["grade"], "연 PD (%)": float(r["pd_pct"]),
                                    "CSA": r["csa"], "딜러 라인": "예" if store.is_dealer(r) else "아니오",
                                    "신용 위험가중치 (%)": float(r["credit_rw_pct"]),
                                    "BA-CVA 업종": r["ba_cva_sector"]} for r in draft.values()]),
                     hide_index=True, width="stretch")
        if ss.get("adm_m_pick") not in [NEW_ROW, *draft]:
            ss["adm_m_pick"] = NEW_ROW
        st.selectbox("수정·삭제할 상대방", [NEW_ROW, *draft], key="adm_m_pick", on_change=on_pick,
                     help="기존 상대방을 고르면 아래 칸에 값이 채워집니다. 같은 이름으로 `추가·수정`하면 덮어씁니다")
        c1, c2, c3 = st.columns(3)
        c1.text_input("이름", key="adm_m_name")
        c2.selectbox("등급", store.GRADES, key="adm_m_grade")
        c3.selectbox("CSA", store.CSA_KINDS, key="adm_m_csa")
        c1.number_input("연 PD (%)", key="adm_m_pd", min_value=0.0, max_value=100.0, step=0.05, format="%.2f")
        c2.number_input("신용 위험가중치 (%)", key="adm_m_rw", min_value=0.0, step=10.0,
                        help="표준방법. 예: 공공기관은 결손보전 기관 0%(별표3 32.가), 그 외 20~50%(32.나·다), "
                             "은행·동등 증권사 A등급 30%·BBB 50%, 기업 무등급 100%")
        c3.selectbox("BA-CVA 업종", list(BA_CVA_RW), key="adm_m_sector")
        st.checkbox("딜러 라인 (헤지 딜러로 쓴다)", key="adm_m_dealer")
        b1, b2, b3, b4 = st.columns(4)
        b1.button("추가·수정", key="adm_m_apply", on_click=on_apply)
        b2.button("삭제", key="adm_m_delete", on_click=on_delete)
        b3.button("변경 취소", key="adm_m_revert", on_click=on_revert)
        save_clicked = b4.button("저장", key="adm_save", type="primary")
        if "_adm_msg" in ss:
            kind, text = ss.pop("_adm_msg")
            (st.error if kind == "error" else st.success)(text)
        if save_clicked:
            errs = draft_params.problems()
            if not any(not store.is_dealer(r) for r in draft.values()):
                errs.append("마스터에 고객(딜러 라인이 아닌 행)이 하나 이상 있어야 함")
            if errs:
                st.error("저장하지 않았습니다: " + " / ".join(errs))
            else:
                try:
                    store.save_params(draft_params)
                    store.save_master(draft)
                    st.success("저장했습니다. 지금부터 모든 사용자의 계산에 쓰입니다.")
                except OSError as e:
                    st.error(f"저장하지 못했습니다: {e}")

# 계산에 쓰는 값은 저장된 것만 (저장하지 않은 관리자 변경은 쓰지 않는다). 저장 직후에도 맞게 여기서 다시 읽는다
params = store.load_params()
master, bad_rows, _from_file = store.load_master()
_sample = None
for _k in ("cl_name", "dl_name"):   # 로컬 마스터에 예시의 상대방 이름이 없으면 샘플 행을 쓴다
    _n = ss.get(_k)
    if _n and _n not in master and ss.get("_preset_" + _k) == _n:
        _sample = _sample or store.sample_master()
        if _n in _sample:
            master[_n] = _sample[_n]
if bad_rows:
    st.warning("거래상대방 마스터에서 읽지 못한 행이 있습니다: " + "; ".join(bad_rows))

st.selectbox("예시 불러오기", PRESETS, key="preset", on_change=load_preset)

st.subheader("1. 고객 거래 (은행 기준)")
c1, c2 = st.columns([1, 3])
product = c1.radio("상품", list(PRODUCTS), key="product", format_func=PRODUCTS.get, on_change=on_product_change)
mat = c1.number_input("만기 (년)", key="mat", min_value=0.25, max_value=30.0, step=0.25, on_change=on_mat_change,
                      help="시작일(T+2, 원화 IRS T+1) + 만기. 30년 초과는 커브 자료가 없어 막는다")
is_irs = product in IRS_CONV
with c2:
    if is_irs:
        client_trade = irs_inputs(product)
    else:
        a, b = st.columns(2)
        with a:
            leg_r = leg_inputs("cr_", "은행 수취 다리 (고객이 지급)", "receive", product)
        with b:
            leg_p = leg_inputs("cp_", "은행 지급 다리 (고객이 수취)", "pay", product)
        client_trade = Trade([leg_r, leg_p], mat, product)
if product == "CCS" and leg_r.ccy != leg_p.ccy and all(l.ccy in m.spot for l in (leg_r, leg_p)) \
        and leg_r.notional > 0 and leg_p.notional > 0:
    implied = (leg_r.notional * m.spot_quote[leg_r.ccy]) / (leg_p.notional * m.spot_quote[leg_p.ccy]) - 1
    if abs(implied) > 0.005:
        st.warning(f"두 다리 명목의 원화 환산액이 {implied:+.1%} 다릅니다 (현물환율 기준). "
                   "거래일 원금 교환 차액은 현물 결제로 보고 계산에서 제외합니다.")

st.subheader("2. 상대방")
cl_names = [n for n, r in master.items() if not store.is_dealer(r)]
if not cl_names:
    st.error("거래상대방 마스터에 고객이 없습니다. 관리자에게 마스터 등록을 요청하세요.")
    st.stop()
if ss.get("cl_name") not in cl_names:
    ss["cl_name"] = None
a, b = st.columns(2)
with a:
    client = cp_pick("cl_name", "고객", cl_names, master)
with b:
    irs_hedge = st.radio("헤지 상대", list(IRS_HEDGES), key="irs_hedge", horizontal=True,
                         help="CCP: 청산 Par 헤지. BTB: 딜러와 고객 거래와 같은 조건(비표준 일정·오프마켓 등 청산 불가 거래). "
                              "딜러 라인: 딜러와 양자 Par 헤지") if is_irs else None
    if is_irs and IRS_HEDGES[irs_hedge] == "ccp":
        st.markdown("**헤지: CCP**")
        st.caption(f"{CCP_NAME}. VM {IRS_CONV[product][0]} 현금 일일 정산, IM 은행 일방 납부(5영업일 99% VaR 근사), "
                   "SA-CCR MPOR 10영업일(별표3 296.사)")
        dealer = Counterparty(CCP_NAME, CCP_RW, 0.0, 0.0, 0.6,
                              CSA(vm=True, im=False, reg_mpor_days=CCP_REG_MPOR_DAYS, ccy=IRS_CONV[product][0], ccp=True))
    else:
        dl_names = [n for n, r in master.items() if store.is_dealer(r)]
        if not dl_names:
            st.error("거래상대방 마스터에 헤지 딜러(딜러 라인)가 없습니다. 관리자에게 마스터 등록을 요청하세요.")
            st.stop()
        if ss.get("dl_name") not in dl_names:
            ss["dl_name"] = None
        dealer = cp_pick("dl_name", "헤지 딜러", dl_names, master)
        if dealer is not None and dealer.csa.vm:   # VM 담보 통화는 마스터(ISDA CSA 적격담보) 기준
            st.caption(f"VM 담보 통화: {dealer.csa.ccy} 현금 (FVA 조달 스프레드에 쓴다)")

st.subheader("3. 헤지와 수수료")
if is_irs:
    hedge_kind = IRS_HEDGES[irs_hedge]
    st.caption({"ccp": "헤지는 CCP 청산 Par 거래. 계산 시 미러 거래의 고정금리를 조정해 거래일 가치 0으로 만든다.",
                "btb": "헤지는 고객 거래와 같은 조건(라인피 제외)의 딜러 양자 거래. 넘어온 오프마켓만큼 거래일에 VM 지급.",
                "line": "헤지는 딜러와 양자 Par 거래. 계산 시 미러 거래의 고정금리를 조정해 거래일 가치 0으로 만든다."}
               [hedge_kind])
else:
    h_mode = st.radio("헤지 거래", list(HEDGE_MODES), key="h_mode", horizontal=True,
                      on_change=lambda: mirror_to_hedge() if st.session_state["h_mode"] == DIRECT else None,
                      help="BTB: 헤지가 고객 거래와 같은 조건(넘어온 오프마켓 포함, 라인피 제외). 오프마켓만큼 거래일에 VM 지급. "
                           "딜러 라인: 딜러와 Par(거래일 가치 0)로 포지션만 막음. 직접 입력: BTB 헤지의 실제 조건")
    hedge_kind = HEDGE_MODES[h_mode]
is_fx = product == "FXFWD"
hedge_trade = None
if hedge_kind == "direct":
    st.caption("헤지 딜러와의 실제 조건. 고객 거래와 다른 금리·명목의 차이가 곧 라인피가 된다.")
    st.button("고객 거래의 미러로 다시 채우기", on_click=mirror_to_hedge)
    a, b = st.columns(2)
    with a:
        h_r = leg_inputs("hr_", "은행 수취 다리 (딜러가 지급)", "receive", product)
    with b:
        h_p = leg_inputs("hp_", "은행 지급 다리 (딜러가 수취)", "pay", product)
    hedge_trade = Trade([h_r, h_p], mat, product)
elif hedge_kind == "line":
    st.caption("헤지는 계산 시 은행 지급 다리 금리(FX 선도는 KRW 금액)를 조정해 거래일 가치 0으로 만든다. "
               "고객 거래는 라인피만큼만 오프마켓이라는 전제다.")

RUN_C, RUN_H = ("고객 선도환율 마진", "헤지 선도환율 마진") if is_fx else ("고객 다리 running", "헤지 다리 running")
fee_opts = {"btb": [RUN_C, RUN_H, "upfront"], "line": [RUN_C], "ccp": [RUN_C],
            "direct": [RUN_C, RUN_H]}[hedge_kind]
c1, c2, c3 = st.columns(3)
if st.session_state.get("fee_loc") not in fee_opts:
    st.session_state["fee_loc"] = fee_opts[0]
fee_loc = c1.radio("손익분기 조정 다리" if hedge_kind == "direct" else "라인피 받는 방법", fee_opts, key="fee_loc",
                   help="고객 다리: 고객 조건에 가산(무담보 익스포저 증가). 헤지 다리: 딜러 조건에서 차감(낼 VM 감소)")
fee_in = 0.0
if hedge_kind != "direct" and fee_loc != "upfront":
    if is_fx:
        qu_in = fx_quote_unit(client_trade)
        fee_in = c2.number_input(f"라인피 (선도환율 마진, 원/{'100엔' if qu_in == 100 else '외화 1단위'})",
                                 key="fee_fx", step=0.1, format="%.2f") / qu_in
    else:
        fee_in = c2.number_input("라인피 (연 bp, 해당 다리 명목 기준)", key="fee_bp", step=1.0)
upfront = 0.0
if hedge_kind in ("btb", "direct"):
    upfront = c3.number_input("upfront 수취 (억원, 음수 = 지급)", key="upfront_oku", step=1.0,
                              help="BTB에서 거래일에 현금으로 주고받는 수수료")

loc_key = {RUN_C: "client", RUN_H: "hedge", "upfront": "upfront"}[fee_loc]


def validate() -> list[str]:
    errs = params.problems()
    if client is None:
        errs.append("고객을 고르지 않았습니다. '2. 상대방'에서 거래의 고객을 고르세요")
    if dealer is None:
        errs.append("헤지 딜러를 고르지 않았습니다. '2. 상대방'에서 거래의 헤지 딜러를 고르세요")
    trades = [client_trade] + ([hedge_trade] if hedge_trade else [])
    for tr in trades:
        if tr.product != "IRS" and tr.legs[0].ccy == tr.legs[1].ccy:
            errs.append("통화스왑·FX 선도는 두 다리 통화가 달라야 함")
        if any(l.notional <= 0 for l in tr.legs):
            errs.append("명목이 0")
        if any(l.ccy not in (m.local if l.discount == "local" else m.disc) for l in tr.legs):
            errs.append(f"선택한 시장 데이터({m.source})에 없는 통화·커브")
        if tr.product == "FXFWD" and "KRW" not in tr.currencies:
            errs.append("FX 선도는 KRW 다리가 있어야 함")
    return sorted(set(errs))


def size_warnings(trades: list, fee_bp_in: float) -> list[str]:
    """이상한 크기의 입력(단위를 틀렸을 가능성)에 대한 경고 문장. 문턱은 정상 입력이 걸리지 않게 넉넉히 잡았다."""
    out = []
    big = {"KRW": 30_000e8, "USD": 2_000e6, "JPY": 3_000e8}   # 원화 3조, 2십억 달러, 3천억 엔
    for tr in trades:
        for l in tr.legs:
            if l.notional > big[l.ccy]:
                out.append(f"{l.ccy} 명목이 {money_ko(l.notional, l.ccy)}으로 매우 큽니다. "
                           f"칸의 단위({UNIT[l.ccy][1]})가 맞는지 확인하고, 아니면 값을 고치세요.")
            if tr.product != "FXFWD" and l.kind == "fixed" and l.rate != 0 and not 0.0005 <= l.rate <= 0.15:
                out.append(f"{l.ccy} 고정금리가 {l.rate * 100:.4f}%입니다. 금리 칸은 % 단위입니다 "
                           "(4.03% = 4.03). 소수(0.0403)나 bp로 넣지 않았는지 확인하세요.")
    if fee_bp_in > 100:
        out.append(f"라인피가 연 {fee_bp_in:,.0f}bp ({fee_bp_in / 100:.1f}%)입니다. 라인피 칸은 bp 단위입니다 "
                   "(0.1% = 10). 맞는 값이면 그대로 계산해도 됩니다.")
    return list(dict.fromkeys(out))


# 시장가 대비 차이 (라인피 전). 딜러 라인·CCP는 고객 거래가 라인피만큼만 오프마켓이라는 전제
QU = fx_quote_unit(client_trade) if is_fx else 1
GAP_UNIT = ("원/100엔" if QU == 100 else "원/외화 1단위") if is_fx else "bp"
for _w in size_warnings([client_trade] + ([hedge_trade] if hedge_trade else []),
                        fee_in if (hedge_kind != "direct" and not is_fx and fee_loc != "upfront") else 0.0):
    st.warning(_w)
if not validate():
    gap = market_gap(client_trade, m) * (QU if is_fx else 1e4)
    gap_txt_now = f"{gap:+,.2f}{'원' if is_fx else 'bp'}"
    if hedge_kind in ("line", "ccp"):
        tol = 0.05 if is_fx else 0.1
        if abs(gap) > tol:
            st.warning(f"라인피를 넣기 전 고객 조건이 시장가와 {gap_txt_now} 다릅니다 (양수 = 은행 유리, {GAP_UNIT}). "
                       "이 차이도 첫날 이익(라인피)에 더해집니다. 의도한 게 아니면 시장가로 맞추세요"
                       + ("" if is_irs else ". 넘어온 오프마켓이 있는 거래라면 BTB를 선택하세요") + ".")
            st.button("고객 조건을 시장가로", on_click=set_irs_par if is_irs else set_client_par)
        else:
            st.caption(f"고객 조건(라인피 전)은 시장가입니다 (차이 {gap_txt_now}).")
    elif hedge_kind == "btb":
        st.caption(f"넘어온 오프마켓 (라인피 전, 은행 기준): {trade_value_today(client_trade, m) / OKU:+,.1f}억 "
                   f"= 고객 다리 {gap_txt_now}")


def input_signature() -> str:
    """결과가 지금 입력으로 계산한 것인지 확인하는 서명."""
    snap = SNAPSHOT.stat().st_mtime if SNAPSHOT.exists() else 0.0   # 블룸버그에서 새로 받으면 바뀐다
    return repr((st.session_state["market_src"], m.as_of, snap, client_trade, hedge_kind, hedge_trade, client, dealer,
                 loc_key, fee_in, upfront, params))


st.caption("계산할 상대방: 고객 " + (client.name if client else "(고르지 않음)") + ", 헤지 "
           + (dealer.name if dealer else "(고르지 않음)") + ". 거래와 맞는지 확인하고 계산하세요.")
if st.button("계산", type="primary"):
    errs = validate()
    if errs:
        st.error(" / ".join(errs))
    else:
        sig = input_signature()
        hedge_used = market_hedge(client_trade, m) if hedge_kind in ("line", "ccp") else hedge_trade
        deal = build_deal(client_trade, client, dealer, hedge=hedge_used, fee_location=loc_key,
                          fee=fee_in, upfront_krw=upfront * OKU)
        with st.spinner("시뮬레이션 중"):
            base = run(deal, m, params)
            be = breakeven(deal, m, params, base=base)
            # BTB에서 upfront만으로 받을 때의 손익분기: running 라인피 없는 같은 구조의 비용
            zero = None
            if hedge_kind == "btb" and loc_key != "upfront":
                zero = run(build_deal(client_trade, client, dealer, fee_location="upfront"), m, params)
            # upfront 방식에서 running으로 대신 받는다면: upfront 0, 고객 다리 라인피 0에서 손익분기
            be_running = None
            if loc_key == "upfront":
                be_running = breakeven(build_deal(client_trade, client, dealer, hedge=hedge_used,
                                                  fee_location="client", fee=0.0), m, params)
        st.session_state["result"] = dict(deal=deal, base=base, be=be, zero=zero, be_running=be_running,
                                          hedge_kind=hedge_kind, loc=loc_key, fee=fee_delta(client_trade, fee_in),
                                          upfront=upfront * OKU, unit_pv=fee_unit_pv(deal, m), is_fx=is_fx,
                                          qu=QU, client_no_fee=client_trade, params=params, sig=sig, product_key=product)

res = st.session_state.get("result")
if res and (not isinstance(res["base"], Run) or "sig" not in res):
    # 서버 실행 중 엔진·화면 코드가 바뀌면 이전 형식의 결과가 남는다. 버리고 다시 계산하게 한다
    del st.session_state["result"]
    res = None
    st.info("계산 코드가 바뀌어 이전 결과를 지웠습니다. '계산'을 다시 눌러 주세요.")
if res:
    r, be, deal, fx_mode, rp, qu = res["base"], res["be"], res["deal"], res["is_fx"], res["params"], res["qu"]
    # 화면 단위: 통화스왑·IRS는 연 bp(조정 다리 명목 기준), FX 선도는 원/외화 1단위(엔은 100엔당)
    UL = ("원/100엔" if qu == 100 else "원/외화 1단위") if fx_mode else "연 bp"
    conv = (lambda krw: krw / res["unit_pv"] * qu) if fx_mode else (lambda krw: krw / res["unit_pv"] * 1e4)
    fmt = (lambda d: f"{d * qu:,.2f}원") if fx_mode else (lambda d: f"{d * 1e4:,.2f}bp")
    st.divider()
    st.subheader("4. 결과")
    if res["sig"] != input_signature():
        st.warning("계산 후 입력이 바뀌었습니다. 아래 결과는 이전 입력 기준입니다. '계산'을 다시 누르세요.")
    hb = r.breakeven_hurdle()
    # 입력 요약: 이 결과를 계산한 입력 (지금 화면의 입력이 아니라 계산 때 저장한 것)
    ct = res["client_no_fee"]
    leg_txt = " / ".join(
        f"{'은행 수취' if l.direction == 'receive' else '은행 지급'} {l.notional / UNIT[l.ccy][0]:,.2f}{UNIT[l.ccy][1]} "
        f"({leg_words(l)})" for l in ct.legs)
    if res["loc"] == "upfront":
        fee_txt = f"upfront {res['upfront'] / OKU:,.2f}억원"
    else:
        fee_num = res["fee"] * (1 if fx_mode else 1e4) * (100 if qu == 100 else 1)
        fee_txt = f"{fee_num:,.2f}{'원/100엔' if qu == 100 else '원/외화 1단위'}" if fx_mode else f"연 {fee_num:,.2f}bp"
        fee_txt += f" ({fee_loc_label(res['loc'], fx_mode)})"
        if res["upfront"]:
            fee_txt += f", upfront {res['upfront'] / OKU:,.2f}억원"
    hk = {"btb": "BTB", "line": "딜러 라인 Par", "ccp": "CCP", "direct": "직접 입력"}[res["hedge_kind"]]
    st.markdown(
        f"**입력 요약** (이 결과를 계산한 입력)  \n"
        f"상품 {PRODUCTS[res.get('product_key', ct.product)]}, 만기 {ct.maturity:g}년  \n"
        f"{leg_txt}  \n"
        f"고객 {deal.client.name}, 헤지 {deal.dealer.name} ({hk}), 라인피 {fee_txt}")
    be_line = ("0% 미만 (어떤 요구수익률에서도 손실)" if hb is None
               else (f"{hb:.1%}" if hb < 0.5 else "50% 이상 (찾는 범위 0~50% 밖이라 숫자가 아니라 '50% 이상'으로 읽는다. 50%까지 올려도 이익이 남는다)"))
    if res["loc"] != "upfront" and res["hedge_kind"] != "direct":
        fee_ask = (f"- 손익분기 라인피: **{fmt(res['fee'] + be.extra)}** (제안 {fmt(res['fee'])} → "
                   f"{'부족' if be.extra > 0 else '충분 (여유)'} {fmt(abs(be.extra))})")
    else:
        fee_ask = None
    st.markdown("**한눈에** (묻는 숫자)  \n"
                f"- 자본비용 반영 손익: **{r.net / OKU:+,.2f}억원** ({'이익' if r.net >= 0 else '손실'})  \n"
                f"- 첫날 이익: {r.day1 / OKU:,.2f}억원 (아래 단계별 표의 첫 줄과 같은 값)  \n"
                + (fee_ask + "  \n" if fee_ask else "")
                + f"- 손익분기 세후 요구수익률: {be_line}")
    if res["hedge_kind"] in ("line", "ccp"):
        ht = deal.hedge_trade
        terms = " / ".join(
            f"{'수취' if l.direction == 'receive' else '지급'} {l.ccy} "
            + (f"{l.notional / UNIT[l.ccy][0]:,.2f}{UNIT[l.ccy][1]}" if ht.product == "FXFWD"
               else (f"{l.rate * 100:.4f}%" if l.kind == "fixed" else f"{FLOAT_INDEX[l.ccy]} + {l.rate * 1e4:.2f}bp"))
            for l in ht.legs)
        st.caption(f"{'CCP' if res['hedge_kind'] == 'ccp' else '딜러 라인'} Par 헤지 조건 (거래일 가치 0): {terms}")
    k = st.columns(4)
    k[0].metric("첫날 이익", f"{r.day1 / OKU:,.2f}억", f"고객 {r.v0_client / OKU:,.2f} / 헤지 {r.v0_hedge / OKU:,.2f}",
                delta_color="off")
    k[1].metric("회계 손익 (E/L만 차감)", f"{r.accounting_net / OKU:,.2f}억", f"{conv(r.accounting_net):+.2f} {UL}")
    k[2].metric("자본비용 반영 손익", f"{r.net / OKU:,.2f}억", f"{conv(r.net):+.2f} {UL}",
                help=f"몬테카를로 표준오차 ±{r.net_se / OKU:,.2f}억 (±{abs(conv(r.net_se)):.2f} {UL}). "
                     "대조 난수, 조달이익 인정 시 FVA는 해석적")
    k[3].metric("손익분기 세후 요구수익률 (자본비용 반영)",
                "0% 미만 (전 구간 손실)" if hb is None else (f"{hb:.1%}" if hb < 0.5 else "50% 이상 (전 구간 이익)"),
                f"입력 {rp.hurdle_after_tax:.1%}", delta_color="off")
    st.caption(f"몬테카를로 표준오차: 자본비용 반영 손익 ±{r.net_se / OKU:,.2f}억 (±{abs(conv(r.net_se)):.2f} {UL}), "
               f"경로 {rp.n_paths:,}개 (대조 난수)")
    if hb is not None and hb >= 0.5:
        worst, h_at = r.worst_net()
        st.caption(f"요구수익률이 높아지면 KVA의 할인(e^-ht)이 커져 KVA가 다시 줄어든다. 0~50% 구간 최저 손익은 "
                   f"{worst / OKU:,.2f}억 (세후 {h_at:.1%}).")

    wf = [("첫날 이익", r.day1, "합계"), ("(-) E/L", -r.el, "차감"),
          ("= 회계 손익 (E/L만 차감)", r.accounting_net, "합계")]
    wf += [(f"(-) {n}", -v, "차감") for n, v in r.costs.items() if n.startswith(("FVA", "MVA"))]
    wf += [("= 조달비용 반영 손익", r.funded_net, "합계")]
    wf += [(f"(-) {n}", -v, "차감") for n, v in r.costs.items() if n.startswith("KVA")]
    wf += [("= 자본비용 반영 손익", r.net, "합계")]
    with st.expander("손익 단계별 내역 (현재가치)", expanded=True):
        wdf = pd.DataFrame([{"항목": n, "억원": v / OKU, UL: conv(v)} for n, v, _ in wf])
        bold = {i for i, (_, _, kind) in enumerate(wf) if kind == "합계"}
        st.dataframe(
            wdf.style.format({"억원": "{:+,.2f}", UL: "{:+,.2f}"})
               .apply(lambda row: ["font-weight: 700" if row.name in bold else ""] * len(row), axis=1),
            hide_index=True, width="stretch")
        st.caption("회계 손익: 첫날 평가이익에서 예상손실(E/L)만 차감한 내부 위험조정영업수익 기준. "
                   "FVA·MVA는 이후 기간 손익으로 실제 발생하는 조달비용(현재가치). "
                   "KVA는 자본비용으로, 성과평가에는 잡히지 않는 비용.")

    st.markdown(f"**손익분기 라인피** (세후 요구수익률 {rp.hurdle_after_tax:.1%}, 자본비용 반영 손익 = 0, "
                f"표준오차 ±{fmt(be.extra_se)})")
    if not be.converged:
        st.warning(f"손익분기 반복이 허용오차(0.01억) 안으로 수렴하지 않았습니다. 마지막 순마진 "
                   f"{be.runs[-1][1].net / OKU:+,.3f}억. 아래 '손익분기 반복 기록'을 확인하세요.")
    q = st.columns(3)
    gap_txt = lambda extra: f"{fmt(abs(extra))} {'부족' if extra > 0 else '여유'}"
    gap_color = "inverse" if be.extra > 0 else "normal"
    where = "고객" if deal.solve_on == "client" else "헤지"

    def fwd_rate(tr: Trade) -> float:
        krw = next(l for l in tr.legs if l.ccy == "KRW")
        fx = next(l for l in tr.legs if l.ccy != "KRW")
        return krw.notional / fx.notional

    if res["hedge_kind"] in ("btb", "line", "ccp") and res["loc"] != "upfront":
        need = res["fee"] + be.extra
        q[0].metric(f"라인피 ({fee_loc_label(res['loc'], fx_mode)})", fmt(need),
                    f"제안 {fmt(res['fee'])} → {gap_txt(be.extra)}", delta_color=gap_color)
        if fx_mode:
            base_k = fwd_rate(res["client_no_fee"])
            sgn = 1 if next(l for l in deal.client_trade.legs if l.ccy == "KRW").direction == "receive" else -1
            q[1].metric(f"손익분기 고객 선도환율 ({UL})", f"{(base_k + sgn * need) * qu:,.2f}",
                        f"마진 전 {base_k * qu:,.2f}", delta_color="off")
        elif res["hedge_kind"] == "btb":
            need_up = -res["zero"].net
            q[1].metric("BTB upfront로만 받는다면", f"{need_up / OKU:,.1f}억",
                        "running 라인피 없이 거래일 현금으로", delta_color="off")
    elif res["loc"] == "upfront":
        q[0].metric("손익분기 upfront", f"{(res['upfront'] - r.net) / OKU:,.1f}억",
                    f"입력 {res['upfront'] / OKU:,.1f}억", delta_color="off")
        br = res["be_running"]
        q[1].metric(f"running으로 받는다면 ({fee_loc_label('client', fx_mode)})", fmt(br.extra),
                    "upfront 없이 고객 다리 라인피만으로", delta_color="off")
    else:   # 직접 입력: 조정 다리의 손익분기 조건
        tr = deal.client_trade if deal.solve_on == "client" else deal.hedge_trade
        if fx_mode:
            now = fwd_rate(tr)
            krw = next(l for l in tr.legs if l.ccy == "KRW")
            target = now + (1 if krw.direction == "receive" else -1) * be.extra
            q[0].metric(f"{where} 손익분기 선도환율 ({UL})", f"{target * qu:,.2f}",
                        f"현재 {now * qu:,.2f} → {gap_txt(be.extra)}", delta_color=gap_color)
        else:
            leg = tr.legs[tr.leg("receive" if deal.solve_on == "client" else "pay")]
            sign = 1 if deal.solve_on == "client" else -1
            scale, unit, nd = (100, "%", 4) if leg.kind == "fixed" else (1e4, "bp", 2)
            now, target = leg.rate * scale, (leg.rate + sign * be.extra) * scale
            q[0].metric(f"{where} {leg.ccy} 다리 손익분기 {'금리' if leg.kind == 'fixed' else '스프레드'}",
                        f"{target:.{nd}f}{unit}", f"현재 {now:.{nd}f}{unit} → {gap_txt(be.extra)}",
                        delta_color=gap_color)
        q[1].metric("부족분을 upfront로 받는다면", f"{max(-r.net, 0) / OKU:,.1f}억",
                    "자본비용 반영 손익이 이미 양수면 0", delta_color="off")
    q[2].caption("라인피가 고객 익스포저나 딜러 VM을 바꾸므로 같은 난수로 다시 계산해 찾았다. "
                 + (f"FX 선도는 선도환율 마진({UL})." if fx_mode else "bp는 해당 다리 명목 기준.")
                 + " upfront는 익스포저를 바꾸지 않는다.")

    a, b = st.columns([1, 1])
    with a:
        st.markdown("**비용 항목** (양수 = 비용)")
        cost_df = pd.DataFrame([{"항목": n, "억원": v / OKU, UL: conv(v)} for n, v in r.costs.items()])
        bar = alt.Chart(cost_df).mark_bar(color=SERIES[0], cornerRadiusEnd=4, height=14).encode(
            x=alt.X("억원:Q", title="억원"), y=alt.Y("항목:N", sort=None, title=None),
            tooltip=["항목", alt.Tooltip("억원:Q", format=",.2f"), alt.Tooltip(f"{UL}:Q", format=",.2f")],
        ).properties(height=34 * len(cost_df))
        st.altair_chart(bar, width="stretch")
    with b:
        st.markdown("**요구수익률 민감도** (세후, 자본비용 반영 손익)")
        hs = [0.05, 0.07, 0.09, 0.10, 0.12, 0.15]
        hdf = pd.DataFrame([{"세후 요구수익률": f"{h:.0%}", "KVA 억원": r.kva_at(h) / OKU,
                             "손익 억원": r.net_at(h) / OKU, f"손익 {UL}": conv(r.net_at(h))} for h in hs])
        st.dataframe(hdf.style.format({"KVA 억원": "{:,.2f}", "손익 억원": "{:,.2f}", f"손익 {UL}": "{:+,.2f}"}),
                     hide_index=True, width="stretch")
        peak = r.total_capital.max()
        st.caption(f"최대 필요자본 {peak / OKU:,.1f}억 (최대 RWA {sum(r.rwa.values()).max() / OKU:,.0f}억), "
                   f"헤지 IM(은행이 낸 금액) 거래일 {r.im[0] / OKU:,.1f}억")

    a, b = st.columns(2)
    with a:
        st.markdown("**필요자본 경로** (기대값, CET1 목표비율 기준)")
        st.altair_chart(line_chart(profile_df(r.times, r.capital), "억원", stacked=True),
                        width="stretch")
    with b:
        st.markdown("**고객 익스포저와 담보**")
        st.altair_chart(line_chart(profile_df(r.times, {
            "고객 기대 익스포저 (EE)": r.ee_client, "고객 PFE 95%": r.pfe_client,
            "딜러에 낸 VM 기대값": r.vm_posted}), "억원"), width="stretch")

    with st.expander("연도별 관리회계 손익 (첫날 이익 인식 후 FVA 비용, 할인 전)"):
        pnl = pd.DataFrame(annual_pnl(r))
        for c in ["첫날 이익", "FVA 비용", "평균 RWA", "평균 필요자본"]:
            pnl[c] = pnl[c] / OKU
        st.dataframe(pnl.style.format({c: "{:,.2f}" for c in pnl.columns if c != "연도"}),
                     hide_index=True, width="stretch")
    if be is not None:
        with st.expander("손익분기 반복 기록"):
            st.dataframe(pd.DataFrame([{f"조정 ({UL})": d * (qu if fx_mode else 1e4), "순마진 (억원)": rr.net / OKU}
                                       for d, rr in be.runs]), hide_index=True)
