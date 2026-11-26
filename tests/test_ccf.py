"""여신환산율 참고 표 읽기·한도 사용액 단정 (가명 값, 합성 표). python -m tests.test_ccf"""
from types import SimpleNamespace as NS

from tests._check import Checker
from ui import ccf

c = Checker()
HEAD = [None, "1년 이하", "2년 이하", "5년 이하", "10년 이하", "10년 초과"]
T = ccf.parse_table([(None, "표"), tuple(HEAD), (None, "금리", 0.01, 0.02, 0.03, 0.04, 0.05),
                     (None, "외환", 0.1, 0.2, 0.3, 0.4, 0.5), (None, "주식", 0, 0, 0, 0, 0)])
c.ok("표를 읽는다", T is not None and T["bounds"] == [1, 2, 5, 10, float("inf")] and T["외환"][2] == 0.3)
c.ok("행이 모자라면 None", ccf.parse_table([tuple(HEAD), (None, "금리", 1, 2, 3, 4, 5)]) is None)
c.ok("머리줄이 없으면 None", ccf.parse_table([(None, "금리", 1, 2, 3)]) is None)
c.ok("숫자가 아니면 None", ccf.parse_table([tuple(HEAD), (None, "금리", "x", 2, 3, 4, 5), (None, "외환", 1, 2, 3, 4, 5)]) is None)
c.ok("파일이 없으면 None", ccf.load_table(ccf.ROOT / "없는파일.xlsx") is None)
u = ccf.usage(T, "CCS", 5.0, 1000.0, 50.0)
c.ok("5년은 5년 이하 구간, 외환: 0.3 x 명목 + 양의 시가", u["row"] == "외환" and u["rate"] == 0.3 and u["total"] == 350.0)
c.ok("5.01년은 10년 이하 구간", ccf.usage(T, "CCS", 5.01, 1000.0, 0)["rate"] == 0.4)
c.ok("10년 초과 구간", ccf.usage(T, "FXFWD", 30.0, 1000.0, 0)["rate"] == 0.5)
c.ok("IRS는 금리 행, 음수 시가는 0", ccf.usage(T, "IRS", 1.0, 1000.0, -70.0)["total"] == 10.0)
tr = NS(product="CCS", legs=[NS(ccy="USD", notional=10.0), NS(ccy="KRW", notional=900.0)])
c.ok("통화스왑 명목은 외화 다리 x 환율", ccf.notional_krw(tr, {"USD": 100.0, "KRW": 1.0}) == 1000.0)
c.ok("IRS 명목은 첫 다리", ccf.notional_krw(NS(product="IRS", legs=[NS(ccy="KRW", notional=500.0)]), {"KRW": 1.0}) == 500.0)
c.finish()
