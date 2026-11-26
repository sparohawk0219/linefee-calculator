"""상대방 마스터(counterparties.csv) 생성 규칙 (DECISIONS O12).

규칙(PD 열 선택, 옛 등급 정정, 외부등급 RW, 32조 공공기관, 딜러·금융기관 CSA, 같은 법인 묶음, 업종 키워드, 꼬리표)은
이 모듈에 있고, 이름별 표와 실데이터 입력 경로는 규칙 파일(JSON)에서 읽는다. 실데이터와 실제 규칙 파일은 로컬 전용이다.
예시(가명) 규칙 파일: tests/master_rules.example.json

사용: python -m ui.master_build --rules local/data/master_rules.json --out <출력 csv>
      (--out을 생략하면 데이터 폴더의 counterparties.csv를 덮어쓴다)
규칙 파일 키:
  inputs: pd_xlsx(PD표 엑셀, 시트 'PD정보'), dealers, fi_ratings, customers (csv 경로; 상대경로는 --root 기준)
  pd_overrides: {"등급|열": PD(소수)}   M19 표 오타 정정
  fi_csa: {고객 id: [CSA, VM통화]}      딜러가 아닌 금융기관 고객의 ISDA CSA
  alias: {고객 파일 표기: 딜러 행 이름}  같은 법인 묶음 (L-045)
  public: {이름: [구분, RW%, 확인 여부, 근거]}, public_fin: [이름], public_name_hint: {이름: 고객 파일 검색어}
  exclude: {이름: 사유}, extra_rows: [행 dict]
"""
import argparse
import collections
import csv
import json
import re
from pathlib import Path

FIN = "금융기관(정부보증 금융 포함)"
PUB = "지방정부·정부보증 비금융·공공기관"
MAT = "소재·에너지·산업재·농업·제조·광업"
CON = "소비재·운송·보관·사무지원"
TEC = "기술·통신"
HLT = "보건·유틸리티·전문기술서비스"
OTH = "기타"
GOV = "국가·중앙은행·MDB"
COLS_OUT = ["name", "grade", "pd_pct", "csa", "dealer_line", "credit_rw_pct", "ba_cva_sector", "vm_ccy", "rw_method", "sales_eok"]
COARSE = {"AA": "AA2", "A+": "A+2", "A-": "A-2"}   # M16b: 옛 등급(세분 없음) -> 세분 등급 대체, 꼬리표로 갱신 필요 표시
IG = ["AAA", "AA", "AA1", "AA2", "AA3", "A+", "A+1", "A+2", "A+3", "A-", "A-1", "A-2", "A-3", "BBB+", "BBB", "BBB-"]
PUB_COL = "공공및비영리단체"
# M18 업종 키워드: 위에서부터 처음 맞는 업종
SECTOR_RULES = [
    (FIN, r"증권|금융|카드|할부|리스업|공제|보험|신용조합|투자기관|투자업"),
    (TEC, r"통신|컴퓨터|전자|디스플레이|다이오드|데이터베이스|방송|텔레비전"),
    (HLT, r"의약|송전|배전|엔지니어링|전문 서비스|컨설팅"),
    (OTH, r"부동산"),   # L-044: 부동산업은 업종표에 없어 기타
    (CON, r"운송|화물|운수|항공|호텔|콘도|골프|의복|봉제|편조|셔츠|의류|액세서리|겉옷|도매|도소매|판매|식당|설탕|가금류|곡물|위생용|장비 임대|장비임대"),
    (MAT, r"제조|건설|건조|중공업|철강|제철|강관|강재|토공|레미콘|원료|화학|섬유|스테인리스|부품"),
]


def sector(biz) -> str:
    b = biz or ""
    for s, pat in SECTOR_RULES:
        if re.search(pat, b):
            return s
    return OTH


def load_pd_table(path, overrides=None) -> dict:
    """PD표 엑셀('PD정보' 시트, 3행 머리글, 열 C~I) -> {(등급, 열 이름): PD(소수)}."""
    import openpyxl
    ws = openpyxl.load_workbook(path, data_only=True)["PD정보"]
    cols = [c.value for c in ws[3][2:9]]
    pd = {(r[1].value, cols[j]): r[2 + j].value for r in ws.iter_rows(min_row=4) if r[1].value for j in range(7)}
    for key, v in (overrides or {}).items():   # M19 오타 정정. 키 "등급|열"
        g, c = key.split("|")
        pd[(g, c)] = v
    return pd


