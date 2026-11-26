"""라인피 계산기 시장 데이터 (KRW·USD·JPY).

    python -m linefee.market        # 블룸버그 현재값 + 과거 5년 주간 변동성·상관 받기

커브는 linefee/curves.py에서 QuantLib으로 실제 상품 관행대로 부트스트랩한다.
    USD SOFR (USD 담보 할인 = 추정)      SOFR O/N 지표 참고, OIS 1주~30년        USOSFR{1Z,A,B,C,F,I,1,1F,n}
    JPY TONA (JPY 담보 할인 = 추정)      OIS 1주~30년                           JYSO{...}
    KRW CD (원화 IRS, KRW 담보 단일 커브) CD 91일 + IRS 6개월~30년               KWCDC Index, KWSWO{F,I,1,1F,n}
    KRW (USD 담보 할인)                   USDKRW 스왑포인트 1주~12개월 + CCS 2~30년  KRW{1W,1M,...,12M}, KRWNSQ{n}
    JPY (USD 담보 할인)                   USDJPY 스왑포인트 1주~12개월 + TONA/SOFR 베이시스 2~30년  JPY{...}, JYBSS{n}Y
환율: KRW 기준 1단위당 원화. 현물 호가는 현물 결제일(T+2) 기준이고, 엔진은 이를 평가일 기준으로 바꾼
      값(spot = 호가 x DF_KRW(결제일) / DF_통화(결제일))을 쓴다. 선도 = spot x DF_통화(t) / DF_KRW(t)
변동성: USDKRW·JPYKRW ATM 내재변동성 만기 구조, USDJPY는 두 환율 간 내재 상관 계산용
과거 추정: 금리 10년 주간 변화(정규), 환율 로그 주간 변화. 5년, 금요일 기준
"""
import json
import os
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import QuantLib as ql

from market_data import SNAPSHOT as LEGACY_SNAPSHOT
from market_data import years_ago

from . import curves as cv
from .curves import Curve

SNAPSHOT = Path(__file__).with_name("market_snapshot.json")
CURRENCIES = ("KRW", "USD", "JPY")
YEARS = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 15, 20, 25, 30]
SHORT_OIS = {"1W": "1Z", "1M": "A", "2M": "B", "3M": "C", "6M": "F", "9M": "I", "18M": "1F"}
FX_TENORS = ["1W", "1M", "2M", "3M", "6M", "9M", "12M"]
VOL_TENORS = [1, 3, 5, 7, 10]


def _ois(root: str) -> dict:
    out = {k: f"{root}{c} Curncy" for k, c in SHORT_OIS.items()}
    return out | {f"{n}Y": f"{root}{n} Curncy" for n in YEARS}


TICKERS = {
    "spot": {"USD": "USDKRW Curncy", "JPY": "JPYKRW Curncy", "USDJPY": "USDJPY Curncy"},
    "usd_ois": _ois("USOSFR"),
    "jpy_ois": _ois("JYSO"),
    "krw_irs": {"CD": "KWCDC Index", "6M": "KWSWOF Curncy", "9M": "KWSWOI Curncy", "18M": "KWSWO1F Curncy"}
               | {f"{n}Y": f"KWSWO{n} Curncy" for n in YEARS},
    "krw_ccs": {f"{n}Y": f"KRWNSQ{n} Curncy" for n in YEARS},
    "jpy_basis_bp": {"1Y": "JYBSS12M Curncy"} | {f"{n}Y": f"JYBSS{n}Y Curncy" for n in YEARS if n > 1},
    "fx_pts_USD": {k: f"KRW{k} Curncy" for k in FX_TENORS},
    "fx_pts_JPY": {k: f"JPY{k} Curncy" for k in FX_TENORS},
    "vol_USD": {n: f"USDKRWV{n}Y Curncy" for n in VOL_TENORS},
    "vol_JPY": {n: f"JPYKRWV{n}Y Curncy" for n in VOL_TENORS},
    "vol_USDJPY": {n: f"USDJPYV{n}Y Curncy" for n in VOL_TENORS},
}
FX_POINT_SCALE = {"USD": 1.0, "JPY": 100.0}   # 블룸버그 FWD_SCALE: KRW 0자리, JPY 2자리

