"""라인피 계산기 대시보드.

    streamlit run app.py

거래 1건(고객)과 헤지(미러 자동 또는 직접 입력)를 넣으면 비용과 손익분기 수수료를 계산한다.
방법론과 근사 사항: docs/METHODOLOGY_v1.md
"""
import importlib
import sys
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
_ENGINE = ["linefee.curves", "linefee.market", "linefee.trade", "linefee.engine", "linefee.regcap",
           "linefee.pricing"]


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

from linefee.market import SNAPSHOT, fetch, load, load_legacy  # noqa: E402  (다시 읽은 뒤 이름을 묶는다)
from linefee.engine import trade_value_today  # noqa: E402
from linefee.pricing import (Run, annual_pnl, breakeven, fee_unit_pv, market_gap,  # noqa: E402
                             market_hedge, par_client, run)
from linefee.trade import (BA_CVA_RW, CSA, BankParams, Counterparty, Leg, Trade,  # noqa: E402
                           build_deal, fee_delta)

OKU = 1e8
CCYS = ["KRW", "USD", "JPY"]
UNIT = {"KRW": (1e8, "억원"), "JPY": (1e8, "억엔"), "USD": (1e6, "백만달러")}
CSA_TYPES = {"없음 (무담보)": CSA(), "VM": CSA(vm=True), "VM + IM": CSA(vm=True, im=True)}
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]   # 범주색 1~4번 (고정 순서)
BTB, LINE, DIRECT = "BTB (헤지도 같은 오프마켓, 라인피 제외)", "딜러 라인 (Par 헤지)", "직접 입력 (BTB 헤지 조건)"
HEDGE_MODES = {BTB: "btb", LINE: "line", DIRECT: "direct"}


PRODUCTS = {"CCS": "통화스왑", "FXFWD": "FX 선도", "KRWIRS": "원화 IRS", "USDOIS": "달러 OIS"}
# 원화 IRS·달러 OIS 관행: (통화, 연 지급 횟수, 일수 계산, 변동금리). 고정·변동 다리 같은 관행, CCP 청산
IRS_CONV = {"KRWIRS": ("KRW", 4, "ACT/365", "CD 91일"), "USDOIS": ("USD", 1, "ACT/360", "SOFR 후취 복리")}
FLOAT_INDEX = {"USD": "SOFR", "JPY": "TONA", "KRW": "CD 91일"}
IRS_DIRS = ["은행 고정 수취 (고객 고정 지급)", "은행 고정 지급 (고객 고정 수취)"]
PRESETS = ["엔/원 30년 CCS (LH 중개)", "USD/KRW 5년 CCS (기업)", "USD/KRW 1년 FX 선도 (기업)",
           "원화 IRS 5년 (기업, 고정 지급)", "달러 OIS 3년 (기업, 고정 지급)"]


def fee_loc_label(loc: str, fx: bool) -> str:
    if loc == "hedge":
        return "헤지 선도환율 마진" if fx else "딜러에게 주는 다리에서 차감"
    return "고객 선도환율 마진" if fx else "고객이 내는 다리에 가산"


MARKETS = {"최신 (블룸버그 스냅샷)": "latest", "엔/원 검토 기준 (2026-11-11, KRW·JPY만)": "legacy"}

st.set_page_config(page_title="라인피 계산기", layout="wide")


# ---------------------------------------------------------------------------
# 시장 데이터
# ---------------------------------------------------------------------------
@st.cache_resource
def _load_market(source: str, engine_version: tuple):
    # engine_version: 엔진 파일 수정 시각. 엔진이 바뀌면 키가 달라져 시장 객체도 새 클래스로 다시 만든다
    return load_legacy() if source == "legacy" else load()


def get_market(source: str):
    return _load_market(source, tuple(sorted(_engine_mtimes().items())))


get_market.clear = _load_market.clear


# ---------------------------------------------------------------------------
# 예시 거래
# ---------------------------------------------------------------------------
def _set_leg(prefix, ccy, amt, kind="fixed", rate=0.0, spread=0.0, freq=1, dc="ACT/365", fx_end=True):
    ss = st.session_state
    ss[prefix + "ccy"], ss[prefix + "amt"], ss[prefix + "kind"] = ccy, float(amt), kind
    ss[prefix + "rate"], ss[prefix + "spread"] = float(rate), float(spread)
    ss[prefix + "freq"], ss[prefix + "dc"], ss[prefix + "fx_end"] = freq, dc, fx_end


def _set_cp(prefix, name, rw, sector, grade, pd_, csa):
    ss = st.session_state
    ss[prefix + "name"], ss[prefix + "rw"] = name, float(rw)
    ss[prefix + "sector"], ss[prefix + "grade"] = sector, grade
    ss[prefix + "pd"], ss[prefix + "lgd"], ss[prefix + "csa"] = float(pd_), 60.0, csa


