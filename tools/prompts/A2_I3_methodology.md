# I3 — 구현자 3: 방법론·엔진 수치 (값이 바뀌는 배치)

- 모델: **sonnet**
- 먼저 `tools/prompts/A2_COMMON.md`, `tools/prompts/A2_I_RULES.md`를 읽고 따른다.
- 배치 1(I1, I2)이 main에 병합된 뒤 시작한다. `git log --oneline main..HEAD`가 비어 있어야 한다.
- 맡은 항목 (**반드시 이 순서, 항목마다 커밋 하나**): **L-010 → L-002 → L-004 → L-001**, 마지막에 문서 13장·테스트 기대값 갱신 커밋.

네 항목 모두 사용자가 내용을 정했다. 아래 지시와 `decision_note`대로만 구현하고, 다른 해석을 넣지 않는다.

## 값 조건과 증분 기록

커밋마다 그 항목이 만든 값 변화만 따로 남긴다. 순서대로 아래 네 줄을 그 항목의 커밋 직후에 돌린다
(`--baseline`, `--save`의 상대 경로는 메인 체크아웃 기준으로 풀린다. `local/a2/before.json`은 이미 있다. 차이가 있으면 종료 코드 1이 정상이다).

```
python tools/golden_check.py --ui --baseline local/a2/before.json      --save local/a2/after_L-010.json > c:/projects/fun/local/a2/diff_L-010.txt
python tools/golden_check.py --ui --baseline local/a2/after_L-010.json --save local/a2/after_L-002.json > c:/projects/fun/local/a2/diff_L-002.txt
python tools/golden_check.py --ui --baseline local/a2/after_L-002.json --save local/a2/after_L-004.json > c:/projects/fun/local/a2/diff_L-004.txt
python tools/golden_check.py --ui --baseline local/a2/after_L-004.json --save local/a2/after_L-001.json > c:/projects/fun/local/a2/diff_L-001.txt
```

| 항목 | golden에서 기대하는 변화 |
|---|---|
| L-010 | **없음** ("통과"). golden은 kappa = 0이고 그 경로의 계산은 비트 단위로 같아야 한다 |
| L-002 | CCP 거래만 (`IRS_KRW_5Y_CCP`, `OIS_USD_3Y_CCP`, `UI:예시4`, `UI:예시5`): `cost.KVA (딜러 신용)` 증가. 나머지 거래는 변화 없음 |
| L-004 | 무담보 고객이 있는 전 거래: `cost.KVA (고객 신용)`, `cost.KVA (고객 CVA)` 소폭 증가. 금리 자산군(IRS·OIS)은 CCP 쪽도 소폭 변화 가능 |
| L-001 | `cost.KVA (고객 CVA)`, `cost.KVA (딜러 CVA)` 소폭 증가 (CCP는 CVA 면제라 0 그대로) |

- 어느 단계에서도 `day1`, `v0_client`, `v0_hedge`는 바뀌면 안 된다. 바뀌면 멈추고 보고한다.
- `cost.E/L`, `cost.FVA`는 바뀌지 않을 것으로 본다. 바뀌면 이유를 코드에서 확인해 impl 메모에 적는다 (설명할 수 없으면 멈추고 보고).
- 위 표는 총괄의 예상이지 정답이 아니다. 방향이나 대상 거래가 표와 다르면 값을 표에 맞추려 들지 말고, 원인을 확인해 impl 메모와 최종 보고에 적는다.
- `total_cost`, `net`, `be.*`, `breakeven_hurdle`은 위 변화에 따라 함께 움직인다.

## 항목별 지시

**L-010 음수 평균회귀** — 사용자 결정: 음수 kappa를 허용하고 화면은 경고만 한다. 엔진이 모든 kappa에서 일관되어야 한다.
- `linefee/engine.py`에서 kappa로 분기하는 모든 곳(`_ou`의 sd·var, `simulate`의 환율 quanto 공분산 재귀의 sdx, `_b`, 그 밖에 `kappa > 0`으로
  나눈 곳)을 **kappa ≠ 0이면 OU 식, kappa = 0이면 Ho-Lee**로 통일한다. OU 식 `(1 - e^{-2κt}) / (2κ)`, `(1 - e^{-κτ}) / κ`는 κ < 0에서도 그대로 성립한다.
  0 근처(|κ| < 1e-12)는 0으로 본다.