def ext_rw(std: str, art: str) -> float:
    """금융기관 고객 외부등급 RW(%) (M16a): 35조 은행·37조 기업."""
    if not std:
        return 100.0 if art == "37" else 75.0      # 무등급: 37조 100%, 35조 실사등급 B(75%) 가정
    g = std.rstrip("+-")
    bank = {"AAA": 20, "AA": 20, "A": 30, "BBB": 50, "BB": 100, "B": 100}
    corp = {"AAA": 20, "AA": 20, "A": 50, "BBB": 75, "BB": 100}
    return float((bank if art == "35" else corp).get(g, 150))


def dealer_csa(d: dict) -> str:
    """L-040: 헤지 딜러 CSA는 ISDA 실제 계약. VM CSA가 없으면 무담보('없음') + 이름 꼬리표."""
    c = d["csa"].replace(" ", "")
    return c if c in ("VM", "VM+IM") else "없음"


def build(pd: dict, dealers: list, fi: dict, customers: list, rules: dict):
    """규칙으로 마스터 행 목록을 만든다. 반환: (행 목록, 제외 목록, 공공기관 내부등급)."""
    fi_csa = {k: tuple(v) for k, v in rules.get("fi_csa", {}).items()}
    alias = rules.get("alias", {})
    public = {k: tuple(v) for k, v in rules.get("public", {}).items()}
    public_fin = set(rules.get("public_fin", []))
    hints = rules.get("public_name_hint", {})
    exclude = rules.get("exclude", {})
    rows = []

    def add(**r):
        r.setdefault("vm_ccy", "")
        r.setdefault("rw_method", "고정")
        r.setdefault("sales_eok", "")
        rows.append(r)

    dnames = {d["name"] for d in dealers}
    dbrr = {d["name"]: d["brr"] for d in dealers}
    dcsa_by = {d["name"]: (dealer_csa(d), d["vm_ccy"]) for d in dealers}
    # 1) 헤지 딜러: 일괄 RW 20% (M17), PD LDP 열 (M17a)
    for d in dealers:
        add(name=d["name"] + (" [VM CSA 없음]" if dealer_csa(d) == "없음" else ""),
            grade="투자등급" if d["brr"] in IG else "투기등급·무등급",
            pd_pct=round(pd[(d["brr"], "LDP")] * 100, 4), csa=dealer_csa(d), dealer_line="Y", credit_rw_pct=20,
            ba_cva_sector=FIN, vm_ccy=d["vm_ccy"])

    pub_brr = {}
    for c in customers:
        for k in public:
            if hints.get(k, k) in c["name"] or c["name"] in k:
                pub_brr[k] = (c["BRR"], c["audit"])
    skipped = []
    for c in customers:
        nm, brr, aud = c["name"], c["BRR"], c["audit"]
        if nm in exclude or "사용X" in nm:
            skipped.append((nm, exclude.get(nm, "사용X 표기")))
            continue
        if any(k in nm or nm in k for k in public):
            continue
        if nm in alias:   # L-045: 표기만 다른 같은 법인은 딜러 행 이름 + (고객), 등급은 딜러 행(최신 내부등급)
            nm = alias[nm]
            brr = dbrr[nm]
        name = nm + (" (고객)" if nm in dnames else "")
        if c["id"] in fi:
            f = fi[c["id"]]
            g = COARSE.get(brr, brr)
            p = pd.get((g, "LDP")) or pd.get((brr, "금융기관"))
            fcsa, fccy = dcsa_by.get(nm) or fi_csa.get(c["id"], ("없음", ""))   # L-041: ISDA 실제 계약
            add(name=name + (" [외부등급 없음]" if not f["standard"] else "") + (" [등급 갱신 필요]" if brr in COARSE else ""),
                grade="투자등급" if brr in IG else "투기등급·무등급", pd_pct=round(p * 100, 4), csa=fcsa,
                dealer_line="N", credit_rw_pct=ext_rw(f["standard"], f["article"]), ba_cva_sector=FIN, vm_ccy=fccy)
            continue
        if not brr:
            skipped.append((nm, "등급 없음"))
            continue
        col = aud if aud in ("외감", "비외감", "LDP", "일반기업") else "일반기업"   # M19: 감사 구분 열
        tag, g = "", brr
        if brr in COARSE:
            g, tag = COARSE[brr], " [등급 갱신 필요]"
        if brr == "D":
            tag = " [부도 등급]"
        p = 1.0 if brr == "D" else pd.get((g, col))
        if p is None:
            skipped.append((nm, f"PD 없음 {g}/{col}"))
            continue
        sales = float(c["sales amount"] or 0) / 100      # 백만원 -> 억원
        add(name=name + tag, grade="투자등급" if brr in IG else "투기등급·무등급", pd_pct=round(p * 100, 4), csa="없음",
            dealer_line="N", credit_rw_pct=0, ba_cva_sector=FIN if "선박투자회사" in nm else sector(c["business type"]),
            rw_method="IRB", sales_eok=round(sales, 2) if sales > 0 else "")

    for k, (cls, rw, ok, _why) in public.items():
        # PD (M21): 정부 조직·'가'(정부 손실보전)는 보증인 대한민국으로 대체(공공 열 AAA), 그 밖에는 감사 구분 열(M19,
        # LDP는 세분 등급), 내부등급이 없으면 공공 열 AA
        brr, aud = pub_brr.get(k, ("", ""))
        if cls in ("정부", "가"):
            p = pd[("AAA", PUB_COL)]
        elif brr and aud == "LDP":
            p = pd[(brr, "LDP")]
        else:
            g = brr.rstrip("123") if brr else "AA"
            p = pd.get((g, PUB_COL)) or pd[("AA", PUB_COL)]
        add(name=k + ("" if ok else " [32조 구분 확인 필요]"), grade="투자등급", pd_pct=round(p * 100, 4), csa="없음",
            dealer_line="N", credit_rw_pct=rw, ba_cva_sector=GOV if cls == "정부" else FIN if k in public_fin else PUB)

    for r in rules.get("extra_rows", []):
        add(**r)
    names = [r["name"] for r in rows]
    assert len(names) == len(set(names)), [n for n in names if names.count(n) > 1]
    return rows, skipped, pub_brr