def load_preset():
    ss = st.session_state
    name = ss["preset"]
    m = get_market(MARKETS[ss["market_src"]])
    ss["upfront_oku"] = 0.0
    if name == "엔/원 30년 CCS (LH 중개)":
        ss["product"], ss["mat"] = "CCS", 30.0
        _set_leg("cr_", "KRW", 100 * m.spot_quote["JPY"], rate=4.03, dc="ACT/360")
        _set_leg("cp_", "JPY", 100, rate=4.07)
        _set_cp("cl_", "LH", 0.0, "지방정부·정부보증 비금융·공공기관", "투자등급", 0.0, "없음 (무담보)")
        _set_cp("dl_", "노무라", 50.0, "금융기관(정부보증 금융 포함)", "투자등급", 0.0, "VM + IM")
        ss["h_mode"], ss["fee_loc"], ss["fee_bp"] = BTB, "고객 다리 running", 10.0
    elif name == "USD/KRW 5년 CCS (기업)" and "USD" in m.spot:
        ss["product"], ss["mat"] = "CCS", 5.0
        # 시장가 KRW 금리 (KRW 고정 분기 ACT/365 vs SOFR 분기 ACT/360, 현물 호가 기준 명목)
        probe = Trade([Leg("KRW", 1e8 * m.spot_quote["USD"], "receive", rate=0.03, freq=4),
                       Leg("USD", 1e8, "pay", kind="float", freq=4, day_count="ACT/360")], 5.0)
        par = par_client(probe, m).legs[0].rate
        _set_leg("cr_", "KRW", 100 * m.spot_quote["USD"] / 100, rate=round(par * 100, 4), freq=4, dc="ACT/365")
        _set_leg("cp_", "USD", 100, kind="float", spread=0.0, freq=4, dc="ACT/360")
        _set_cp("cl_", "기업 고객", 100.0, "소재·에너지·산업재·농업·제조·광업", "투자등급", 0.2, "없음 (무담보)")
        _set_cp("dl_", "헤지 딜러", 50.0, "금융기관(정부보증 금융 포함)", "투자등급", 0.0, "VM + IM")
        ss["h_mode"], ss["fee_loc"], ss["fee_bp"] = LINE, "고객 다리 running", 10.0
    elif name == "USD/KRW 1년 FX 선도 (기업)" and "USD" in m.spot:
        ss["product"], ss["mat"] = "FXFWD", 1.0
        probe = Trade([Leg("KRW", 1e8 * m.spot_quote["USD"], "receive"), Leg("USD", 1e8, "pay")], 1.0, "FXFWD")
        fwd = par_client(probe, m).legs[0].notional / 1e8      # 시장 선도환율 (스왑포인트 기준)
        _set_leg("cr_", "KRW", 100 * fwd / 100, fx_end=True)
        _set_leg("cp_", "USD", 100, fx_end=True)
        _set_cp("cl_", "기업 고객", 100.0, "소재·에너지·산업재·농업·제조·광업", "투자등급", 0.2, "없음 (무담보)")
        _set_cp("dl_", "헤지 딜러", 50.0, "금융기관(정부보증 금융 포함)", "투자등급", 0.0, "VM")
        ss["h_mode"], ss["fee_loc"], ss["fee_fx"] = LINE, "고객 선도환율 마진", 1.0
    elif name.startswith(("원화 IRS", "달러 OIS")):
        krw = name.startswith("원화")
        if not krw and "USD" not in m.local:   # 엔/원 검토 기준 스냅샷에는 USD 커브가 없다
            mirror_to_hedge()
            return
        ss["product"], ss["mat"] = ("KRWIRS", 5.0) if krw else ("USDOIS", 3.0)
        ss["irs_amt"], ss["irs_dir"], ss["irs_spread"] = (1000.0 if krw else 100.0), IRS_DIRS[0], 0.0
        _set_cp("cl_", "기업 고객", 100.0, "소재·에너지·산업재·농업·제조·광업", "투자등급", 0.2, "없음 (무담보)")
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


def on_product_change():
    """상품을 바꾸면 기본값을 맞춘다. FX 선도는 딜러 라인 헤지, IRS·OIS는 CCP 헤지와 파 금리."""
    ss = st.session_state
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
    st.session_state["preset"] = "엔/원 30년 CCS (LH 중개)"
    load_preset()
