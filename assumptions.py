"""구버전(엔/원 검토 당시), 회귀 비교 전용. 현재 방법은 `linefee/`, `docs/METHODOLOGY_v1.md`. KVA 조달금리 등이 확정 결정(DECISIONS M5)과 다르다.
아래 주석의 "확정"은 엔/원 검토 당시의 선택이며, 현재 확정 방법(docs/DECISIONS.md)을 뜻하지 않는다.

거래 조건과 시장·신용·규제 가정.

숫자를 바꾼 뒤 `python main.py`를 다시 돌리면 된다.
시장 데이터(커브, 환율, 변동성, CDS)는 market_snapshot.json에서 읽는다.
새로 받으려면 `python market_data.py --out 새파일.json` (블룸버그 필요, 회귀 기준 스냅샷은 덮어쓰지 않는다).

거래: Party A 딜러 B, Party B 당행. 딜러 B가 최초에 원화 지급·엔화 수취, 만기에 반대,
중간에 엔/원 고정금리 교환. 반대편 HF와는 같은 조건의 반대 거래 (무담보).
"""
from dataclasses import dataclass, field

from market_data import Market, load


@dataclass
class Assumptions:
    # ---- 거래 조건 ----
    notional_jpy_oku: float = 100.0   # 명목금액 (억엔)
    maturity_years: int = 30          # 만기 (년), 연 1회 고정 쿠폰
    coupon_jpy: float | None = 0.0407  # 엔화 고정금리 (None이면 커브 파 금리), 연 지급액 = 금리
    coupon_krw: float | None = 0.0403  # 원화 고정금리 (None이면 커브 파 금리)
    krw_day_count: str = "ACT/360"     # 원화 쿠폰 일수계산. ACT/360이면 연 지급액 = 금리 x 365.25/360
    # 귀사 수수료 수취 방식 (엔/원 검토 당시 선택: running)
    #   "running": 공공기관 A가 내는 원화 쿠폰 = 딜러 B에 주는 원화 쿠폰 + hf_krw_spread_bp (같은 ACT/360)
    #              공공기관 A 4.13%, 딜러 B 4.03%. 딜러 B는 upfront를 내지 않는다
    #   "running_dealer_b": 공공기관 A 쿠폰 = coupon_krw, 딜러 B 쿠폰 = coupon_krw - hf_krw_spread_bp
    #              (수수료를 담보부인 딜러 B 다리에 넣음. 공공기관 A 익스포저가 늘지 않고 VM이 줄어든다)
    #   "upfront": 양쪽 쿠폰이 같고 딜러 B가 upfront를 지급 (dealer_b_upfront_bullet_bp)
    fee_structure: str = "running"
    hf_krw_spread_bp: float = 10.0
    # True면 공사와의 방향을 뒤집는다 (귀사가 공사에 KRW를 주고 JPY를 받음, 딜러 B와도 반대).
    # SIMM IM은 민감도 부호만 바뀌므로 금액이 같다.
    hf_reverse: bool = False
    break_year: int = 10              # 딜러 B와의 mutual break 시점 (dealer_b_structure="break"일 때)

    # ---- 시장 (블룸버그 스냅샷) ----
    market: Market = field(default_factory=load, repr=False)

    # ---- 신용 ----
    hf_cds_tenor: int = 10            # HF 신용 대용치: 한국 국가 CDS 이 만기 값을 30년 평탄하게 적용
    hf_spread_over_sovereign_bp: float = 0.0  # HF가 국가보다 넓다고 볼 때 가산
    cds_dealer_b: float = 0.0070        # 딜러 B CDS (정산 지연 0이면 쓰이지 않음)
    lgd: float = 0.60                 # 부도시 손실률
    include_hf_cva: bool = False      # HF 회계상 CVA. 위험가중치 0%, PD 0이므로 미반영
    pfe_quantiles: tuple = (0.95, 0.99)  # PFE 신뢰수준

    # ---- 딜러 B CSA ----
    settlement_lag_days: int = 0      # 실제 담보 정산 지연 (경제적 CVA용). 0이면 딜러 B CVA = 0
    regulatory_mpor_days: int = 10    # SA-CCR 규제상 MPOR 하한 (양자간 장외파생 10영업일)

    # ---- 딜러 B IM-CSA (양방향, 분리보관) ----
    im_csa: bool = True
    simm_version: str = "2.8+2512"    # 현재 시행. "2.9+2606"은 2026-12-05 시행
    simm_calc_currency: str = "USD"   # IM CSA 계산통화
    simm_include_principal: bool = False  # 통화스왑 원금 교환분은 IM 대상 아님
    im_haircut: float = 0.04          # 받은 IM(국채) 담보차감률 (266.다의 C에 반영)
    im_funding_spread: float = 0.0    # 낸 IM 조달 비용 (MVA). 담보채권 한계수익이 낮아 0에 가깝다

    # ---- 담보 조달 (딜러 B CSA: USD 현금) ----
    usd_funding_spread: float = 0.0012  # USD 현금담보를 낼 때 조달비용: 내부 FTP SOFR + 12bp, CSA 이자 SOFR
    usd_benefit_spread: float = 0.0012  # USD 현금담보를 받을 때 자금이익 (같은 FTP로 조달 대체)
    recognise_fba: bool = True          # 담보 수취 시 자금이익 인정

    # ---- 자본 ----
    # 자본 요구수익률은 세후로 입력하고 세전으로 바꿔 쓴다 (수수료·FVA가 모두 세전이므로)
    hurdle_after_tax: float = 0.10    # 세후 요구수익률 (자기자본비용 기준)
    tax_rate: float = 0.275           # 법인세 25% + 지방소득세 2.5% (2026년 귀속, 확인 필요)
    # 자본비용 산정 방식 (xva_model.kva 참고)
    #   "retained_net": 수수료 현재가치를 첫날 이익으로 인식해 자본(CET1)에 넣고, 묶인 자본은 조달을 대체
    #   "net_funding" / "retained" / "gross"(이전 기본값, 가장 보수적)
    kva_method: str = "retained_net"
    # 목표 자본비율: 보통주자본(CET1) 기준. 요구수익률(ROE)과 같은 자본 기준으로 맞춘다.
    # 목표비율 13%는 DECISIONS M4의 기준값 (규제 요구 총자본 11.5% = 최저 8% + 보전 2.5% + D-SIB 1%)
    capital_ratio: float = 0.13
    rw_hf: float = 0.0                # HF 신용위험 위험가중치 (자본 부과 없음)
    # 딜러 B 신용위험 위험가중치 (시행세칙 별표3 35~37)
    #   은행과 동등한 규제를 받는 증권회사(36): 은행 기준(35) A+~A- 30%, BBB+~BBB- 50%
    #   그렇지 않은 증권회사: 기업 기준(37) 적용
    rw_dealer_b: float = 0.50

    # SA-CCR에서 통화스왑을 어느 자산군으로 분류할지 (시행세칙 별표3 267.나)
    #   "fx_only": 주된 위험요인(환율) 하나로 분류 → FX add-on 4%만
    #   "fx_and_ir": FX + 통화별 금리 두 다리까지 함께 분류 (267.나.(2) 복잡한 거래)
    sa_ccr_ccs_treatment: str = "fx_only"   # 엔/원 검토 당시 선택: 금리 다리 미포함
    # 공공기관 A에 CVA 자본 부과. 신용 RW 0%와 별개로 별표3 306.에 따라 모든 파생상품이 대상
    # (면제는 적격 중앙청산소 거래, 298.가·나 거래뿐)
    hf_cva_capital: bool = True

    # ---- CVA 자본 방식 ----
    # "ba_cva": BA-CVA (reduced, 헤지 없음)
    # "ccr_proxy": CRE50.9 특례. 비청산 장외파생 명목 합계가 1,000억 유로 이하인 은행은
    #              CVA 자본을 신용위험 자본과 같게 둘 수 있다
    #   시행세칙 별표3 308: 파생상품 명목원금 합계 140조원 이하 은행만 "ccr_proxy" 선택 가능
    # "ccr_ratio": 참고용. 은행 전체의 CVA RWA / CCR RWA 실적 비율을 이 거래에 그대로 적용.
    #   거래별 비율은 만기와 상대방 유형에 따라 수십 배 차이 나므로 가격 산정에는 쓰지 않는다
    cva_capital_method: str = "ba_cva"      # 엔/원 검토 당시 선택: 308. 특례 대상 아님
    cva_to_ccr_rwa_ratio: float = 0.33   # 은행 전체 실적치 (배분 기준이 아니라 결과로 나온 비율)

    # ---- CVA 자본: BA-CVA (reduced, 헤지 없음) ----
    ba_cva_rw_dealer_b: float = 0.05    # 금융기관 투자등급 위험가중치
    # 313.(2): 지방정부·정부보증 비금융·공공기관 투자등급 1% (공공기관 A). 정부보증 금융(HF)이면 5%
    ba_cva_rw_hf: float = 0.01
    ba_cva_ds: float = 0.65           # discount scalar
    ba_cva_alpha: float = 1.4
    # 유효만기 M (시행세칙 별표3 313.(3) → 126., 5년 상한 미적용)
    #   "residual": 126.다(상계계약 하 장외파생은 거래 만기의 명목가중평균) / 126.라(계약상 잔존만기)
    #   "cashflow": 126.가 현금흐름 가중 만기 산식을 거래 단위에 적용
    ba_cva_maturity: str = "residual"
    # 포트폴리오 한계 기여도. 1.0 = 거래상대방 1곳만 있는 단독 기준.
    # 거래상대방이 많은 포트폴리오에서는 dK/dSCVA = (rho^2 x 합계 SCVA + (1-rho^2) x SCVA_dealer_b) / K
    # 이고, 딜러 B 비중이 작으면 rho(= 0.5)에 가까워진다.
    # 기본값 0.5: 이 거래로 은행 전체 BA-CVA 자본이 실제로 늘어나는 양 (근사치)
    # 엔/원 검토 당시 선택: 귀사 CVA 포트폴리오는 거의 완전 분산 → rho(0.5)에 수렴
    ba_cva_marginal_factor: float = 0.5

    # ---- 딜러 B upfront (딜러 B가 귀사에 지급, HF에서는 받는 것 없음) ----
    dealer_b_structure: str = "bullet"          # "bullet": 30년 break 없음, "break": 10년 mutual break
    # 30년 bullet upfront 금액 (엔/원 검토 당시 합의) (원화 명목 대비 bp). None이면 아래 연 bp x 원화 연금계수
    dealer_b_upfront_bullet_bp: float | None = 220.0
    # 딜러 B 제시: 연 bp를 30년 원화 연금계수로 upfront 환산해 지급 (연 10bp x 30이 아님)
    dealer_b_fee_bullet_bp_pa: float = 10.0     # 30년 bullet
    dealer_b_fee_break_bp_pa: float = 8.0       # 10년 break 포함 (합의 해지라면 경제적으로 bullet과 같음)

    # ---- break 행사 시 재헤지 (dealer_b_structure="break"일 때만. 10년 시점에 남은 20년을 다시 구함) ----
    replacement_bid_offer_bp: float = 5.0      # 20년 JPY/KRW CCS bid-offer (연 bp, 귀사 부담)
    # 새 딜러와의 거래 조건 (연 bp). 양수 = 귀사가 새 딜러에 지급, 음수 = 새 딜러가 귀사에 지급
    # (지금 딜러 B처럼 새 딜러도 돈을 내고 받아 간다면 음수)
    replacement_dealer_charge_bp: float = 10.0
    break_exercise_prob: float = 1.0           # 10년 시점에 break가 실행될 확률

    # ---- 금리 요인 (공사 익스포저 시뮬레이션) ----
    # 통화별 평행이동 요인. 변동성·상관은 market_snapshot.json의 hist (python market_data.py hist --out 새파일.json)
    rate_factors: bool = True
    rate_vol_scale: float = 1.0               # 과거 추정 변동성에 곱하는 배수
    rate_mean_reversion: float = 0.0          # 0이면 평행이동이 그대로 누적 (Ho-Lee)
    rate_corr_override: list | None = None    # [FX, KRW, JPY] 3x3 상관행렬 직접 지정

    # ---- 시뮬레이션 ----
    n_paths: int = 20000
    dt: float = 0.25                  # 시간 격자 (년)
    seed: int = 11

    @property
    def cost_of_capital(self) -> float:
        """세전 자본 요구수익률 = 세후 / (1 - 세율)."""
        return self.hurdle_after_tax / (1 - self.tax_rate)

    @property
    def jpykrw_spot(self) -> float:
        return self.market.jpykrw_spot

    @property
    def cds_hf(self) -> float:
        return self.market.korea_cds[self.hf_cds_tenor] + self.hf_spread_over_sovereign_bp / 1e4

    @property
    def notional_krw_oku(self) -> float:
        """원화 명목금액 (억원)."""
        return self.notional_jpy_oku * self.jpykrw_spot
