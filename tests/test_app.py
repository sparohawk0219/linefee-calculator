"""대시보드(AppTest) 확인. 스냅샷 필요. python -m tests.test_app"""
import contextlib
import os
import sys
import tempfile
from pathlib import Path

from streamlit import config as st_config
from streamlit.runtime.secrets import secrets_singleton
from streamlit.testing.v1 import AppTest

from linefee.trade import BankParams

FAILS = []


def check(label, ok):
    print(f"  {'ok  ' if ok else '실패'} {label}")
    if not ok:
        FAILS.append(label)


ADMIN_PW = "test-admin-pw"   # 시험용 값 (실제 비밀번호 아님)
DATA_DIR = tempfile.mkdtemp(prefix="linefee_test_app_")   # 시험마다 비워 쓰는 데이터 디렉터리
os.environ["LINEFEE_DATA_DIR"] = DATA_DIR


@contextlib.contextmanager
def no_secrets_file():
    """비밀값 파일(.streamlit/secrets.toml)이 없는 상태로 격리한다. 이 PC의 파일 유무에 시험이 좌우되지 않게 한다 (L-070)."""
    saved = st_config.get_option("secrets.files")
    st_config.set_option("secrets.files", [])
    secrets_singleton._reset()
    try:
        yield
    finally:
        st_config.set_option("secrets.files", saved)
        secrets_singleton._reset()


def new_app(secret=ADMIN_PW):
    for f in Path(DATA_DIR).glob("*"):   # 저장한 마스터·파라미터가 다음 시험에 새지 않게
        f.unlink()
    at = AppTest.from_file(str(Path(__file__).resolve().parent.parent / "app.py"), default_timeout=600)
    if secret is not None:
        at.secrets["admin_password"] = secret
    at.run()
    if at.exception:
        sys.exit(f"app.py 실행 오류: {at.exception[0].value}")
    return at


def login(at, pw=ADMIN_PW):
    at.text_input(key="admin_pw").set_value(pw).run()
    next(b for b in at.button if b.label == "관리자 로그인").click().run()
    return at


def button(at, label):
    return next(b for b in at.button if b.label == label)


def calc(at):
    button(at, "계산").click().run()
    if at.exception:
        sys.exit(f"계산 중 오류: {at.exception[0].value}")
    return at.session_state["result"]


def num(at, label):
    return next(w for w in at.number_input if w.label == label)


def test_param_ranges():
    print("은행 파라미터 범위 (L-013) - 관리자 화면")
    at = login(new_app())
    check("관리자 로그인", at.session_state["role"] == "admin")
    tax = num(at, "세율 (%)")
    check("은행 파라미터 위젯 key는 adm_로 시작", all(n.key.startswith("adm_") for n in at.number_input
                                                  if n.label in ("세율 (%)", "CET1 목표비율 (%)")))
    check("세율 min 0, max 100 미만", tax.min == 0.0 and tax.max is not None and tax.max < 100.0)
    for label in ("CET1 목표비율 (%)", "세후 요구수익률 (%)", "BA-CVA 한계계수", "금리 변동성 배수"):
        check(f"{label} min 0", num(at, label).min == 0.0)
    check("금리 평균회귀(kappa)는 min 없음 (음수 허용, L-010)", num(at, "금리 평균회귀 (연)").min is None)
    check("기본값 유지 (세율 27.5, CET1 13, 요구수익률 10, 한계계수 0.5, 배수 1)",
          (tax.value, num(at, "CET1 목표비율 (%)").value, num(at, "세후 요구수익률 (%)").value,
           num(at, "BA-CVA 한계계수").value, num(at, "금리 변동성 배수").value) == (27.5, 13.0, 10.0, 0.5, 1.0))


def test_product_roundtrip():
    print("상품 CCS -> IRS -> CCS 두 다리 입력 유지 (L-012)")
    at = new_app()
    before = {k: at.session_state[k] for k in ("cr_amt", "cp_amt", "cr_ccy", "cp_ccy", "cr_rate", "cp_rate")}
    at.radio(key="product").set_value("KRWIRS").run()
    check("IRS 화면 예외 없음", not at.exception)
    at.radio(key="product").set_value("USDOIS").run()
    at.radio(key="product").set_value("CCS").run()
    check("CCS 복귀 예외 없음", not at.exception)
    after = {k: at.session_state[k] for k in before}
    check(f"다리 입력 유지 {before['cr_amt']:.1f} {before['cp_ccy']}", after == before)
    check("상품을 바꾸면 상대방이 비워진다", at.session_state["cl_name"] is None and at.session_state["dl_name"] is None)
    next(b for b in at.button if b.label == "계산").click().run()
    check("상대방을 고르지 않으면 계산하지 않고 안내한다",
          "result" not in at.session_state and any("고객을 고르지" in e.value for e in at.error))
    at.selectbox(key="cl_name").set_value("기업 C").run()
    at.selectbox(key="dl_name").set_value("딜러 B").run()
    next(b for b in at.button if b.label == "계산").click().run()
    check("복귀 후 '계산'이 결과를 만든다", not at.exception and "result" in at.session_state
          and at.session_state["result"] is not None)


