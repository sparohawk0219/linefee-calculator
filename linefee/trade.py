"""거래·상대방·은행 파라미터 입력 모델.

방향은 모두 은행 기준이다. receive = 은행이 받는 다리, pay = 은행이 내는 다리.
고객 거래와 헤지 거래를 각각 Trade로 둔다. 헤지는 기본이 고객 거래의 미러(방향만 반대)이고,
직접 입력하면 쿠폰·명목이 달라도 된다.
"""
from dataclasses import dataclass, field, replace

# BA-CVA 위험가중치 (시행세칙 별표3 313.(2), CRE50.16). (투자등급, 투기등급·무등급)
BA_CVA_RW = {
    "국가·중앙은행·MDB": (0.005, 0.020),
    "지방정부·정부보증 비금융·공공기관": (0.010, 0.040),
    "금융기관(정부보증 금융 포함)": (0.050, 0.120),
    "소재·에너지·산업재·농업·제조·광업": (0.030, 0.070),
    "소비재·운송·보관·사무지원": (0.030, 0.085),
    "기술·통신": (0.020, 0.055),
    "보건·유틸리티·전문기술서비스": (0.015, 0.050),
    "기타": (0.050, 0.120),
}


@dataclass
class Leg:
    ccy: str                     # KRW, USD, JPY
    notional: float              # 해당 통화 금액 (예: 100억엔 = 1e10)
    direction: str               # "receive" | "pay" (은행 기준)
    kind: str = "fixed"          # "fixed" | "float"
    rate: float = 0.0            # fixed: 연 금리, float: 가산 스프레드 (소수)
    freq: int = 1                # 연 지급 횟수 (지급일은 MarketData.schedule: 실제 영업일·결제일 기준)
    day_count: str = "ACT/365"   # 이자 일수 계산: "ACT/365"(ACT/365F) | "ACT/360"
    final_exchange: bool = True  # 만기 원금 교환. 거래일 원금 교환은 현물 결제로 보고 제외
    discount: str = "csa"        # 할인 커브 (MarketData.disc_for): "csa" USD 현금 담보, "krw" KRW 현금 담보(CD 커브),
                                 # "local" 자국 통화 담보. 담보 상대방 거래는 build_deal이 상대방 CSA 통화로 정한다 (M24)

    @property
    def sign(self) -> float:
        return 1.0 if self.direction == "receive" else -1.0

    def flipped(self) -> "Leg":
        return replace(self, direction="pay" if self.direction == "receive" else "receive")


@dataclass
class Trade:
    legs: list
    maturity: float              # 년 (개월 단위로 반올림해 시작일 + 만기로 만기일을 잡는다)
    product: str = "CCS"         # "CCS" | "FXFWD" | "IRS" (원화 IRS·달러 OIS, 같은 통화 두 다리)

    def mirrored(self) -> "Trade":
        return replace(self, legs=[leg.flipped() for leg in self.legs])

    def leg(self, direction: str) -> int:
        """해당 방향 다리의 위치 (2다리 거래 가정)."""
        return next(i for i, l in enumerate(self.legs) if l.direction == direction)

    def bumped(self, idx: int, delta: float) -> "Trade":
        """idx 다리의 금리(고정) 또는 스프레드(변동)를 delta만큼 바꾼 거래."""
        legs = list(self.legs)
        legs[idx] = replace(legs[idx], rate=legs[idx].rate + delta)
        return replace(self, legs=legs)

    def favoured(self, side: str, delta: float) -> "Trade":
        """은행에 유리하게 delta만큼 조건을 바꾼 거래 (라인피 반영, 손익분기 조정에 쓴다).

        통화스왑: side 다리의 금리(변동이면 스프레드)를 바꾼다. delta는 연 소수 (10bp = 0.001)
                  side="receive"면 올리고, "pay"면 내린다.
        FX 선도: KRW 다리 금액을 delta(원/외화 1단위) x 외화 명목만큼 바꾼다 (선도환율 마진).
                 KRW를 받으면 늘리고, 내면 줄인다. side는 쓰지 않는다.
        """
        if self.product == "FXFWD":
            k = next((i for i, l in enumerate(self.legs) if l.ccy == "KRW"), None)
            if k is None:
                raise ValueError("FX 선도 마진은 KRW 다리가 있는 거래만 지원")
            fx_leg = next(l for l in self.legs if l.ccy != "KRW")
            legs = list(self.legs)
            sgn = 1.0 if legs[k].direction == "receive" else -1.0
            legs[k] = replace(legs[k], notional=legs[k].notional + sgn * delta * fx_leg.notional)
            return replace(self, legs=legs)
        return self.bumped(self.leg(side), delta if side == "receive" else -delta)

    @property
    def currencies(self) -> set:
        return {l.ccy for l in self.legs}