HIST_TENOR = 10
HIST_TICKERS = {
    "r_USD": f"USOSFR{HIST_TENOR} Curncy",
    "r_KRW": f"KRWNSQ{HIST_TENOR} Curncy",
    "r_JPY_ois": f"JYSO{HIST_TENOR} Curncy",
    "r_JPY_basis_bp": f"JYBSS{HIST_TENOR}Y Curncy",
    "fx_USD": "USDKRW Curncy",
    "fx_JPY": "JPYKRW Curncy",
    "r_KRW_irs": f"KWSWO{HIST_TENOR} Curncy",   # 변동성만 (원화 IRS 거래의 KRW 요인)
}
# 시뮬레이션 요인 이름: 금리 r_{통화}, 환율 fx_{통화} (KRW 대비)
FACTORS = ["r_USD", "r_KRW", "r_JPY", "fx_USD", "fx_JPY"]


def snapshot_guide(path: Path) -> str:
    """스냅샷 파일이 없을 때 보이는 안내 (앱·테스트 공통)."""
    return (f"시장 스냅샷이 없다: {path.name} ({path})" + chr(10)
            + "스냅샷은 블룸버그 라이선스 데이터라 저장소에 없고 로컬에만 둔다 (docs/DECISIONS.md O1)." + chr(10)
            + "  - 최신 스냅샷 (linefee/market_snapshot.json): 블룸버그 터미널이 켜진 PC에서 python -m linefee.market 으로 받는다 "
            + "(blpapi 필요, requirements.txt 참고)." + chr(10)
            + "  - 회귀 기준 스냅샷 (루트 market_snapshot.json, 2026-11-11): 새로 받을 수 없다. 기존 작업 PC의 파일을 복사해 둔다." + chr(10)
            + "자세한 절차는 README.md.")