- kappa = 0 분기의 식은 지금과 글자 그대로 같게 둔다 (golden 비트 일치). kappa > 0의 결과도 바뀌면 안 된다:
  고치기 전에 `BankParams(rate_mean_reversion=0.03)`으로 거래 하나의 `run(...).net`을 적어 두고 고친 뒤 같은지 확인한다.
- 새 테스트 `tests/test_engine_kappa.py`: kappa ∈ {-0.05, 0, 0.05}에서 (1) 각 시점의 경로 표본분산이 모델 분산 `s.var`와 몬테카를로 오차 안에서 같고,
  (2) `tests/verify_methodology.py`의 martingale 검사와 같은 방식의 할인계수 martingale 검사가 통과한다.
  고치기 전 코드에서 kappa = -0.05가 실패하는 것을 먼저 확인한다.
- `app.py`: 금리 평균회귀 입력이 음수면 경고를 보인다 (계산은 막지 않는다). `min_value`를 주지 않는다.
- `docs/METHODOLOGY_v1.md`의 금리 모형 절에 kappa 부호별 처리를 한 줄 적는다.

**L-002 CCP SA-CCR MPOR 10영업일 (296.사)**
- `linefee/trade.py`에 상수 `CCP_REG_MPOR_DAYS = 10`을 두고 (주석에 296.사), `app.py`의 CCP `CSA(... reg_mpor_days=...)`가 그 상수를 쓴다.
  **이름을 정확히 지킨다** — `tools/golden_check.py`가 이 상수를 읽어 engine 층 CCP 거래에 쓴다.
- CCP IM의 보유기간 `BankParams.ccp_im_mpor_days = 5`(IM VaR 근사)는 다른 값이다. 건드리지 않는다.
- 화면 캡션, `METHODOLOGY_v1.md` 12장·14장, `DESIGN.md`의 "5영업일"·"근사" 표현을 296.사 인용으로 바꾼다.
  `reg/byeolpyo3.txt`에서 296.사와 267.카.(2)를 직접 확인하고 쓴다.

**L-004 10영업일 하한 (267.카.(1), 267.자.(1))**
- `linefee/regcap.py`: 비증거금부 만기요인 MF = sqrt(min(M, 1년) / 1년)의 M에 10영업일(= 10/250년, 코드의 MPOR 환산과 같은 관행) 하한.
  금리 자산군 감독 듀레이션의 종료일 E에도 10영업일 하한.
- **만기 시점 노드(잔존 0)는 지금처럼 둔다** (add-on 0, RC만 남김). 하한은 그 전 노드에만 적용한다.
- `METHODOLOGY_v1.md` 7.1, `DESIGN.md` 5.1에 하한과 조항을 적는다.

**L-001 BA-CVA 유효만기 하한 (126.다)**
- `linefee/regcap.py` `capital_paths`: M = max(잔존만기, 10영업일 = 10/250년). 근거는 126.다(상계계약 하 장외파생상품의 하한 = 최소보유기간 10영업일),
  5년 상한 해제는 313.(3). **만기 시점 노드(잔존 0)는 지금처럼 M = 0** (사용자 결정: RC만 남기는 처리 유지).
- docstring과 `METHODOLOGY_v1.md` 7.2에 조항을 적는다. 1년 하한(126.가)은 적용하지 않는다 — 이유(상계계약 하 장외파생)를 한 문장으로.

**마지막 커밋: 문서 13장·테스트 기대값** (제목 `A2 값 갱신: 방법론 변경 반영`)
- `METHODOLOGY_v1.md` 13장(검증 수치)과 그 밖의 결과 수치, `tests/verify_methodology.py`·`tests/regression_jpykrw.py`의 기대값 표(I2가 만든 `EXPECTED`)를
  새 값으로 고친다. 회귀 기준 스냅샷으로 다시 계산한 값만 쓴다. 최신 스냅샷 날짜에 묶인 문서 수치는 그 날짜 스냅샷이 없으면 고치지 말고 목록만 보고한다.
- 이 커밋 뒤 누적 변화를 남긴다: `python tools/golden_check.py --ui > c:/projects/fun/local/a2/diff_total.txt`
  (기준값 대비 전체 전/후 표. 사용자 게이트 2 자료).
- 이 커밋은 네 항목 모두에 걸치므로 `ledger.py impl`을 다시 쓰지 말고 최종 보고에 해시를 적는다.

## 최종 보고에 추가

- 항목별 `diff_L-0xx.txt`의 "거래별 요약" 표에서 순마진 차이가 가장 큰 거래와 그 크기
- 기대 변화 표와 달랐던 점
