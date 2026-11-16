"""날짜 계산 단정 (블룸버그·스냅샷 없이 실행). python -m tests.test_dates"""
import sys
from datetime import date

from market_data import years_ago

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


if __name__ == "__main__":
    test_years_ago()
    print("실패: " + (", ".join(FAILS) if FAILS else "없음"))
    sys.exit(1 if FAILS else 0)
