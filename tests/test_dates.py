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


if __name__ == "__main__":
    test_years_ago()
    test_business_day()
    print("실패: " + (", ".join(FAILS) if FAILS else "없음"))
    sys.exit(1 if FAILS else 0)
