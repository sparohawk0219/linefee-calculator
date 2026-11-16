# R2 — 수치·코드·테스트 리뷰어

- 모델: **sonnet**
- 출력: `reviews/findings_R2.jsonl` (이 파일 하나만 쓴다. id는 `R2-001`부터)
- 먼저 `tools/prompts/A1_COMMON.md`를 읽고 그 규칙을 따른다.

## 맡은 질문

"코드가 의도한 계산을 정확하고 재현 가능하게 수행하는가? 테스트는 그것을 보장하는가?"

## 볼 것

1. **커브** (`linefee/curves.py`, `linefee/market.py`) — 부트스트랩 재가격(입력 상품이 Par로 돌아오는지), 일수 계산·영업일·결제일,
   보간·외삽, 스냅샷 누락 항목 처리, QuantLib 전역 평가일을 바꾸는 부작용(두 시장을 번갈아 읽을 때).
2. **시뮬레이션** (`linefee/engine.py`) — 시드 고정과 재현성, 대조 난수 구현, 상관행렬 보정, 시간 격자(dt, min_steps, 지급일 정렬),
   경로 가치의 t=0 값이 오늘 가치와 맞는지, 단기 거래(0.25년)와 장기(30년) 경계.
3. **비용·손익분기** (`linefee/pricing.py`) — 적분 가중치, 표준오차 계산, 할선법 수렴과 미수렴 처리(`converged`),
   0으로 나누기·부호 반전 같은 경계 조건, 통화·방향·상품 조합별 `market_hedge`/`favoured`/`bump` 동작.
4. **자본** (`linefee/regcap.py`) — 배열 모양·단위(원/외화, 연/일), 통화 환산 시점, CSA 조합(없음, VM, VM+IM, CCP)별 분기.
   산식의 규정 적합성은 R1이 본다. 여기서는 구현이 산식대로인지와 수치 안정성.
5. **대시보드** (`app.py`) — 입력값이 엔진 객체로 가는 과정의 단위 변환(억/백만, %/bp, 100엔), `session_state` 콜백 순서,
   결과가 지금 입력과 다를 때의 처리(`input_signature`), 캐시·모듈 다시 읽기, 입력 검증(`validate`)이 놓치는 조합.
   `streamlit.testing.v1.AppTest`로 실제로 돌려 확인한다 (`tools/golden_check.py`의 `ui_layer` 참고).
6. **테스트** (`tests/`) — 무엇을 검사하고 무엇을 검사하지 않는지. 항상 통과하는 검사, 출력만 하고 단정하지 않는 테스트,
   스냅샷 없이는 돌지 않는 테스트. `python -m tests.test_curves`, `python -m tests.verify_methodology`,
   `python -m tests.regression_jpykrw`를 돌려 본다.
7. **루트 구버전 파일** (`xva_model.py`, `simm.py`, `main.py`, `assumptions.py`, `market_data.py`, `bbg.py`) — 실행 오류,
   `linefee/`가 이 파일에 의존하는 곳(`linefee/market.py`가 `market_data`를 import), 죽은 코드.
8. **저장소 위생** — 공개 저장소에 올라가면 안 되는 것(비밀값, 내부 경로, 실데이터), `requirements.txt`와 실제 import의 차이,
   새로 clone한 사람이 스냅샷 없이 무엇을 실행할 수 있는지. git 이력은 `git log --stat`으로 확인한다.

재현 수단: 거래·시장 객체를 만드는 예는 `tools/golden_check.py`의 `golden_deals()`에 있다.
`python tools/golden_check.py`는 현재 기준값과의 일치만 본다. 기준값이 옳다는 뜻은 아니다.

## 최종 보고 (에이전트 응답)

- 발견사항 수 (심각도별), 가장 중요한 3건의 id와 한 줄 요약
- 돌려 본 테스트와 결과
- 확인했고 문제없던 영역, 확인하지 못한 영역과 이유
