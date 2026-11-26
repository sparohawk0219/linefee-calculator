"""마스터 생성 규칙 단정 (가명 픽스처, 실데이터 없이). python -m tests.test_master_build"""
import json
from pathlib import Path

from tests._check import Checker
from ui import master_build as mb

c = Checker()
PUBC = "공공및비영리단체"
PD = {}
for g, v in {"AAA": 0.0003, "AA": 0.0003, "AA2": 0.0004, "A+2": 0.0010, "A-2": 0.0030, "BBB-": 0.01462}.items():
    for col in ("외감", "비외감", "일반기업", "LDP", "금융기관", PUBC):
        PD[(g, col)] = v
dealers = [dict(name="딜러 B", brr="AA2", csa="VM+IM", vm_ccy="USD"), dict(name="딜러 D", brr="A+2", csa="", vm_ccy="")]
fi = {"F1": dict(id="F1", standard="A+", article="35"), "F2": dict(id="F2", standard="", article="37")}


def cust(id, name, brr, aud="외감", biz="", sales=""):
    return {"id": id, "name": name, "BRR": brr, "audit": aud, "business type": biz, "sales amount": sales}


customers = [
    cust("F1", "금융기관 F", "AA"), cust("F2", "금융기관 G", "A-2"),
    cust("1", "딜러 B 표기다름", "A+2"),
    cust("2", "제조 기업 H", "A+", biz="자동차 부품 제조업", sales="1234500"),
    cust("3", "부동산 기업 I", "BBB-", biz="부동산 임대업"), cust("4", "부도 기업 J", "D", biz="건설업"),
    cust("5", "시험 레코드", "AA"), cust("6", "구 기업 사용X", "AA"), cust("7", "등급 없는 기업", ""),
    cust("8", "공공기관 A", "AA", "비외감"), cust("9", "공공기관 E", "AA3", "비외감"),
]
rules = json.loads(Path("docs/master_rules.example.json").read_text(encoding="utf-8"))
rows, skipped, _ = mb.build(PD, dealers, fi, customers, rules)
by = {r["name"]: r for r in rows}

c.ok("딜러 VM+IM 그대로, RW 20, 딜러선 Y", by["딜러 B"]["csa"] == "VM+IM" and by["딜러 B"]["credit_rw_pct"] == 20
     and by["딜러 B"]["dealer_line"] == "Y")
c.ok("VM CSA 없는 딜러는 무담보 + 꼬리표", "딜러 D [VM CSA 없음]" in by and by["딜러 D [VM CSA 없음]"]["csa"] == "없음")
c.ok("같은 법인 묶음: 딜러 이름 + (고객), 딜러 등급(AA2) PD", "딜러 B (고객)" in by and by["딜러 B (고객)"]["pd_pct"] == 0.04)
g = by["금융기관 F [등급 갱신 필요]"]
c.ok("금융기관: 옛 등급 AA는 AA2 PD(LDP), 35조 A 등급 RW 30, 실제 CSA(VM, USD)",
     g["pd_pct"] == 0.04 and g["credit_rw_pct"] == 30.0 and g["csa"] == "VM" and g["vm_ccy"] == "USD")
c.ok("금융기관 무등급 37조: RW 100, CSA 없음 (표에 없는 id), 꼬리표", by["금융기관 G [외부등급 없음]"]["credit_rw_pct"] == 100.0
     and by["금융기관 G [외부등급 없음]"]["csa"] == "없음")
h = by["제조 기업 H [등급 갱신 필요]"]
c.ok("기업: IRB, 매출 백만원->억원, 업종 키워드 제조", h["rw_method"] == "IRB" and h["sales_eok"] == 12345.0
     and h["ba_cva_sector"] == mb.MAT)
c.ok("부동산업은 기타, BBB- 투자등급 PD는 정정값(1.462%)", by["부동산 기업 I"]["ba_cva_sector"] == mb.OTH
     and by["부동산 기업 I"]["pd_pct"] == 1.462)
c.ok("부도 등급: PD 100%, 꼬리표", by["부도 기업 J [부도 등급]"]["pd_pct"] == 100.0)
c.ok("제외 목록: 시험·사용X·등급 없음", {n for n, _ in skipped} == {"시험 레코드", "구 기업 사용X", "등급 없는 기업"})
a = by["공공기관 A"]
c.ok("공공기관 '가': RW 0, PD는 공공 열 AAA 대체, 업종 금융(정부 손실보전 금융)",
     a["credit_rw_pct"] == 0 and a["pd_pct"] == 0.03 and a["ba_cva_sector"] == mb.FIN)
e = by["공공기관 E [32조 구분 확인 필요]"]
c.ok("공공기관 '나' 미확인: RW 20, 꼬리표, 업종 공공", e["credit_rw_pct"] == 20 and e["ba_cva_sector"] == mb.PUB)
c.ok("공공기관은 일반 기업 행으로 중복되지 않음", sum(1 for r in rows if r["name"].startswith("공공기관")) == 2)
c.ok("ext_rw: 35조 BBB 50, 37조 A 50, 낮은 등급 150", mb.ext_rw("BBB-", "35") == 50 and mb.ext_rw("A+", "37") == 50
     and mb.ext_rw("CCC", "35") == 150)
c.finish()
