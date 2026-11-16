# B0 H: 평가 하니스 (sonnet)

먼저 `tools/prompts/B_COMMON.md`를 읽는다. 역할은 harness다. `ui_eval/`과 `tests/test_score.py`만 만든다. 화면(`app.py`)은 고치지 않는다.

## 이미 있는 것 (총괄이 썼다. 읽고 그대로 쓴다. 고치지 않는다)

- `ui_eval/contract.py`: 과제 읽기(`load_tasks`, `task_set`), 계산 결과 → 입력(`inputs_from_result`), 입력 비교(`diff_inputs`),
  결과 → 정답 항목(`answers_from_result`), 답 판정(`answer_ok`), 항목 설명(`FIELD_LABELS`), 경로(`PRIVATE_DIR`, `ANSWERS`, `TASKS_*`),
  평가용 상수(`ENV_DATA_DIR`, `SECRET_ADMIN`, `EVAL_ADMIN_PASSWORD`, `ADMIN_KEY_PREFIX`).
- `ui_eval/tasks_public.yaml`(공개 과제 15건: 세일즈 12, 관리자 3), 저장소 밖 `tasks_hidden.yaml`(세일즈 6건). JSON 부분집합이라 `json`으로 읽는다. PyYAML은 설치되어 있지 않다.
- `ui_eval/make_answers.py`: 정답 생성기. 정답 파일은 이미 저장소 밖에 있다. 다시 만들지 않는다 (`--check`만 쓴다).
  정답 파일 형식: `{"tasks": {id: {"inputs": 의도한 입력, "answers": {...}, "first": {"inputs", "answers"}}}}` (`first`는 `inputs_first`가 있는 과제만).
- 화면이 지키는 약속: B_COMMON.md "화면 계약". 지금 화면(`app.py`)은 2, 3, 8번만 지킨다. 역할 분리(4~7)는 다음 단계 U0가 만든다.

## 만들 것

### 1. `ui_eval/backend.py` — 화면 접근 한 겹

화면 프레임워크에 닿는 코드는 이 파일에만 둔다. 규약(Protocol)과 Streamlit 구현을 함께 쓴다.

```
class Backend(Protocol):
    def __init__(self, data_dir: Path, admin_password: str | None): ...
    def elements(self) -> list[dict]   # 화면 순서. {"id", "kind", "label", "value", "options", "help", "key"}
    def set(self, elem_id: str, value) -> None
    def click(self, elem_id: str) -> None
    def result(self) -> dict | None    # session_state["result"]와 같은 항목. 없으면 None
    def role(self) -> str              # "general" | "admin"
```

- `kind`: heading, text, caption, warning, error, info, metric, table, chart, select, radio, number, text_input, checkbox, button 등.
- `id`: 위젯 key가 있으면 key, 없으면 종류 + 라벨로 만든 안정적인 이름(같은 라벨이 여럿이면 순번). 다시 실행해도 같아야 한다.
- Streamlit 구현은 AppTest. `at.secrets[SECRET_ADMIN] = admin_password`, 환경 변수 `LINEFEE_DATA_DIR = data_dir`를 넣고 `app.py`를 실행한다.
  AppTest가 내는 "missing ScriptRunContext" 경고는 출력에서 지운다.
- 저장소에 `ui/testing.py`가 있고 그 안에 `Backend`가 있으면 그것을 쓴다 (화면 프레임워크 교체 대비, DECISIONS O11). 규약은 docstring에 정확히 적는다 (화면 구현자가 이것만 보고 구현한다).

### 2. `ui_eval/driver.py` — 페르소나가 쓰는 텍스트 드라이버

페르소나(haiku)는 이 명령으로만 화면을 본다. 사람이 쓰는 CLI가 아니라 모델이 쓰는 CLI다: 출력은 짧고, 오류 메시지는 다음에 할 일을 알려 준다.