# 적격 CCP에 대한 청산회원 거래익스포져의 SA-CCR 최소 리스크 담보기간 (영업일). 별표3 296.사: 장외파생상품은 10영업일
# (267.카.(2)의 중앙청산거래 5영업일에 대한 특칙). CCP IM 보유기간(BankParams.ccp_im_mpor_days)과는 다른 값이다.
CCP_REG_MPOR_DAYS = 10


@dataclass
class CSA:
    vm: bool = False             # VM 일일 교환 (임계치 0, 최소이전금액 0, 정산 지연 0 가정)
    im: bool = False             # IM 양방향 (SIMM 단독, 임계치 없음)
    reg_mpor_days: int = 10      # SA-CCR 규제 MPOR
    im_haircut: float = 0.04     # 받은 IM(채권) 담보차감률 (NICA에 반영)
    ccy: str = "USD"             # VM 담보 통화 (조달 스프레드 선택)
    ccp: bool = False            # 적격 CCP: 은행이 IM 일방 납부(VaR 근사), CVA 자본 면제, 신용 RW 2%


@dataclass
class Counterparty:
    name: str
    credit_rw: float             # 신용위험 위험가중치
    cva_rw: float                # BA-CVA 위험가중치
    pd: float = 0.0              # 연 부도율 (E/L용, 기본내부등급법이면 RW에도)
    lgd: float = 0.60
    csa: CSA = field(default_factory=CSA)
    irb: bool = False            # True면 credit_rw 대신 기본내부등급법 RW (별표3 120, PD 하한 0.05%, 감독 LGD)
    irb_lgd: float = 0.40        # 기본내부등급법 감독 LGD: 기업 40%, 금융기관 45% (별표3 124.나)
    sales_eok: float | None = None   # 연 매출액 (억원). 700억 이하면 중소기업 상관계수 조정 (별표3 120.나)
    interbank: bool = False      # 은행간(금융기관·CCP) 상대방. 은행 관리회계 E/L 비교(bank_el, M30)에서 30%만 부과. 라인피 계산에는 안 쓴다


def untradable(cp: "Counterparty") -> str | None:
    """거래불가 사유 (없으면 None). 부도 등급(PD 100%) 상대방은 라인피를 계산하지 않는다 (DECISIONS M25, Run C L-060).

    PD 100%에서는 E/L이 시간 격자에 따라 바뀌는 인공값이 되고 기본내부등급법 RW가 0이라 라인피가 PD 2~11% 상대방보다 낮게 나온다.
    """
    if cp is not None and cp.pd >= 1.0:
        return f"{cp.name}: 부도 등급(PD 100%) 상대방은 거래불가입니다 (라인피를 계산하지 않음, DECISIONS M25)"
    return None


