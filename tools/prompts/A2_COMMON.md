# Run A2 공통 규칙 (I1, I2, I3, C1, C2 모두 적용)

이 저장소는 은행 파생상품 데스크의 라인피(XVA) 계산기다. Run A1 리뷰에서 나온 발견사항 21건을 사용자가 모두 accept했고,
A2는 그것을 구현하고 종결을 확인하는 런이다.

## 작업 위치

- 작업 디렉터리: `c:\projects\fun\.worktrees\a2` (브랜치 `a2/fixes`). **메인 체크아웃 `c:\projects\fun`의 추적 파일은 고치지 않는다.**
- 원장(`reviews/ledger.jsonl`)과 golden 기준값(`local/`)은 메인 체크아웃에만 있다. `tools/ledger.py`, `tools/golden_check.py`는
  worktree에서 실행해도 메인의 것을 읽고 쓴다. 직접 열어 고치지 않는다.
- 임시 파일은 메인 체크아웃의 `c:\projects\fun\local\scratch\`에만 만든다 (gitignore 대상).
- 시장 스냅샷은 worktree에 복사되어 있다 (gitignore 대상). 없으면 `python tools/golden_check.py`가 복사한다.

## 먼저 읽을 것

1. `docs/DECISIONS.md` — 확정 결정. 전제이며 다시 묻지 않는다. 고치지 않는다.
2. 자기 역할의 프롬프트 파일.
3. 맡은 항목: `python tools/ledger.py show L-001` (claim, repro_command, expected_vs_actual, suggested_fix, **decision_note**).
   `decision_note`는 사용자 결정이다. `suggested_fix`와 다르면 `decision_note`가 우선한다.

## 금지

- push, 메인 브랜치 변경, 브랜치 전환, rebase, reset --hard, stash, 이력 재작성.
- `docs/DECISIONS.md`, `tools/`, `.gitignore`, `local/golden_baseline.json`, `reviews/` 파일 수정 (원장은 `tools/ledger.py` 명령으로만 기록).
- `python tools/golden_check.py --write` (기준값 재설정은 사용자 승인 후 비서가 한다).
- 블룸버그 호출 (`python -m linefee.market`, `python market_data.py`, `bbg.py`, 화면의 "블룸버그에서 새로 받기"). 스냅샷을 덮어쓴다.
- 다른 에이전트를 띄우는 것.
- 맡은 항목 밖의 수정. 보다가 다른 문제를 발견하면 고치지 말고 최종 보고에 적는다.

## 검사 명령 (worktree 루트에서)

```
python tools/golden_check.py --ui          # 값 불변 게이트 (약 20초)
python -m tests.test_curves
python -m tests.verify_methodology
python -m tests.regression_jpykrw
python tools/path_gate.py --role implementer --base main
```

## 가명 (L-011, 전 역할 공통 — 새로 쓰는 코드·문서·커밋 메시지에도 적용)

실명 → 가명 대응표는 로컬 전용 파일 `c:\projects\fun\local\a2\pseudonyms.md`에 있다 (이 프롬프트는 공개 저장소에 올라가므로 실명을 적지 않는다).
쓰는 가명은 셋이다: **공공기관 A** (엔/원 거래의 고객), **딜러 B** (그 거래의 헤지 딜러), **당행** (이 은행).
자행 CET1 실적 수치 주석은 지운다 (목표비율 13%는 DECISIONS M4의 기준값으로만 적는다).

원장의 claim에는 실명이 남아 있다 (로컬 전용). 저장소 파일과 커밋 메시지에는 옮겨 적지 않는다.

## 공개 저장소 주의

커밋은 공개 저장소로 간다. 시장 스냅샷의 원자료(호가, 커브 포인트, 환율 수준)를 코드·문서·테스트에 새로 적지 않는다.
계산 결과(억원, bp)는 된다.