```
python -m ui_eval.driver list --persona sales|admin [--set public|hidden|all]   # 과제 id만 한 줄씩 (지시문은 start에서)
python -m ui_eval.driver start <run> <persona> <task>    # 세션을 만들고 세션 이름(run-persona-task), 과제 지시문, 제출 항목, 첫 화면 전체를 찍는다
python -m ui_eval.driver show   <session> [--all]        # 지금 화면 전체
python -m ui_eval.driver help   <session> <id>           # 그 요소의 도움말(help)
python -m ui_eval.driver set    <session> <id> <값>      # 값 넣기·고르기 (select·radio는 보이는 글자 또는 순번)
python -m ui_eval.driver click  <session> <id>
python -m ui_eval.driver note   <session> "<글>"         # 혼란 보고
python -m ui_eval.driver answer <session> 항목=값 ...     # 답 제출. 세션이 끝난다
python -m ui_eval.driver giveup <session> "<이유>"       # 포기. 세션이 끝난다
```

- run 예: `r0`, `r1`, `r2`, `smoke`, `dev`. persona: `p1`(세일즈 신입), `p2`(세일즈 숙련), `p3`(관리자). 과제 id가 `H`로 시작하면 숨긴 과제(`contract.task_set`).
- 세션 상태: 명령마다 새 프로세스다. 세션 폴더의 행동 로그를 처음부터 다시 실행해 화면을 되살리는 방식(replay)을 기본으로 한다.
  한 명령이 30초 안에 끝나야 한다 (계산 한 번은 1초 안팎). 더 빠른 방식이 있으면 써도 되지만 Windows에서 떠 있는 프로세스를 남기지 않는다.
- 세션 폴더: 공개 과제는 `ui_eval/runs/<run>/<persona>/<task>/`, 숨긴 과제는 `<PRIVATE_DIR>/runs/<run>/<persona>/<task>/`.
  그 안에 `log.jsonl`(행동 로그), `data/`(이 세션의 데이터 디렉터리: 시작할 때 `data/counterparties.sample.csv`를 `counterparties.csv`로 복사. 다시 실행할 때마다 처음 상태로).
  같은 이름으로 `start`를 다시 부르면 이전 폴더를 지우고 새로 시작한다.
- 화면 찍기: 화면 순서대로 한 줄에 요소 하나. 예: `[cr_amt] 숫자 "명목 (억원)" = 1,425.30`, `[preset] 선택 "예시 불러오기" = ... {1: ..., 2: ...}`,
  `⚠ 경고: ...`, `✖ 오류: ...`, `(지표) 첫날 이익 = 6.1억 | 고객 ... `, 표는 머리글 + 앞 12행, 차트는 `[차트] 제목`만. 도움말이 있는 요소는 끝에 `(?)`.
  `set`·`click` 뒤에는 **바뀐 줄만**(새로 생김 `+`, 사라짐 `-`, 값 바뀜 `~`) 찍고, 지금 보이는 경고·오류는 매번 모두 찍는다. 결과 영역이 새로 생기거나 바뀌면 결과 영역은 전부 찍는다.
  한 번 출력이 80줄을 넘지 않게 한다(`show --all`만 예외).
- `start`가 찍는 것: 지시문(`instruction`), 제출 항목(과제의 `scored`를 `FIELD_LABELS` 설명과 함께, 그리고 `answer` 명령 예시 한 줄). 관리자 과제(persona admin)면 평가용 관리자 비밀번호도 찍는다.
  `inputs`, `traps`, 정답은 어디에도 찍지 않는다.
- 행동 로그 한 줄(JSON): `seq, ts, cmd, id, label, value, ok, error, role, warnings`(행동 뒤 보이는 경고·오류 글), `dirty`(마지막 계산 뒤 입력 위젯 값이 바뀌었는가),
  `admin_visible`(role이 general인데 보이는 `adm_` key 목록과 audit_roles의 금지 라벨에 걸린 라벨 목록).
  계산이 일어난 행동(행동 전후로 `result()`가 새로 생기거나 바뀜)에는 `calc: {"inputs": inputs_from_result, "answers": answers_from_result, "summary_ok": 입력 요약 판정}`을 넣는다.
  `answer` 줄에는 제출한 값과 그 시점의 `dirty`를 넣는다.