for _k, _v in {"ccp_name": "CCP (KRX·LCH)", "ccp_rw": 2.0, "irs_amt": 1000.0, "irs_dir": IRS_DIRS[0],
               "irs_rate": 0.0, "irs_spread": 0.0, "fee_bp": 10.0, "fee_fx": 1.0}.items():
    st.session_state.setdefault(_k, _v)


# ---------------------------------------------------------------------------
# 입력 위젯
# ---------------------------------------------------------------------------
def leg_inputs(prefix: str, title: str, direction: str, product: str) -> Leg:
    ss = st.session_state
    st.markdown(f"**{title}**")
    ccy = st.selectbox("통화", CCYS, key=prefix + "ccy")
    unit, label = UNIT[ccy]
    amt = st.number_input(f"명목 ({label})", key=prefix + "amt", min_value=0.0, step=10.0, format="%.2f")
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
               f"{ccy} 담보 커브로 평가 (CCP 청산 기준)")
    a, b = st.columns(2)
    a.number_input(f"명목 ({UNIT[ccy][1]})", key="irs_amt", min_value=0.0, step=100.0, format="%.2f")
    b.radio("방향", IRS_DIRS, key="irs_dir")
    a.number_input("고정금리 (%)", key="irs_rate", step=0.0001, format="%.4f",
                   help="만기·스프레드를 바꾸면 시장 파 금리로 다시 채운다. 라인피는 아래에서 따로 넣는다")
    b.number_input(f"{index} 가산 스프레드 (bp)", key="irs_spread", step=1.0, format="%.2f", on_change=set_irs_par)
    a.button("시장 파 금리로 채우기", on_click=set_irs_par)
    return make_irs(product)


def cp_inputs(prefix: str, title: str) -> Counterparty:
    st.markdown(f"**{title}**")
    name = st.text_input("이름", key=prefix + "name")
    c1, c2 = st.columns(2)
    rw = c1.number_input("신용 위험가중치 (%)", key=prefix + "rw", min_value=0.0, step=10.0,
                         help="표준방법. 예: 공공기관 0%, 은행·동등 증권사 A등급 30%·BBB 50%, 기업 무등급 100%")
    csa_name = c2.selectbox("CSA", list(CSA_TYPES), key=prefix + "csa")
    sector = st.selectbox("BA-CVA 업종", list(BA_CVA_RW), key=prefix + "sector")
    c3, c4, c5 = st.columns(3)
    grade = c3.selectbox("등급", ["투자등급", "투기등급·무등급"], key=prefix + "grade")
    pd_ = c4.number_input("연 PD (%)", key=prefix + "pd", min_value=0.0, step=0.05, format="%.2f")
    lgd = c5.number_input("LGD (%)", key=prefix + "lgd", min_value=0.0, max_value=100.0, step=5.0)
    cva_rw = BA_CVA_RW[sector][0 if grade == "투자등급" else 1]
    st.caption(f"BA-CVA 위험가중치 {cva_rw:.1%}")
    return Counterparty(name, rw / 100, cva_rw, pd_ / 100, lgd / 100, CSA_TYPES[csa_name])


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

with st.sidebar:
    st.header("시장 데이터")
    st.selectbox("기준", list(MARKETS), key="market_src", on_change=load_preset,
                 help="바꾸면 선택한 예시 거래를 그 시장 기준(현물환율)으로 다시 불러온다")
    m = get_market(MARKETS[st.session_state["market_src"]])
    st.caption(f"{m.source} · 현물 호가 " + ", ".join(f"{c}KRW {v:,.4f}" for c, v in m.spot_quote.items()
                                                    if c != "KRW")
               + ("" if m.short_end else " · 1년 미만 상품 없음 (1년물부터 부트스트랩)"))
    if st.button("블룸버그에서 새로 받기"):
        with st.spinner("블룸버그 조회 중 (현재값 + 과거 5년)"):
            try:
                fetch()
                get_market.clear()
                st.success(f"저장: {SNAPSHOT.name}")
            except Exception as e:  # 블룸버그 미접속 등
                st.error(f"실패: {e}")

    st.header("은행 파라미터")
    cap_ratio = st.number_input("CET1 목표비율 (%)", value=13.0, step=0.5)
    hurdle = st.number_input("세후 요구수익률 (%)", value=10.0, step=0.5)
    tax = st.number_input("세율 (%)", value=27.5, step=0.5)
    ftp = st.number_input("USD 담보 조달 FTP 스프레드 (bp)", value=12.0, step=1.0,
                          help="FTP - CSA 담보 이자(SOFR). USD 현금 담보 VM의 FVA에 쓴다")
    ftp_krw = st.number_input("KRW 담보 조달 FTP 스프레드 (bp)", value=12.0, step=1.0,
                              help="KRW FTP - KRW 현금 담보 이자. CCP 원화 IRS의 VM 조달(FVA)에 쓴다 (확인 필요)")
    kva_sp = st.number_input("KVA 조달 스프레드 alpha (bp)", value=10.0, step=1.0,
                             help="KVA 조달금리 r(t) = CD(원화 IRS) 커브 선도 + alpha")
    kva_curve = st.radio("KVA 조달금리 기준 커브", ["원화 IRS (CD)", "원화 CCS (USD 담보, 비교용)"], horizontal=True,
                         help="자본이 대체하는 KRW 조달의 기준금리. 원화 IRS(CD)가 확정 방식")
    fba = st.checkbox("받은 담보 조달이익(FBA) 인정", value=True)
    marginal = st.number_input("BA-CVA 한계계수", value=0.5, step=0.05,
                               help="은행 CVA 포트폴리오 대비 증분. 완전 분산이면 0.5(rho)")
    im_spread = st.number_input("낸 IM 조달 스프레드 (bp)", value=0.0, step=1.0)
    st.header("시뮬레이션")
    n_paths = st.select_slider("경로 수", [2000, 5000, 10000, 20000], value=5000)
    vol_scale = st.number_input("금리 변동성 배수", value=1.0, step=0.1)
    kappa = st.number_input("금리 평균회귀 (연)", value=0.0, step=0.01)

