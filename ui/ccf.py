"""여신환산율(은행 PFE 대체 표) 참고 표시 (Run C L-088). 표 값은 로컬 파일에서만 읽는다 (DECISIONS O12: 저장소에 넣지 않는다).

파일: 데이터 폴더(LINEFEE_DATA_DIR, 없으면 data/)의 여신환산율.xlsx, 없으면 저장소 루트의 것 (모두 gitignore).
형식: 머리줄에 '1년 이하' ... '10년 초과' 만기 구간, 행 이름 '금리'·'외환'에 환산율(소수). 파일이 없거나 형식이 다르면 None
(화면은 참고 표시를 하지 않는다). 계산 결과에는 쓰지 않는 참고 지표다.
한도 사용액 = 환산율 x 명목(원화) + max(고객 거래 시가, 0).  통화스왑·FX 선도는 '외환' 행(M2: 통화스왑은 환율 자산군만),
원화 IRS·달러 OIS는 '금리' 행. 만기 구간은 거래 만기(년)가 속한 구간.
"""
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILE_NAME = "여신환산율.xlsx"
ROWS = ("금리", "외환")


def find_file() -> Path | None:
    """LINEFEE_DATA_DIR이 있으면 그 폴더만, 없으면 저장소의 data/, 저장소 루트 순서."""
    d = os.environ.get("LINEFEE_DATA_DIR")
    for p in [Path(d) / FILE_NAME] if d else [ROOT / "data" / FILE_NAME, ROOT / FILE_NAME]:
        if p.exists():
            return p
    return None


def parse_table(rows) -> dict | None:
    """시트의 행 목록(셀 값의 튜플) -> {"bounds": [상한(년)...], "금리": [...], "외환": [...]} 또는 None."""
    bounds, table = None, {}
    for r in rows:
        cells = [c for c in r if c is not None]
        if bounds is None:
            b = [re.fullmatch(r"\s*(\d+)\s*년\s*(이하|초과)\s*", str(c)) for c in cells]
            if len(cells) >= 2 and all(b):
                bounds = [float(m.group(1)) if m.group(2) == "이하" else float("inf") for m in b]
            continue
        if cells and str(cells[0]).strip() in ROWS and len(cells) == len(bounds) + 1:
            try:
                table[str(cells[0]).strip()] = [float(c) for c in cells[1:]]
            except (TypeError, ValueError):
                return None
    if bounds is None or sorted(bounds) != bounds or any(k not in table for k in ROWS):
        return None
    return {"bounds": bounds, **table}


def load_table(path: Path | None = None) -> dict | None:
    """로컬 여신환산율 표. 없거나 읽을 수 없으면 None."""
    p = path or find_file()
    if p is None:
        return None
    try:
        import openpyxl
        wb = openpyxl.load_workbook(p, data_only=True, read_only=True)
        for ws in wb.worksheets:
            t = parse_table(ws.iter_rows(values_only=True))
            if t:
                return t
    except Exception:   # openpyxl 미설치, 깨진 파일 등: 참고 표시만 생략한다 (계산에는 영향 없음)
        pass
    return None


def notional_krw(trade, spot: dict) -> float:
    """거래 명목(원화). 통화스왑·FX 선도는 외화 다리 중 큰 쪽(SA-CCR 조정명목과 같은 방식), 그 밖은 첫 다리. spot: 통화 -> 1단위당 원화."""
    foreign = [l for l in trade.legs if l.ccy != "KRW"]
    legs = foreign if foreign and trade.product != "IRS" else trade.legs[:1]
    return max(l.notional * spot[l.ccy] for l in legs)


def usage(table: dict, product: str, maturity: float, notional_krw: float, v0_client_krw: float) -> dict:
    """한도 사용액(원)과 구성. product: 'IRS'면 금리 행, 그 밖(통화스왑·FX 선도)은 외환 행."""
    row = "금리" if product == "IRS" else "외환"
    i = next(k for k, b in enumerate(table["bounds"]) if maturity <= b + 1e-9)   # 마지막 상한은 inf
    rate = table[row][i]
    pos = max(v0_client_krw, 0.0)
    return dict(row=row, rate=rate, notional=notional_krw, add_on=rate * notional_krw, positive_mv=pos,
                total=rate * notional_krw + pos)