- 입력 요약 판정(`summary_ok`): 화면에 `입력 요약`으로 시작하는 묶음이 있고, 그 글에 계산에 쓴 만기, 두 다리 명목(화면 단위 숫자), 라인피 숫자가 들어 있으면 참. 지금 화면에는 없으므로 거짓이 정상이다.

### 3. `ui_eval/score.py` — 채점 (LLM을 쓰지 않는다)

```
python -m ui_eval.score <run>                 # 그 실행의 모든 세션을 채점
python -m ui_eval.score --compare <이전> <이번>   # 채택 판정. 종료 코드 0 = 채택, 1 = 되돌림, 2 = 판정 불가(자료 없음)
```

세션별 판정 (공개·숨김 같은 규칙):

| 지표 | 정의 |
|---|---|
| 과제 성공 | `scored` 항목 모두 `contract.answer_ok` |
| 오입력 발생 | 계산 중 하나라도 `diff_inputs(의도한 입력, 실제 입력)`이 비어 있지 않다. `first.inputs`와 같은 계산은 오입력이 아니다 |
| 놓친 오입력 | 답을 내기 직전 마지막 계산의 입력이 의도한 입력과 다르다 (계산이 없거나 포기한 세션 포함) |
| 중간 수정 | 오입력 발생이면서 놓친 오입력은 아님 |
| 해석 오류 | 마지막 계산의 입력은 맞는데 답이 틀림 (어느 항목이 틀렸는지 함께) |
| 오래된 결과 사용 | 답을 낼 때 `dirty`가 참이거나, `first`가 있는 과제에서 마지막 계산의 입력이 `first.inputs`와 같다 |
| 입력 확인 가능 | 그 세션의 계산 중 `summary_ok`인 비율 |
| 조작 수 | `set` + `click` 횟수 |
| 관리자 영역 노출 | role general인 행동 중 `admin_visible`이 비어 있지 않은 횟수 |
| 혼란 보고 | `note` 횟수, 포기 여부 |

출력:
- `ui_eval/runs/<run>/score.json`: 공개 과제는 세션별 상세 + 페르소나별·전체 합계. 숨긴 과제는 **합계 숫자만**(성공 수, 놓친 오입력 수, 세션 수).
- `ui_eval/runs/<run>/feedback_public.md`: 개선 에이전트(U)가 읽는 글. 공개 과제만. 과제별로 무엇이 틀렸는지(다른 입력 항목의 의도값·실제값, 틀린 답 항목),
  그 세션의 경고, 혼란 보고 원문, 조작 수. 숨긴 과제의 문항·id별 결과·로그 내용은 넣지 않는다.
- `<PRIVATE_DIR>/runs/<run>/score_hidden.json`: 숨긴 과제 세션별 상세.
- 표준 출력: 한 화면짜리 요약 표 (페르소나 × 지표, 공개/숨김 구분).
- 빠진 세션: p1·p2는 세일즈 과제 전부(공개 12 + 숨김 6), p3는 실행에 p3 폴더가 있을 때만 관리자 과제 3건이 기대 세션이다.
  없거나 `answer`·`giveup`으로 끝나지 않은 세션을 `빠진 세션: p1 T07, ...` 한 줄로 찍는다 (숨긴 과제는 id만). 빠진 세션이 있으면 `--compare`는 종료 코드 2.
- 관리자 과제(p3)는 따로 묶어 적고 채택 판정에 넣지 않는다.

채택 판정(`--compare`): 세일즈 페르소나 p1, p2만 쓴다. 아래 숫자는 상수로 한곳에 모아 둔다.

