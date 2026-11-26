"""날짜 계산 단정 (블룸버그·스냅샷 없이 실행). python -m tests.test_dates"""
import sys
from datetime import date

from linefee.market import check_business_day, prev_business_day
from market_data import prev_weekday, years_ago

FAILS = []


def check(label, got, want):
    ok = got == want
    print(f"  {'ok  ' if ok else '실패'} {label}: {got}" + ("" if ok else f" (기대 {want})"))
    if not ok:
        FAILS.append(label)


def test_years_ago():
    print("5년 구간 시작일 (L-021)")
    check("2028-02-29 - 5년", years_ago(date(2028, 2, 29), 5), date(2023, 2, 28))
    check("2024-02-29 - 4년 (윤일 -> 윤일)", years_ago(date(2024, 2, 29), 4), date(2020, 2, 29))
    check("2026-11-13 - 5년", years_ago(date(2026, 11, 13), 5), date(2021, 11, 13))
    check("2026-03-01 - 5년", years_ago(date(2026, 3, 1), 5), date(2021, 3, 1))


def test_business_day():
    print("as_of 영업일 (L-009)")
    check("토 -> 직전 금", prev_business_day(date(2026, 11, 14)), date(2026, 11, 13))
    check("일 -> 직전 금", prev_business_day(date(2026, 11, 15)), date(2026, 11, 13))
    check("금(영업일) 그대로", prev_business_day(date(2026, 11, 13)), date(2026, 11, 13))
    check("공휴일(2026-10-09 한글날) -> 10-08", prev_business_day(date(2026, 10, 9)), date(2026, 10, 8))
    check("weekday 토", prev_weekday(date(2026, 11, 14)), date(2026, 11, 13))
    check("weekday 일", prev_weekday(date(2026, 11, 15)), date(2026, 11, 13))
    check("weekday 월 그대로", prev_weekday(date(2026, 11, 16)), date(2026, 11, 16))
    check_business_day("2026-11-13")
    try:
        check_business_day("2026-11-14")
        check("토요일 as_of는 오류", "오류 없음", "ValueError")
    except ValueError as e:
        check("토요일 as_of 오류 메시지에 직전 영업일", "2026-11-13" in str(e), True)


def test_spot_date():
    print("현물 결제일 관행 (L-024): T+1은 비USD 통화 영업일, 결제일은 통화쌍 + USD 공통 영업일")
    from linefee import curves as cv

    def sd(as_of, ccys):
        return cv.spot_date(cv.qdate(as_of), ccys).ISO()
    # 추수감사절 전날(수): 미국 휴일 목요일을 T+1로 센다 -> 금요일 (공통 달력 2영업일이면 월요일)
    check("2026-11-25 USDKRW", sd("2026-11-25", ["USD", "KRW"]), "2026-11-27")
    check("2026-11-25 USDJPY", sd("2026-11-25", ["USD", "JPY"]), "2026-11-27")
    check("2026-11-25 JPYKRW", sd("2026-11-25", ["KRW", "JPY"]), "2026-11-27")
    check("2026-11-25 USDKRW 결제 지연 영업일 수", cv.spot_days(cv.qdate("2026-11-25"), ["USD", "KRW"]), 1)
    # 한글날(금) 다음 월요일이 미국 휴일(콜럼버스데이): KRW T+1 = 월, T+2 = 화
    check("2026-10-08 USDKRW", sd("2026-10-08", ["USD", "KRW"]), "2026-10-13")
    # 교차 통화쌍도 결제일이 미국 휴일이면 넘긴다: 2026-07-02(목) JPYKRW T+2 = 07-06(월)
    check("2026-07-02 JPYKRW", sd("2026-07-02", ["KRW", "JPY"]), "2026-07-06")
    check("평일 USDKRW T+2", sd("2026-11-11", ["USD", "KRW"]), "2026-11-13")
    sc = cv.schedule("2026-11-25", "FXFWD", ("KRW", "USD"), 12, 1, "ACT/365")
    check("FX 선도 시작일 = 현물 결제일 (일)", round(sc.start[0] * 365), 2)


def test_krw_extra_holidays():
    print("서울 달력 추가 휴일 (L-025): 임기만료 선거일")
    import QuantLib as ql

    from linefee import curves as cv
    check("2028-04-12 국회의원 선거일 휴일", cv.CALENDARS["KRW"].isBusinessDay(ql.Date(12, 4, 2028)), False)
    check("2030-06-05 지방선거일 휴일", cv.CALENDARS["KRW"].isBusinessDay(ql.Date(5, 6, 2030)), False)
    check("공통 달력(KRW+USD)에도 반영", cv.calendar(["KRW", "USD"]).isBusinessDay(ql.Date(12, 4, 2028)), False)
    check("목록의 모든 날짜가 휴일", all(not cv.CALENDARS["KRW"].isBusinessDay(cv.qdate(d))
                                    for d in cv.KRW_EXTRA_HOLIDAYS), True)


def test_weekly():
    print("과거 추정 주간 시계열 (L-028): 금요일이 휴일이면 그 주 마지막 값, 모든 구간 1주")
    from datetime import timedelta

    import numpy as np

    from linefee.market import friday_grid, weekly_last
    days = [date(2026, 1, 5) + timedelta(days=i) for i in range(70) if (date(2026, 1, 5) + timedelta(days=i)).weekday() < 5]
    a = {d: float(i) for i, d in enumerate(days)}
    b = {d: v for d, v in a.items() if d != date(2026, 1, 30)}          # b만 금요일(1-30) 휴일
    fr = friday_grid([a, b])
    check("금요일 수 = 주 수 (휴일 금요일도 남는다)", len(fr), 10)
    check("모든 간격 1주", {(y - x).days for x, y in zip(fr[:-1], fr[1:])}, {7})
    vb = weekly_last(b, fr)
    check("휴일 금요일은 그 주 목요일 값", vb[fr.index(date(2026, 1, 30))], a[date(2026, 1, 29)])
    check("나머지 금요일은 그날 값", bool(np.all(weekly_last(a, fr) == [a[f] for f in fr])), True)


if __name__ == "__main__":
    test_weekly()
    test_years_ago()
    test_business_day()
    test_spot_date()
    test_krw_extra_holidays()
    print("실패: " + (", ".join(FAILS) if FAILS else "없음"))
    sys.exit(1 if FAILS else 0)