@dataclass
class BankParams:
    capital_ratio: float = 0.13          # CET1 목표비율
    hurdle_after_tax: float = 0.10
    tax_rate: float = 0.275
    ftp_spread: float = 0.0012           # USD 현금 담보 조달 FTP - CSA 이자(SOFR)
    ftp_spread_krw: float = 0.0012       # KRW 현금 담보 조달: KRW FTP - KRW 담보 이자. VM 담보 통화가 KRW인 상대(CCP 원화 IRS, KRW CSA 양자 딜러)의 FVA
                                         # (확인 필요: 임시로 USD와 같게)
    kva_funding_spread: float = 0.0010   # KVA 조달금리 = CD(원화 IRS) 커브 선도 + alpha. alpha 기본 10bp
    kva_funding: str = "krw_irs"         # KVA r(t)의 KRW 기준 커브: "krw_irs"(CD·IRS, 확정) | "krw_ccs"(USD 담보 KRW, 비교용)
    ccp_im_mpor_days: int = 5            # CCP IM 보유기간 (영업일)
    ccp_im_conf: float = 0.99            # CCP IM 신뢰수준
    recognise_fba: bool = True
    ba_cva_marginal_factor: float = 0.5  # 은행 CVA 포트폴리오 대비 증분 (완전 분산 시 rho = 0.5)
    ba_cva_ds: float = 0.65
    ba_cva_alpha: float = 1.4
    im_funding_spread: float = 0.0       # 낸 IM 조달 비용 (MVA)
    # 운영위험 자본 (신 표준방법 근사, M28): 첫날 이익 D가 영업지수 BI의 금융 요소에 op_risk_years년 동안 D / op_risk_years씩 들어간다.
    # 추가 운영위험 자본 = BI 한계계수(BIC) x ILM x D / op_risk_years. KVA에 (h - r)로 반영 ("KVA (운영위험)")
    op_risk_bic: float = 0.15            # BI 한계계수 (신 표준방법 BIC 구간 계수. 기본 15%, 0이면 미반영)
    op_risk_ilm: float = 1.0             # 내부손실승수 ILM
    op_risk_years: int = 3               # BI 평균 기간 (년)
    processing_cost_krw: float = 0.0     # 건당 처리·관리 비용 (원, 현재가치). "처리 비용" 항목으로 순마진에서 뺀다
    simm_version: str = "2.8+2512"
    rate_vol_scale: float = 1.0
    rate_mean_reversion: float | None = None   # 금리 평균회귀 kappa. None = 스냅샷 과거 추정치(없으면 0), 값 = 관리자 입력 (M26)
    n_paths: int = 10000                 # 몬테카를로 경로 수 기본값 (DECISIONS M29). 5,000은 시드 변동 0.2~0.3bp
    dt: float = 0.25                     # 시뮬레이션 최대 간격 (년)
    min_steps: int = 24                  # 최소 구간 수 (단기 거래의 적분 정확도)
    antithetic: bool = True              # 대조 난수 (경로 i와 i + n/2가 부호만 반대)
    seed: int = 11

    def problems(self) -> list[str]:
        """파라미터 범위 위반 목록 (앱 입력 위젯의 min/max와 같은 조건). 금리 평균회귀 kappa는 음수를 허용하므로 검사하지 않는다."""
        errs = []
        if not 0.0 <= self.tax_rate < 1.0:
            errs.append("세율은 0% 이상 100% 미만")
        for name, v in (("CET1 목표비율", self.capital_ratio), ("세후 요구수익률", self.hurdle_after_tax),
                        ("BA-CVA 한계계수", self.ba_cva_marginal_factor), ("금리 변동성 배수", self.rate_vol_scale),
                        ("운영위험 BIC", self.op_risk_bic), ("운영위험 ILM", self.op_risk_ilm),
                        ("건당 처리 비용", self.processing_cost_krw)):
            if v < 0.0:
                errs.append(f"{name}은 0 이상")
        if self.op_risk_years < 1:
            errs.append("운영위험 BI 평균 기간은 1년 이상")
        return errs

    def ftp_for(self, ccy: str) -> float:
        """담보 통화별 조달 스프레드."""
        return self.ftp_spread_krw if ccy == "KRW" else self.ftp_spread

    @property
    def cost_of_capital(self) -> float:
        """세전 요구수익률 = 세후 / (1 - 세율)."""
        return self.hurdle_after_tax / (1 - self.tax_rate)