def read_csv(path, enc="utf-8") -> list:
    with open(path, encoding=enc, newline="") as f:
        return list(csv.DictReader(f))


def build_from_rules(rules_path, root=None):
    rules = json.loads(Path(rules_path).read_text(encoding="utf-8"))
    base = Path(root) if root else Path.cwd()
    inp = {k: base / v for k, v in rules["inputs"].items()}
    pd = load_pd_table(inp["pd_xlsx"], rules.get("pd_overrides"))
    dealers = read_csv(inp["dealers"])
    fi = {r["id"]: r for r in read_csv(inp["fi_ratings"])}
    customers = read_csv(inp["customers"], "utf-8-sig")
    return build(pd, dealers, fi, customers, rules)


def write_master(rows, out):
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS_OUT)
        w.writeheader()
        w.writerows(rows)


def main():
    ap = argparse.ArgumentParser(description="규칙 파일로 상대방 마스터 생성")
    ap.add_argument("--rules", required=True, help="규칙 JSON (예시: tests/master_rules.example.json)")
    ap.add_argument("--root", help="규칙 파일 inputs 상대경로의 기준 폴더 (기본: 현재 폴더)")
    ap.add_argument("--out", help="출력 csv (기본: 데이터 폴더의 counterparties.csv)")
    a = ap.parse_args()
    from ui.store import master_path
    out = Path(a.out) if a.out else master_path()
    rows, skipped, _pub = build_from_rules(a.rules, a.root)
    write_master(rows, out)
    print(len(rows), "행 ->", out, dict(collections.Counter((r["dealer_line"], r["rw_method"]) for r in rows)))
    print("태그", dict(collections.Counter(re.findall(r"\[(.*?)\]", r["name"])[0] for r in rows if "[" in r["name"])))
    print("제외", len(skipped), "건")


if __name__ == "__main__":
    main()