def test_roles():
    print("역할 분리 (계약 4, 5)")
    at = new_app()
    keys = [w.key for grp in (at.number_input, at.selectbox, at.radio, at.checkbox, at.button, at.text_input,
                              at.select_slider) for w in grp if w.key]
    check("로그인 전 일반 화면에 adm_ key가 없다", not [k for k in keys if k.startswith("adm_")])
    check("로그인 전 일반 화면에 은행 파라미터 입력이 없다", not [w for w in at.number_input if "CET1" in w.label])
    check("고객·헤지 딜러는 마스터 이름으로 고른다 (cl_name, dl_name)",
          "cl_name" in keys and "dl_name" in keys and not [w for w in at.number_input if "PD" in w.label])
    check("일반 화면의 role은 admin이 아니다", at.session_state["role"] != "admin" if "role" in at.session_state else True)
    login(at, "wrong-password")
    check("틀린 비밀번호는 거부", "role" not in at.session_state or at.session_state["role"] != "admin")
    check("틀린 비밀번호 안내가 보인다", any("맞지 않" in e.value for e in at.error))
    login(at)
    check("맞는 비밀번호로 로그인", at.session_state["role"] == "admin")
    check("로그인하면 adm_ 위젯이 생긴다", any(w.key and w.key.startswith("adm_") for w in at.number_input))
    button(at, "로그아웃").click().run()
    check("로그아웃하면 일반 화면으로", ("role" not in at.session_state or at.session_state["role"] != "admin")
          and not [w for w in at.number_input if w.key and w.key.startswith("adm_")])
    with no_secrets_file():
        nosecret = new_app(secret=None)
        check("비밀값이 없어도 일반 화면이 뜬다", not nosecret.exception)
        login(nosecret)
        check("비밀값이 없으면 로그인을 막고 안내한다",
              ("role" not in nosecret.session_state or nosecret.session_state["role"] != "admin")
              and any("설정되지 않았습니다" in w.value for w in nosecret.warning))


def test_admin_save_changes_general():
    print("관리자가 저장한 은행 파라미터가 일반 화면의 계산에 쓰인다")
    at = login(new_app())
    num(at, "CET1 목표비율 (%)").set_value(14.0).run()
    r0 = calc(at)
    check("저장 전에는 계산에 쓰이지 않는다 (기본 13%)", abs(r0["params"].capital_ratio - 0.13) < 1e-12)
    button(at, "저장").click().run()
    check("저장 성공 안내", any("저장했습니다" in s.value for s in at.success))
    check("bank_params.json이 생긴다", (Path(DATA_DIR) / "bank_params.json").exists())
    button(at, "로그아웃").click().run()
    r1 = calc(at)
    check("일반 화면의 params.capital_ratio가 저장한 값 (14%)", abs(r1["params"].capital_ratio - 0.14) < 1e-12)
    fresh = AppTest.from_file(str(Path(__file__).resolve().parent.parent / "app.py"), default_timeout=600).run()
    check("새 세션도 같은 값", abs(calc(fresh)["params"].capital_ratio - 0.14) < 1e-12)
    bad = login(new_app())
    num(bad, "세율 (%)").set_value(99.9).run()
    num(bad, "CET1 목표비율 (%)").set_value(10.0).run()
    button(bad, "저장").click().run()
    check("범위 안의 값은 저장된다 (세율 99.9%)", (Path(DATA_DIR) / "bank_params.json").exists())