- 주지표: 공개 과제 성공 수 S(p1 + p2 합, 최대 24), 공개 놓친 오입력 수 M.
- 개선 인정: (S가 2 이상 늘고 M이 늘지 않음) 또는 (M이 2 이상 줄고 S가 줄지 않음) 또는 (p1, p2 각각 S가 줄지 않고 M이 늘지 않으면서, 각자 둘 중 하나는 나아짐).
- 숨김 비악화: 숨긴 과제 성공 수가 이전보다 1 넘게 줄지 않고, 숨긴 놓친 오입력 수가 1 넘게 늘지 않는다 (1건은 잡음으로 본다).
- 가드: `audit.json` 통과, 공개·숨김 세션의 관리자 영역 노출 0, `faults.json`의 오래된 결과 미표시 0, 오경고율이 이전보다 높지 않음, 실행 불가 fault 0.
- 채택 = 개선 인정 + 숨김 비악화 + 가드 전부. 결과를 `ui_eval/runs/<이번>/verdict.json`에 쓰고, 표준 출력 마지막 줄에 `채택` 또는 `되돌림: <이유들>`,
  그 윗줄에 `가드 실패` 여부와 `목표 달성` 여부(S가 22 이상, M이 1 이하, 숨긴 성공 10 이상)를 적는다.
- 값 불변(golden)은 별도 게이트라 여기서 보지 않는다.

### 4. `ui_eval/fault_inject.py` — 오입력 주입 (페르소나 없이 화면만 본다)

```
python -m ui_eval.fault_inject <run>      # ui_eval/runs/<run>/faults.json 을 쓴다. 종료 코드 0 (판정은 score --compare가 한다)
```

예시를 불러온 뒤 계약 3번의 key로 값 하나를 틀리게 넣고, 계산 전과 계산 직후에 경고·오류 요소가 뜨는지 본다. 일반 사용자 화면에서 한다.

| id | 예시 | 넣는 오입력 | 뜻 |
|---|---|---|---|
| F1 | 4 | `irs_amt` × 1000 | 원 단위 혼동 |
| F2 | 2 | `cp_amt` × 2 (한쪽 다리만) | 다리 명목 불일치 |
| F3 | 2 | `cr_rate` × 100 | %를 bp로 |
| F4 | 2 | `fee_bp` × 100 | 라인피 단위 |
| F5 | 2 | `cp_spread` = 2500 | 스프레드 단위 |
| F6 | 3 | `cr_amt` ÷ 100 | FX 선도 원화 금액 단위 |
| F7 | 1 | `cr_rate` ÷ 100 | %를 소수로 (BTB) |
| F8 | 1~5 각각 | 계산 뒤 `mat` 변경 (4·5는 `irs_amt` 변경) | 오래된 결과: 결과가 숨겨지거나 `이전 입력 기준` 경고가 있어야 한다 |

정상 대조군: 예시 5개 그대로, 예시 4에서 `mat` 7, 예시 5에서 `irs_amt` 200, 예시 2에서 두 다리 명목을 함께 절반. 경고·오류가 뜨면 오경고.
`faults.json`: fault별 `caught`(계산 전 / 계산 후 / 못 잡음), 포착률, 오경고율, `stale_unflagged` 수, 실행하지 못한 fault(key가 없음 등)는 `not_run` 목록.

### 5. `ui_eval/audit_roles.py` — 관리자 영역 노출 점검

```
python -m ui_eval.audit_roles [<run>]     # 통과면 종료 코드 0. run을 주면 ui_eval/runs/<run>/audit.json 에도 쓴다
```