params = BankParams(capital_ratio=cap_ratio / 100, hurdle_after_tax=hurdle / 100, tax_rate=tax / 100,
                    ftp_spread=ftp / 1e4, ftp_spread_krw=ftp_krw / 1e4, kva_funding_spread=kva_sp / 1e4,
                    kva_funding="krw_irs" if kva_curve.startswith("원화 IRS") else "krw_ccs",
                    recognise_fba=fba, ba_cva_marginal_factor=marginal,
                    im_funding_spread=im_spread / 1e4, rate_vol_scale=vol_scale,
                    rate_mean_reversion=kappa, n_paths=n_paths)

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
a, b = st.columns(2)
with a:
    client = cp_inputs("cl_", "고객")
with b:
    if is_irs:
        st.markdown("**헤지: CCP**")
        ccp_name = st.text_input("이름", key="ccp_name")
        ccp_rw = st.number_input("거래 익스포저 위험가중치 (%)", key="ccp_rw", min_value=0.0, step=0.5,
                                 help="적격 CCP 2%. CVA 자본 면제. 부도기금 출연 자본은 미반영")
        st.caption(f"VM {IRS_CONV[product][0]} 현금 일일 정산, IM 은행 일방 납부(5영업일 99% VaR 근사), "
                   "SA-CCR MPOR 5영업일")
        dealer = Counterparty(ccp_name, ccp_rw / 100, 0.0, 0.0, 0.6,
                              CSA(vm=True, im=False, reg_mpor_days=5, ccy=IRS_CONV[product][0], ccp=True))
    else:
        dealer = cp_inputs("dl_", "헤지 딜러")

st.subheader("3. 헤지와 수수료")
if is_irs:
    hedge_kind = "ccp"
    st.caption("헤지는 CCP 청산 Par 거래. 계산 시 미러 거래의 고정금리를 조정해 거래일 가치 0으로 만든다.")
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
    errs = []
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


# 시장가 대비 차이 (라인피 전). 딜러 라인·CCP는 고객 거래가 라인피만큼만 오프마켓이라는 전제
QU = fx_quote_unit(client_trade) if is_fx else 1
GAP_UNIT = ("원/100엔" if QU == 100 else "원/외화 1단위") if is_fx else "bp"
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
                                          qu=QU, client_no_fee=client_trade, params=params, sig=sig)

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
    if res["hedge_kind"] in ("line", "ccp"):
        ht = deal.hedge_trade
        terms = " / ".join(
            f"{'수취' if l.direction == 'receive' else '지급'} {l.ccy} "
            + (f"{l.notional / UNIT[l.ccy][0]:,.2f}{UNIT[l.ccy][1]}" if ht.product == "FXFWD"
               else (f"{l.rate * 100:.4f}%" if l.kind == "fixed" else f"{FLOAT_INDEX[l.ccy]} + {l.rate * 1e4:.2f}bp"))
            for l in ht.legs)
        st.caption(f"{'CCP' if res['hedge_kind'] == 'ccp' else '딜러 라인'} Par 헤지 조건 (거래일 가치 0): {terms}")
    k = st.columns(4)
    k[0].metric("첫날 이익", f"{r.day1 / OKU:,.1f}억", f"고객 {r.v0_client / OKU:,.1f} / 헤지 {r.v0_hedge / OKU:,.1f}",
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