def test_master():
    print("거래상대방 마스터 추가·수정·삭제")
    at = new_app()
    names = list(at.selectbox(key="cl_name").options)
    check("마스터 기본은 가명 샘플 (기업 C 포함, 딜러 B는 고객 목록에 없음)",
          "기업 C" in names and "딜러 B" not in names)
    check("헤지 딜러 목록은 딜러 라인 행", set(at.selectbox(key="dl_name").options) == {"딜러 B", "딜러 D"})
    at = login(at)
    at.text_input(key="adm_m_name").set_value("기업 G")
    at.number_input(key="adm_m_pd").set_value(0.5)
    at.selectbox(key="adm_m_sector").set_value("기술·통신").run()
    button(at, "추가·수정").click().run()
    check("추가하고 저장하기 전에는 고객 목록에 안 나온다", "기업 G" not in at.selectbox(key="cl_name").options)
    button(at, "저장").click().run()
    check("마스터 추가·저장 후 cl_name 선택지에 나온다", "기업 G" in at.selectbox(key="cl_name").options)
    check("counterparties.csv가 생긴다", (Path(DATA_DIR) / "counterparties.csv").exists())
    at.selectbox(key="cl_name").set_value("기업 G").run()
    r = calc(at)
    check("그 고객으로 계산한다", r["deal"].client.name == "기업 G" and abs(r["deal"].client.pd - 0.005) < 1e-12)
    at.selectbox(key="adm_m_pick").set_value("기업 C").run()
    check("기존 상대방을 고르면 값이 채워진다", at.number_input(key="adm_m_pd").value == 0.2)
    at.selectbox(key="adm_m_grade").set_value("투기등급·무등급")
    at.number_input(key="adm_m_pd").set_value(0.8).run()
    button(at, "추가·수정").click().run()
    button(at, "저장").click().run()
    at.selectbox(key="cl_name").set_value("기업 C").run()
    c = calc(at)["deal"].client
    check("수정한 등급·PD가 계산에 쓰인다 (PD 0.8%)", abs(c.pd - 0.008) < 1e-12)
    at.selectbox(key="adm_m_pick").set_value("기업 G").run()
    button(at, "삭제").click().run()
    button(at, "저장").click().run()
    check("삭제 후 고객 목록에서 사라진다", "기업 G" not in at.selectbox(key="cl_name").options)
    at.text_input(key="adm_m_name").set_value("").run()
    button(at, "추가·수정").click().run()
    check("이름 없는 행은 거부", any("이름" in e.value for e in at.error))


def test_master_rename():
    print("관리자: 이름 변경 시 열 승계, RW 방식·매출·VM 통화 입력 (L-053, L-057)")
    new_app()   # 데이터 폴더를 비운다
    seed = ("name,grade,pd_pct,csa,dealer_line,credit_rw_pct,ba_cva_sector,vm_ccy,rw_method,sales_eok,note\n"
            "기업 I,투자등급,0.5,VM,N,0,기술·통신,,IRB,1200,비고I\n"
            "딜러 K,투자등급,0.0,VM+IM,Y,20,금융기관(정부보증 금융 포함),KRW,고정,,\n")
    (Path(DATA_DIR) / "counterparties.csv").write_text(seed, encoding="utf-8-sig")
    at = AppTest.from_file(str(Path(__file__).resolve().parent.parent / "app.py"), default_timeout=600)
    at.secrets["admin_password"] = ADMIN_PW
    at = login(at.run())
    at.selectbox(key="adm_m_pick").set_value("기업 I").run()
    check("IRB 행을 고르면 RW 방식·매출이 채워진다", at.selectbox(key="adm_m_rwm").value == "IRB"
          and at.text_input(key="adm_m_sales").value == "1200")
    at.text_input(key="adm_m_name").set_value("기업 I2").run()
    button(at, "추가·수정").click().run()
    button(at, "저장").click().run()
    rows = {r.split(",")[0]: r.split(",") for r in (Path(DATA_DIR) / "counterparties.csv").read_text(
        encoding="utf-8-sig").splitlines()[1:]}
    check("이름을 바꾸면 옛 행은 교체되고 새 행이 IRB·매출·비고를 이어받는다",
          "기업 I" not in rows and rows["기업 I2"][8] == "IRB" and rows["기업 I2"][9] == "1200"
          and rows["기업 I2"][-1] == "비고I")
    at.selectbox(key="adm_m_pick").set_value("딜러 K").run()
    check("KRW VM 딜러를 고르면 VM 통화가 채워진다", at.selectbox(key="adm_m_vm").value == "KRW")
    at.selectbox(key="adm_m_pick").set_value("기업 I2").run()
    at.text_input(key="adm_m_sales").set_value("abc").run()
    button(at, "추가·수정").click().run()
    check("매출이 숫자가 아니면 거부", any("매출" in e.value for e in at.error))
    at.text_input(key="adm_m_sales").set_value("1200")
    at.text_input(key="adm_m_name").set_value("딜러 K").run()
    button(at, "추가·수정").click().run()
    check("다른 행과 같은 이름으로 바꾸면 거부", any("이미 있습니다" in e.value for e in at.error))


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
        new_app()
    except FileNotFoundError as e:   # 스냅샷은 로컬 전용
        sys.exit(str(e))
    test_validate_params()
    test_product_roundtrip()
    test_param_ranges()
    test_roles()
    test_admin_save_changes_general()
    test_master()
    test_master_rename()
    print("실패: " + (", ".join(FAILS) if FAILS else "없음"))
    sys.exit(1 if FAILS else 0)
