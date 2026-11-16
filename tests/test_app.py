"""대시보드(AppTest) 확인. 스냅샷 필요. python -m tests.test_app"""
import sys
from pathlib import Path

from streamlit.testing.v1 import AppTest

from linefee.trade import BankParams

FAILS = []


def check(label, ok):
    print(f"  {'ok  ' if ok else '실패'} {label}")
    if not ok:
        FAILS.append(label)


def new_app():
    at = AppTest.from_file(str(Path(__file__).resolve().parent.parent / "app.py"), default_timeout=600)
    at.run()
    if at.exception:
        sys.exit(f"app.py 실행 오류: {at.exception[0].value}")
    return at


def num(at, label):
    return next(w for w in at.number_input if w.label == label)


def test_param_ranges(at):
    print("은행 파라미터 범위 (L-013)")
    tax = num(at, "세율 (%)")
    check("세율 min 0, max 100 미만", tax.min == 0.0 and tax.max is not None and tax.max < 100.0)
    for label in ("CET1 목표비율 (%)", "세후 요구수익률 (%)", "BA-CVA 한계계수", "금리 변동성 배수"):
        check(f"{label} min 0", num(at, label).min == 0.0)
    check("금리 평균회귀(kappa)는 min 없음 (음수 허용, L-010)", num(at, "금리 평균회귀 (연)").min is None)
    check("기본값 유지 (세율 27.5, CET1 13, 요구수익률 10, 한계계수 0.5, 배수 1)",
          (tax.value, num(at, "CET1 목표비율 (%)").value, num(at, "세후 요구수익률 (%)").value,
           num(at, "BA-CVA 한계계수").value, num(at, "금리 변동성 배수").value) == (27.5, 13.0, 10.0, 0.5, 1.0))


def test_validate_params():
    print("BankParams.problems (L-013)")
    check("기본값은 문제 없음", BankParams().problems() == [])
    check("세율 100% 거부", BankParams(tax_rate=1.0).problems() != [])
    check("세율 -1% 거부", BankParams(tax_rate=-0.01).problems() != [])
    check("한계계수 음수 거부", BankParams(ba_cva_marginal_factor=-0.1).problems() != [])
    check("변동성 배수 음수 거부", BankParams(rate_vol_scale=-1.0).problems() != [])
    check("kappa 음수는 검사하지 않음", BankParams(rate_mean_reversion=-0.05).problems() == [])


if __name__ == "__main__":
    try:
        app = new_app()
    except FileNotFoundError as e:   # 스냅샷은 로컬 전용
        sys.exit(str(e))
    test_validate_params()
    test_param_ranges(app)
    print("실패: " + (", ".join(FAILS) if FAILS else "없음"))
    sys.exit(1 if FAILS else 0)