@dataclass
class Deal:
    client_trade: Trade          # 은행 vs 고객 (수수료 반영 후 조건)
    hedge_trade: Trade           # 은행 vs 헤지 딜러 (수수료 반영 후 조건)
    client: Counterparty
    dealer: Counterparty
    upfront_krw: float = 0.0     # 고객·딜러가 거래일에 현금으로 내는 수수료 (원)
    solve_on: str = "client"     # 손익분기 조정 다리: "client" = 고객 수취 다리, "hedge" = 헤지 지급 다리


def collateral_discount(cp: Counterparty | None) -> str | None:
    """상대방 VM 담보 통화에 맞는 다리 할인 종류 (DECISIONS M24). USD 현금 -> "csa", KRW 현금 -> "krw", 무담보 -> None."""
    if cp is None or not cp.csa.vm:
        return None
    if cp.csa.ccy not in ("USD", "KRW"):
        raise ValueError(f"{cp.name}: VM 담보 통화 {cp.csa.ccy}의 할인 커브가 없습니다 (USD·KRW만)")
    return "krw" if cp.csa.ccy == "KRW" else "csa"


def collateralised(trade: Trade, cp: Counterparty | None) -> Trade:
    """다리 할인을 상대방 담보 통화 커브로 바꾼 거래 (DECISIONS M24). 무담보 상대방이면 그대로 (기존 방식)."""
    kind = collateral_discount(cp)
    if kind is None:
        return trade
    return replace(trade, legs=[replace(l, discount=kind) for l in trade.legs])


def fee_delta(trade: Trade, fee: float) -> float:
    """화면 단위 수수료를 Trade.favoured의 delta로. 통화스왑: 연 bp → 소수, FX 선도: 원/외화 1단위 그대로."""
    return fee if trade.product == "FXFWD" else fee / 1e4


def build_deal(client_trade: Trade, client: Counterparty, dealer: Counterparty,
               hedge: Trade | None = None, fee_location: str = "client",
               fee: float = 0.0, upfront_krw: float = 0.0) -> Deal:
    """거래 조합을 만든다. client_trade는 라인피를 넣기 전 조건이다.

    hedge=None이면 BTB 헤지: 고객 거래와 같은 조건(넘어온 오프마켓 포함), 방향만 반대, 라인피 제외.
    hedge를 주면 그 조건 그대로 쓴다 (딜러 라인 Par 헤지 pricing.market_hedge(..., dealer), 또는 직접 입력).
    다리 할인은 상대방 담보 통화 커브로 정한다 (collateralised, M24): 고객 거래는 고객 CSA, 헤지는 딜러(CCP) CSA.
    무담보 상대방의 거래는 넘어온 다리 할인 그대로다.
    라인피 fee (통화스왑: 연 bp, FX 선도: 원/외화 1단위)는
      fee_location="client": 고객 거래에 반영 (고객이 내는 다리 가산 / 선도환율 마진)
      fee_location="hedge":  BTB 헤지에서 딜러에 주는 다리 차감
      fee_location="upfront": running 없음, upfront_krw만 (BTB 전용)
    """
    no_fee = client_trade
    if fee_location == "client" and fee:
        client_trade = no_fee.favoured("receive", fee_delta(no_fee, fee))
    if hedge is None:
        hedge = no_fee.mirrored()
        if fee_location == "hedge" and fee:
            hedge = hedge.favoured("pay", fee_delta(hedge, fee))
    solve_on = "hedge" if fee_location == "hedge" else "client"
    return Deal(collateralised(client_trade, client), collateralised(hedge, dealer), client, dealer, upfront_krw,
                solve_on)