def require_snapshot(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(snapshot_guide(path))


# ---------------------------------------------------------------------------
# 날짜 (블룸버그와 무관)
# ---------------------------------------------------------------------------
def prev_business_day(d: date, ccys=CURRENCIES) -> date:
    """d 이전(d 포함) 가장 가까운, 통화들의 공통 영업일 (curves.calendar 기준)."""
    cal = cv.calendar(ccys)
    while not cal.isBusinessDay(ql.Date(d.day, d.month, d.year)):
        d -= timedelta(days=1)
    return d


def check_business_day(as_of: str, ccys=CURRENCIES) -> None:
    """as_of가 공통 영업일이 아니면 원인을 알 수 있는 오류를 낸다 (그대로 두면 부트스트랩이 지표 고정값 오류로 실패)."""
    d = date.fromisoformat(as_of)
    if prev_business_day(d, ccys) != d:
        raise ValueError(f"스냅샷 as_of={as_of}({'월화수목금토일'[d.weekday()]})는 {'·'.join(ccys)} 공통 영업일이 아니다. "
                         f"호가 기준일은 영업일이어야 한다 (직전 영업일 {prev_business_day(d, ccys)}). "
                         "스냅샷의 as_of를 호가 기준일로 고치거나 다시 받는다.")


# ---------------------------------------------------------------------------
# 블룸버그
# ---------------------------------------------------------------------------
def fetch(years: int = 5, path: Path = SNAPSHOT) -> dict:
    """현재값과 과거 추정치를 받아 스냅샷으로 저장한다. 값이 없는 테너는 빠진다.

    필수 호가(OPTIONAL_GROUPS 밖)가 하나라도 없으면 ValueError로 멈추고 기존 파일을 덮어쓰지 않는다.
    """
    from bbg import bdh, bdp

    tickers = [t for group in TICKERS.values() for t in group.values()]
    raw = bdp(tickers, ["PX_LAST"])
    snap = {"as_of": prev_business_day(date.today()).isoformat(), "source": "Bloomberg PX_LAST", "missing": [],
            "note": "fx_pts_*: 스왑포인트를 통화 단위로 환산한 값 (KRW 그대로, JPY / 100)"}
    for key, group in TICKERS.items():
        snap[key] = {}
        for n, t in group.items():
            if "PX_LAST" in raw.get(t, {}):
                v = raw[t]["PX_LAST"]
                if key.startswith("fx_pts_"):
                    v = v / FX_POINT_SCALE[key[-3:]]
                snap[key][str(n)] = v
            else:
                snap["missing"].append(t)

    end = date.today()
    start = years_ago(end, years)
    series = {k: dict(bdh(t, "PX_LAST", start.strftime("%Y%m%d"), end.strftime("%Y%m%d")))
              for k, t in HIST_TICKERS.items()}
    days = sorted(set.intersection(*(set(v) for v in series.values())))
    fridays = [d for d in days if d.weekday() == 4]

    def col(k):
        return np.array([series[k][d] for d in fridays])

    levels = np.vstack([
        col("r_USD") / 100,
        col("r_KRW") / 100,
        (col("r_JPY_ois") + col("r_JPY_basis_bp") / 100) / 100,
        np.log(col("fx_USD")),
        np.log(col("fx_JPY")),
    ])
    x = np.diff(levels, axis=1)
    vol = x.std(axis=1, ddof=1) * np.sqrt(52)
    irs_vol = float(np.diff(col("r_KRW_irs") / 100).std(ddof=1) * np.sqrt(52))
    snap["hist"] = {
        "window": f"{fridays[0]:%Y-%m-%d} ~ {fridays[-1]:%Y-%m-%d}, 주간 {x.shape[1]}개",
        "tickers": HIST_TICKERS,
        "labels": FACTORS,
        "sigma": dict(zip(FACTORS, map(float, vol))) | {"r_KRW_irs": irs_vol},  # 금리: 연 정규(소수), 환율: 로그
        "corr": np.corrcoef(x).round(6).tolist(),
    }
    check_snapshot(snap)   # 필수 호가가 빠졌으면 여기서 예외: 기존 파일은 그대로 둔다 (L-022)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(snap, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)   # 다 쓴 뒤에 교체: 쓰다 실패해도 기존 스냅샷이 남는다
    return snap


# ---------------------------------------------------------------------------
# 시장 객체
# ---------------------------------------------------------------------------
@dataclass
class MarketData:
    as_of: str
    spot: dict                 # 평가일 기준 1단위당 원화 (선도 공식의 출발점). KRW = 1
    spot_quote: dict           # 현물 호가 (결제일 T+2 기준). 화면 표시·명목 환산용
    disc: dict                 # 통화 -> Curve (USD 담보 할인)
    proj: dict                 # 통화 -> Curve (변동금리 추정). USD SOFR, JPY TONA, KRW CD 91일
    local: dict                # 통화 -> Curve (자국 통화 담보 할인: CCP 청산 IRS·OIS)
    fx_vol: dict               # 통화 -> {만기(년): ATM 내재변동성(소수)}, KRW 대비
    usdjpy_vol: dict = field(default_factory=dict)
    hist: dict = field(default_factory=dict)   # labels, sigma, corr, window
    source: str = ""
    quotes: dict = field(default_factory=dict, repr=False)   # 부트스트랩에 쓴 호가 (검증·표시용)
    missing: list = field(default_factory=list)   # 스냅샷을 받을 때 값이 없던 티커 (화면 표시용)
    short_end: bool = True     # 1년 미만 상품(스왑포인트, 단기 OIS, CD)이 커브에 들어갔는지

    def disc_for(self, leg) -> Curve:
        """다리 할인 커브. leg.discount == "local"이면 자국 통화 담보 커브, 아니면 USD 담보 커브."""
        return self.local[leg.ccy] if getattr(leg, "discount", "csa") == "local" else self.disc[leg.ccy]

    def fwd_fx(self, ccy: str, t) -> np.ndarray:
        """t 시점 선도환율 (1단위당 원화)."""
        if ccy == "KRW":
            return np.ones_like(np.asarray(t, dtype=float))
        return self.spot[ccy] * self.disc[ccy].df(t) / self.disc["KRW"].df(t)

    def schedule(self, trade, leg) -> cv.Sched:
        """다리의 실제 지급 일정 (curves.schedule, 평가일 기준 시점)."""
        return cv.schedule(self.as_of, trade.product, tuple(sorted(trade.currencies)),
                           int(round(trade.maturity * 12)), leg.freq, leg.day_count)

    def end_time(self, trade) -> float:
        """거래 마지막 지급 시점 (년, 평가일 기준)."""
        return float(max(self.schedule(trade, l).pay[-1] for l in trade.legs))

    def term_vol(self, ccy: str, t: float) -> float:
        """만기 t까지의 평균 내재변동성. 최장 만기 이후는 평탄."""
        v = self.fx_vol[ccy]
        k = sorted(v)
        return float(np.interp(t, k, [v[i] for i in k]))

    def implied_fx_corr(self, t: float) -> float | None:
        """USDKRW·JPYKRW 로그 수익률 상관 (USDJPY 변동성으로 역산). 자료가 없으면 None.

        log USDJPY = log USDKRW - log JPYKRW 이므로
        rho = (s_USD^2 + s_JPY^2 - s_USDJPY^2) / (2 s_USD s_JPY)
        """
        if not self.usdjpy_vol or "USD" not in self.fx_vol or "JPY" not in self.fx_vol:
            return None
        k = sorted(self.usdjpy_vol)
        s_uj = float(np.interp(t, k, [self.usdjpy_vol[i] for i in k]))
        s_u, s_j = self.term_vol("USD", t), self.term_vol("JPY", t)
        return float(np.clip((s_u**2 + s_j**2 - s_uj**2) / (2 * s_u * s_j), -0.99, 0.99))


OPTIONAL_GROUPS = ("fx_pts_USD", "fx_pts_JPY")   # 1년 미만 스왑포인트: 없으면 short_end=False 로 읽는다


RATE_GROUPS = ("usd_ois", "jpy_ois", "krw_irs", "krw_ccs")   # 호가 단위: % (4.37 = 4.37%)
HIST_KEYS = ("labels", "sigma", "corr")


def _num(v) -> bool:
    """유한한 실수인가 (None·문자열·bool·NaN·inf 제외)."""
    return isinstance(v, (int, float)) and not isinstance(v, bool) and bool(np.isfinite(v))


def check_snapshot(snap: dict) -> None:
    """필수 키·테너(TICKERS 기준)가 모두 있고 값이 쓸 수 있는지 본다. 문제를 전부 모아 한 번에 알린다.

    값 검사: 모든 호가는 유한한 숫자, 금리(%)는 -1~25(전 테너가 0.1 미만이면 소수 단위로 본다), 베이시스(bp)는 +-300 이내,
    현물·변동성은 0보다 큼. hist는 labels·sigma·corr가 있어야 한다 (L-026).
    """
    lost = [f"{key}.{n}" for key, group in TICKERS.items() if key not in OPTIONAL_GROUPS
            for n in group if str(n) not in snap.get(key, {})]
    if lost:
        raise ValueError(f"스냅샷에 필수 호가가 없다 ({len(lost)}개): {', '.join(lost)}"
                         + (f". 받을 때 못 받은 티커: {', '.join(snap['missing'])}" if snap.get("missing") else ""))
    bad = []
    for key, group in TICKERS.items():
        vals = snap.get(key, {})
        for n in group:
            v = vals.get(str(n))
            if v is None and key in OPTIONAL_GROUPS:
                continue
            if not _num(v):
                bad.append(f"{key}.{n}={v!r}: 유한한 숫자가 아님")
            elif key in RATE_GROUPS and not -1.0 <= v <= 25.0:
                bad.append(f"{key}.{n}={v}: 금리(%)는 -1~25")
            elif key == "jpy_basis_bp" and abs(v) > 300:
                bad.append(f"{key}.{n}={v}: 베이시스(bp)는 +-300 이내")
            elif (key == "spot" or key.startswith("vol_")) and v <= 0:
                bad.append(f"{key}.{n}={v}: 0보다 커야 함")
    for key in RATE_GROUPS:
        v = [x for x in snap.get(key, {}).values() if _num(x)]
        if len(v) >= 3 and max(abs(x) for x in v) < 0.1:
            bad.append(f"{key}: 모든 금리가 0.1 미만 (호가 단위는 %, 소수(0.0437)로 넣지 않았는지 확인)")
    hist = snap.get("hist")
    if not isinstance(hist, dict) or any(k not in hist for k in HIST_KEYS):
        bad.append(f"hist: {', '.join(HIST_KEYS)}가 있어야 함 (변동성·상관 자료)")
    if bad:
        raise ValueError(f"스냅샷 값에 문제가 있다 ({len(bad)}개): " + "; ".join(bad[:12]) + (" ..." if len(bad) > 12 else ""))


def _by_int(d: dict) -> dict:
    return {int(k): v for k, v in d.items()}


def _years(d: dict) -> dict:
    """{"1": x} 형식(예전 스냅샷)을 {"1Y": x}로."""
    return {(k if k[-1] in "WMY" or k == "CD" else f"{k}Y"): v for k, v in d.items()}


def _today_spot(quote: float, ccy: str, krw: Curve, fgn: Curve, as_of: str) -> float:
    """현물 결제일 기준 호가 -> 평가일 기준 환율."""
    ts = cv.spot_date_time(as_of, ["KRW", ccy])
    return float(quote * krw.df(ts) / fgn.df(ts))


def _set_today(as_of: str) -> ql.Date:
    today = cv.qdate(as_of)
    ql.Settings.instance().evaluationDate = today
    return today


def load(path: Path = SNAPSHOT) -> MarketData:
    require_snapshot(path)
    snap = json.loads(path.read_text(encoding="utf-8"))
    check_snapshot(snap)
    check_business_day(snap["as_of"])
    today = _set_today(snap["as_of"])
    usd_q = cv.sofr_curve(today, snap["usd_ois"])
    usd_h = ql.YieldTermStructureHandle(usd_q)
    tona_q = cv.tona_curve(today, snap["jpy_ois"])
    tona_h = ql.YieldTermStructureHandle(tona_q)
    usdkrw, usdjpy = snap["spot"]["USD"], snap["spot"]["USDJPY"]
    krw_q = cv.krw_csa_curve(today, snap["krw_ccs"], snap.get("fx_pts_USD", {}), usdkrw, usd_h)
    jpy_q = cv.jpy_csa_curve(today, snap["jpy_basis_bp"], snap.get("fx_pts_JPY", {}), usdjpy, usd_h, tona_h)
    cd_q = cv.krw_irs_curve(today, snap["krw_irs"])
    usd, tona = Curve.from_ql(usd_q, "USD SOFR"), Curve.from_ql(tona_q, "JPY TONA")
    krw, jpy = Curve.from_ql(krw_q, "KRW (USD 담보)"), Curve.from_ql(jpy_q, "JPY (USD 담보)")
    cd = Curve.from_ql(cd_q, "KRW CD (원화 IRS)")
    quote = {"KRW": 1.0, "USD": usdkrw, "JPY": usdkrw / usdjpy}   # JPYKRW는 교차 환율로 (커브와 일관)
    return MarketData(
        as_of=snap["as_of"],
        spot={"KRW": 1.0, "USD": _today_spot(usdkrw, "USD", krw, usd, snap["as_of"]),
              "JPY": _today_spot(quote["JPY"], "JPY", krw, jpy, snap["as_of"])},
        spot_quote=quote,
        disc={"KRW": krw, "USD": usd, "JPY": jpy},
        proj={"USD": usd, "JPY": tona, "KRW": cd},
        local={"USD": usd, "JPY": tona, "KRW": cd},
        fx_vol={c: {k: v / 100 for k, v in _by_int(snap[f"vol_{c}"]).items()} for c in ("USD", "JPY")},
        usdjpy_vol={k: v / 100 for k, v in _by_int(snap["vol_USDJPY"]).items()},
        hist=snap.get("hist", {}),
        source=f"{path.name} ({snap['as_of']})",
        quotes={k: snap.get(k, {}) for k in ("usd_ois", "jpy_ois", "krw_irs", "krw_ccs", "jpy_basis_bp",
                                     "fx_pts_USD", "fx_pts_JPY", "spot")},
        missing=list(snap.get("missing", [])),
        short_end=bool(snap.get("fx_pts_USD")),
    )


def load_legacy(path: Path = LEGACY_SNAPSHOT) -> MarketData:
    """엔/원 30년 CCS 검토에 쓴 스냅샷(market_snapshot.json, 2026-11-11)을 읽는다.

    USD 커브·USDKRW·1년 미만 상품이 없다. 그래서
      - USD 다리가 있는 거래에는 쓸 수 없다 (회귀 테스트·엔/원 검토용)
      - KRW·JPY 할인 커브는 1년물 CCS·베이시스부터 부트스트랩한다 (1년 이내는 1년 구간 선도 평탄)
      - 베이시스 헬퍼의 USD 다리(SOFR 플랫, 담보 통화)는 어떤 USD 커브에서도 Par이므로 4% 평탄 커브를 쓴다
    """
    require_snapshot(path)
    snap = json.loads(path.read_text(encoding="utf-8"))
    check_business_day(snap["as_of"], ("KRW", "JPY"))
    today = _set_today(snap["as_of"])
    usd_h = ql.YieldTermStructureHandle(cv.flat_curve(today, 0.04))
    tona_q = cv.tona_curve(today, _years(snap["jpy_ois"]))
    jpy_q = cv.jpy_csa_curve(today, _years(snap["jpy_basis_bp"]), {}, 1.0, usd_h,
                             ql.YieldTermStructureHandle(tona_q))
    krw_q = cv.krw_csa_curve(today, _years(snap["krw_ccs"]), {}, 1.0, usd_h)
    cd_q = cv.krw_irs_curve(today, _years(snap["krw_irs"]))
    krw, jpy = Curve.from_ql(krw_q, "KRW (USD 담보)"), Curve.from_ql(jpy_q, "JPY (USD 담보)")
    tona, cd = Curve.from_ql(tona_q, "JPY TONA"), Curve.from_ql(cd_q, "KRW CD (원화 IRS)")
    h = snap["hist"]
    labels = ["r_KRW", "r_JPY", "fx_JPY"]
    corr = [[1.0, h["corr_krw_jpy"], h["corr_fx_krw"]],
            [h["corr_krw_jpy"], 1.0, h["corr_fx_jpy"]],
            [h["corr_fx_krw"], h["corr_fx_jpy"], 1.0]]
    return MarketData(
        as_of=snap["as_of"],
        spot={"KRW": 1.0, "JPY": _today_spot(snap["jpykrw_spot"], "JPY", krw, jpy, snap["as_of"])},
        spot_quote={"KRW": 1.0, "JPY": snap["jpykrw_spot"]},
        disc={"KRW": krw, "JPY": jpy},
        proj={"JPY": tona, "KRW": cd},
        local={"KRW": cd, "JPY": tona},
        fx_vol={"JPY": {k: v / 100 for k, v in _by_int(snap["jpykrw_vol"]).items()}},
        hist={"labels": labels,
              "sigma": {"r_KRW": h["sigma_krw"], "r_JPY": h["sigma_jpy"], "fx_JPY": h["sigma_fx_hist"]},
              "corr": corr, "window": h["window"]},
        source=f"{path.name} ({snap['as_of']}, 엔/원 검토 기준)",
        quotes={k: _years(snap[k]) for k in ("jpy_ois", "krw_irs", "krw_ccs", "jpy_basis_bp")},
        short_end=False,
    )


if __name__ == "__main__":
    s = fetch()
    print(f"{s['as_of']} 저장: {SNAPSHOT}, 누락 {s['missing'] or '없음'}")
    print("hist", s["hist"]["window"], {k: round(v, 4) for k, v in s["hist"]["sigma"].items()})
