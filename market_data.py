"""시장 데이터: 블룸버그에서 받아 market_snapshot.json에 저장하고, 모델은 스냅샷만 읽는다.

    python market_data.py      # 블룸버그에서 새로 받기
    python market_data.py hist # 금리 변동성·상관 추정치 추가 (과거 5년 주간)

커브 구성 (딜러 B CSA가 USD 현금이므로 USD SOFR 담보 기준)
    KRW 할인커브: KRW/USD 온쇼어 CCS (KRW 고정 vs SOFR) 파 금리       KRWNSQ{n} Curncy
    JPY 할인커브: JPY OIS(TONA) 파 금리 + TONA/SOFR 베이시스             JYSO{n}, JYBSS{n}Y Curncy
    SIMM용 KRW 금리 커브: KRW IRS (vs 91일 CD)                          KWSWO{n} Curncy
    선도환율 = 현물 x DF_JPY / DF_KRW
연 1회 지급 파 금리로 보고 부트스트랩한다 (실제 지급 주기 차이는 무시).
"""
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np

SNAPSHOT = Path(__file__).with_name("market_snapshot.json")
TENORS = [1, 2, 3, 4, 5, 7, 10, 12, 15, 20, 25, 30]
VOL_TENORS = [1, 3, 5, 7, 10]
CDS_TENORS = [1, 3, 5, 7, 10]

TICKERS = {
    "jpykrw_spot": "JPYKRW Curncy",
    "jpy_ois": {n: f"JYSO{n} Curncy" for n in TENORS},
    "jpy_basis_bp": {n: (f"JYBSS{n}Y Curncy" if n > 1 else "JYBSS12M Curncy") for n in TENORS},
    "krw_ccs": {n: f"KRWNSQ{n} Curncy" for n in TENORS},
    "krw_irs": {n: f"KWSWO{n} Curncy" for n in TENORS},
    "jpykrw_vol": {n: f"JPYKRWV{n}Y Curncy" for n in VOL_TENORS},
    "korea_cds_bp": {n: f"KOREA CDS USD SR {n}Y D14 Corp" for n in CDS_TENORS},
}


def fetch() -> dict:
    """블룸버그 PX_LAST를 받아 스냅샷으로 저장한다. 값이 없는 테너는 빠진다."""
    from bbg import bdp

    tickers = [TICKERS["jpykrw_spot"]]
    for key, by_tenor in TICKERS.items():
        if isinstance(by_tenor, dict):
            tickers += list(by_tenor.values())
    raw = bdp(tickers, ["PX_LAST"])

    snap = {"as_of": date.today().isoformat(), "source": "Bloomberg PX_LAST", "tickers": TICKERS}
    snap["jpykrw_spot"] = raw[TICKERS["jpykrw_spot"]]["PX_LAST"]
    missing = []
    for key, by_tenor in TICKERS.items():
        if not isinstance(by_tenor, dict):
            continue
        snap[key] = {}
        for n, t in by_tenor.items():
            if "PX_LAST" in raw.get(t, {}):
                snap[key][str(n)] = raw[t]["PX_LAST"]
            else:
                missing.append(t)
    snap["missing"] = missing
    SNAPSHOT.write_text(json.dumps(snap, indent=2, ensure_ascii=False), encoding="utf-8")
    return snap


HIST_TENOR = 10   # 금리 평행이동 요인의 대표 만기
HIST_TICKERS = {
    "krw": f"KRWNSQ{HIST_TENOR} Curncy",
    "jpy_ois": f"JYSO{HIST_TENOR} Curncy",
    "jpy_basis_bp": f"JYBSS{HIST_TENOR}Y Curncy",
    "fx": "JPYKRW Curncy",
}


def fetch_hist(years: int = 5) -> dict:
    """과거 주간 변화로 금리 평행이동 변동성과 세 요인 간 상관을 추정해 스냅샷에 추가한다.

    KRW 금리: KRW CCS 10년, JPY 금리: JPY OIS 10년 + TONA/SOFR 베이시스 10년, FX: log JPYKRW.
    주간(금요일) 변화를 쓴다. 일간은 서울과 도쿄 마감 시각이 달라 상관이 낮게 나온다.
    """
    from bbg import bdh

    end = date.today()
    start = date(end.year - years, end.month, end.day)
    series = {k: dict(bdh(t, "PX_LAST", start.strftime("%Y%m%d"), end.strftime("%Y%m%d")))
              for k, t in HIST_TICKERS.items()}
    days = sorted(set.intersection(*(set(v) for v in series.values())))
    fridays = [d for d in days if d.weekday() == 4]
    krw = np.array([series["krw"][d] for d in fridays]) / 100
    jpy = (np.array([series["jpy_ois"][d] for d in fridays])
           + np.array([series["jpy_basis_bp"][d] for d in fridays]) / 100) / 100
    lfx = np.log([series["fx"][d] for d in fridays])
    x = np.vstack([np.diff(krw), np.diff(jpy), np.diff(lfx)])
    vol = x.std(axis=1, ddof=1) * np.sqrt(52)
    corr = np.corrcoef(x)
    hist = {
        "window": f"{fridays[0]} ~ {fridays[-1]}, 주간 {x.shape[1]}개",
        "tickers": HIST_TICKERS,
        "sigma_krw": float(vol[0]),        # 연 표준편차 (소수, 정규)
        "sigma_jpy": float(vol[1]),
        "sigma_fx_hist": float(vol[2]),    # 참고용. 시뮬레이션은 내재변동성을 쓴다
        "corr_krw_jpy": float(corr[0, 1]),
        "corr_fx_krw": float(corr[2, 0]),  # FX = JPYKRW (엔 강세가 +)
        "corr_fx_jpy": float(corr[2, 1]),
    }
    snap = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    snap["hist"] = hist
    SNAPSHOT.write_text(json.dumps(snap, indent=2, ensure_ascii=False), encoding="utf-8")
    return hist