- 일반 사용자 화면에서 예시 5개와 상품 4개를 차례로 고르고, 계산도 한 번 해서, `adm_` key 요소가 하나도 없고 금지 라벨(은행 파라미터·시뮬레이션·시장 데이터 받기·마스터 편집 위젯의 라벨:
  `CET1 목표비율`, `세후 요구수익률`, `세율`, `FTP`, `KVA 조달`, `한계계수`, `IM 조달`, `경로 수`, `변동성 배수`, `평균회귀`, `블룸버그`, `신용 위험가중치`, `연 PD`, `LGD`, `BA-CVA 업종`)이 입력 위젯으로 보이지 않는지 본다.
- 틀린 비밀번호로 로그인 → 여전히 없음. 맞는 비밀번호(`EVAL_ADMIN_PASSWORD`) → role admin, `adm_` 요소가 1개 이상. 로그아웃 → 다시 없음.
- 비밀값 없이(`admin_password=None`) 실행 → 일반 화면이 오류 없이 뜨고 로그인은 되지 않는다.
- 지금 화면은 역할이 없으므로 **실패가 정상**이다. U0 뒤에 통과해야 한다.

### 6. `ui_eval/smoke.py`, `tests/test_score.py`

- `python -m ui_eval.smoke`: 드라이버 함수를 직접 불러(페르소나 없이) run `smoke`로 공개 과제 T01, T03, T08을 정해진 조작으로 풀고, 로그의 `calc.answers`에서 답을 뽑아 제출하고, 채점까지 돌린다.
  세 과제 모두 성공·오입력 없음이어야 종료 코드 0. 이어서 일부러 틀린 실행(T03의 명목을 다르게, T08을 다시 계산하지 않고 제출)이 실패·놓친 오입력·오래된 결과 사용으로 잡히는지 본다.
  조작은 계약 3번의 key만 쓴다 (U0·U가 화면을 바꿔도 다시 돌아가야 한다. 게이트로 계속 쓴다).
- `python -m tests.test_score`: 화면·엔진 없이 합성 로그로 채점 규칙을 확인한다 (`tests/test_curves.py` 방식, 실패 시 종료 코드 0이 아님).
  성공, 놓친 오입력, 중간 수정, 해석 오류, 오래된 결과(두 경우), 관리자 노출, 그리고 `--compare`의 채택·되돌림 경우(개선 인정 세 갈래, 숨김 악화, 가드 실패 각각)를 넣는다.
  정답 파일을 읽지 않는다 (합성 정답을 쓴다).

## 확인

```
python -m tests.test_score                      # 종료 코드 0
python -m ui_eval.smoke                         # 종료 코드 0
python -m ui_eval.fault_inject smoke            # faults.json 생성, not_run 없음
python -m ui_eval.audit_roles                   # 지금은 실패가 정상. 실패 이유가 "관리자 영역이 보인다"여야 한다 (실행 오류가 아니라)
python -m ui_eval.make_answers --check          # "같음" 2줄 (정답 파일을 건드리지 않았다)
python tools/golden_check.py --ui               # "통과"
python tools/path_gate.py --role harness --base main   # "통과"
```

직접 한 번 써 본다: `python -m ui_eval.driver start dev p1 T04` 부터 `answer`까지 모델의 눈으로 풀어 보고, 출력이 길거나 다음 행동을 알 수 없는 곳을 고친다.

## 하지 않는 것

- 화면을 고쳐서 과제를 쉽게 만드는 것. 하니스는 지금 화면을 있는 그대로 잰다.
- 정답·의도한 입력·함정 설명을 드라이버 출력이나 `feedback_public.md` 밖의 공개 위치에 찍는 것. 숨긴 과제의 것은 저장소 안 어디에도 쓰지 않는다.
- 총괄 소유 파일 수정. 계약에 빠진 것이 있으면 최종 보고에 적는다.

## 최종 보고

- 만든 파일, 커밋 해시
- 확인 명령 결과 (마지막 줄씩)
- 지금 화면의 `faults.json` 요약 (포착률, 오경고율)
- 드라이버를 써 보며 고친 점, 계약에서 모호했던 점, 총괄 소유 파일에 대한 요청
