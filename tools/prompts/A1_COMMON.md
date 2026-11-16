# Run A1 공통 규칙 (R1, R2, V 모두 적용)

이 저장소는 은행 파생상품 데스크의 라인피(XVA) 계산기다 (Python + QuantLib + Streamlit).
당신은 독립 리뷰 런(A1)의 에이전트다. 작업 디렉터리는 저장소 루트(메인 체크아웃)다.

## 먼저 읽을 것

1. `docs/DECISIONS.md` — 사용자가 확정한 결정. **전제이며 다시 묻지 않는다.**
   - 코드·문서가 결정을 구현하지 않으면 정상 발견사항이다.
   - 결정 자체에 대한 이견은 `category: "decision-conflict"`로만 적는다. 다른 발견사항의 claim·suggested_fix에 섞지 않는다.
2. `docs/METHODOLOGY_v1.md`, `DESIGN.md` — 방법론과 설계.

## 읽기 전용

- 저장소의 어떤 파일도 고치지 않는다. 자기 역할의 출력 파일만 쓴다 (역할별 프롬프트 참조).
- git 명령은 읽기만 한다 (`status`, `log`, `diff`, `show`). commit, checkout, stash, reset, push 금지.
- 명령 실행은 된다: 테스트, 짧은 재현 스크립트. 임시 파일은 `local/scratch/`에만 만든다 (gitignore 대상).
- 블룸버그 호출 금지 (`python -m linefee.market`, `python market_data.py`, `bbg.py`, app의 "블룸버그에서 새로 받기"). 스냅샷 파일을 덮어쓴다.
- `local/golden_baseline.json`을 쓰는 `python tools/golden_check.py --write` 금지. 비교 실행(`python tools/golden_check.py`)은 된다.
- 다른 에이전트를 띄우지 않는다.

## 범위

저장소 전체: `linefee/`, `app.py`, `tests/`, `docs/`, `DESIGN.md`, 루트의 `xva_model.py`, `main.py`, `simm.py`,
`assumptions.py`, `market_data.py`, `bbg.py`, `reg/byeolpyo3.txt`(시행세칙 별표3 원문), `requirements.txt`, `.gitignore`.
`tools/`는 범위 밖이다.

## 공개 저장소 주의

발견사항 파일은 공개 저장소에 커밋된다.
- 시장 스냅샷의 원자료(호가, 커브 포인트, 환율 수준)를 인용하지 않는다. 계산 결과(억원, bp)와 차이는 적어도 된다.
- 실제 거래상대방에 대한 평가·내부 정보를 새로 쓰지 않는다 (코드에 이미 있는 예시 이름을 가리키는 것은 된다).

## 발견사항 형식

한 줄에 JSON 객체 하나 (JSONL, UTF-8). 필드:

| 필드 | 내용 |
|---|---|
| `id` | 리뷰어 접두어 + 일련번호. `"R1-001"`, `"R2-001"` |
| `severity` | `"P1"` 결과 숫자가 틀리거나 규정 위반 / `"P2"` 조건에 따라 틀림, 문서와 코드 불일치 / `"P3"` 품질·명확성 |
| `category` | `methodology` \| `regulatory` \| `numerics` \| `code` \| `tests` \| `docs-mismatch` \| `repo-hygiene` \| `decision-conflict` |
| `file` | 저장소 기준 상대 경로 (슬래시 `/`) |
| `line` | 줄 번호 (정수). 파일 전체에 해당하면 0 |
| `claim` | 무엇이 문제인지 한두 문장. 추측이면 추측이라고 쓴다 |
| `repro_command` | 저장소 루트에서 실행하면 문제를 보여 주는 **한 줄 명령** (`python -c "..."`, `python -m tests...`, `grep -n ...`). 없으면 `""` |
| `expected_vs_actual` | 기대값과 실제값. 규정·문서 근거는 조항·줄 번호로 |
| `touches_methodology` | 고치는 데 방법론 변경이 필요하면 `true` |
| `suggested_fix` | (선택) 고치는 방향 한 문장 |

예:

```json
{"id": "R2-001", "severity": "P2", "category": "numerics", "file": "linefee/pricing.py", "line": 123, "claim": "…", "repro_command": "python -c \"…\"", "expected_vs_actual": "기대 …, 실제 …", "touches_methodology": false, "suggested_fix": "…"}
```

## 품질 기준

- 직접 확인한 것만 낸다. 재현 명령을 실제로 돌려 본 뒤 결과를 `expected_vs_actual`에 적는다.
- 재현 명령이 없는 항목(문서 서술, 규정 해석)은 근거 위치를 줄 번호까지 적는다.
- 개수를 채우지 않는다. 문제없는 영역은 최종 보고에 "확인했고 문제없음"으로 적는다.
- 취향(이름, 스타일, 리팩터링 제안)은 내지 않는다. P3는 사용자가 오해하거나 유지보수 때 실수할 근거가 있을 때만.
- 제출 전에 `python tools/ledger.py merge`를 **돌리지 않는다** (총괄 쪽에서 돌린다). 대신 자기 파일이 유효한 JSONL인지 확인한다:
  `python -c "import json,sys;[json.loads(l) for l in open(sys.argv[1],encoding='utf-8') if l.strip()];print('ok')" reviews/findings_R1.jsonl`