# ---------------------------------------------------------------------------
# 커브
# ---------------------------------------------------------------------------
@dataclass
class Curve:
    """연 1회 파 금리에서 부트스트랩한 할인커브. 1~30년 연 단위 DF를 갖고, 그 사이는 로그 선형 보간."""
    years: np.ndarray   # 0, 1, ..., 30
    dfs: np.ndarray

    @classmethod
    def from_par(cls, par_by_tenor: dict, max_year: int = 30) -> "Curve":
        tenors = np.array(sorted(par_by_tenor))
        rates = np.array([par_by_tenor[t] for t in tenors]) / 100
        years = np.arange(0, max_year + 1)
        par = np.interp(years[1:], tenors, rates)   # 양 끝은 평탄 외삽
        dfs = [1.0]
        for i, c in enumerate(par):
            dfs.append((1 - c * sum(dfs[1:i + 1])) / (1 + c))
        return cls(years=years.astype(float), dfs=np.array(dfs))

    def df(self, t) -> np.ndarray:
        t = np.asarray(t, dtype=float)
        return np.exp(np.interp(t, self.years, np.log(self.dfs)))

    def par_rate(self, maturity: int) -> float:
        k = np.arange(1, maturity + 1)
        return float((1 - self.df(maturity)) / self.df(k).sum())

    def zero_rate(self, t: float) -> float:
        return float(-np.log(self.df(t)) / t)


@dataclass
class Market:
    as_of: str
    jpykrw_spot: float
    krw: Curve            # KRW 할인커브 (USD 담보 기준, KRW CCS)
    jpy: Curve            # JPY 할인커브 (USD 담보 기준, OIS + 베이시스)
    krw_irs: Curve        # SIMM 금리 위험요인용 KRW IRS 커브
    jpy_ois: Curve        # SIMM 금리 위험요인용 JPY OIS 커브
    vol_by_tenor: dict    # JPYKRW ATM 내재변동성 (소수)
    korea_cds: dict       # 한국 국가 CDS (소수)
    rate_hist: dict       # 금리 평행이동 변동성·상관 (fetch_hist). 없으면 빈 dict

    def fx_vol(self, t: float) -> float:
        """만기 t까지의 평균 내재변동성. 최장 만기(10년) 이후는 평탄."""
        tenors = sorted(self.vol_by_tenor)
        return float(np.interp(t, tenors, [self.vol_by_tenor[k] for k in tenors]))


def load(path: Path = SNAPSHOT) -> Market:
    snap = json.loads(path.read_text(encoding="utf-8"))

    def by_int(d):
        return {int(k): v for k, v in d.items()}

    ois, basis = by_int(snap["jpy_ois"]), by_int(snap["jpy_basis_bp"])
    common = sorted(set(ois) & set(basis))
    jpy_vs_sofr = {n: ois[n] + basis[n] / 100 for n in common}
    return Market(
        as_of=snap["as_of"],
        jpykrw_spot=snap["jpykrw_spot"],
        krw=Curve.from_par(by_int(snap["krw_ccs"])),
        jpy=Curve.from_par(jpy_vs_sofr),
        krw_irs=Curve.from_par(by_int(snap["krw_irs"])),
        jpy_ois=Curve.from_par(ois),
        vol_by_tenor={k: v / 100 for k, v in by_int(snap["jpykrw_vol"]).items()},
        korea_cds={k: v / 1e4 for k, v in by_int(snap["korea_cds_bp"]).items()},
        rate_hist=snap.get("hist", {}),
    )


if __name__ == "__main__":
    import sys
    if sys.argv[1:] == ["hist"]:     # python market_data.py hist
        for k, v in fetch_hist().items():
            print(f"{k:>14}: {v}")
        raise SystemExit
    s = fetch()
    print(f"{s['as_of']} 저장: {SNAPSHOT.name}, 누락 {s['missing'] or '없음'}")
    m = load()
    print(f"JPYKRW {m.jpykrw_spot:.4f}")
    print(f"{'년':>4}{'KRW CCS':>9}{'JPY OIS':>9}{'JPY 베이시스':>12}{'KRW IRS':>9}")
    for n in TENORS:
        print(f"{n:>4}{s['krw_ccs'].get(str(n), float('nan')):>9.3f}"
              f"{s['jpy_ois'].get(str(n), float('nan')):>9.3f}"
              f"{s['jpy_basis_bp'].get(str(n), float('nan')):>12.1f}"
              f"{s['krw_irs'].get(str(n), float('nan')):>9.3f}")
    print("JPYKRW vol", s["jpykrw_vol"], "Korea CDS", s["korea_cds_bp"])
