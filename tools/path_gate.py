"""경로 게이트: 이 체크아웃에서 바뀐 파일이 역할의 허용 목록 안에 있는지 확인한다.

    python tools/path_gate.py --role ui               # main 대비 커밋 + 작업 트리 변경(추적 안 된 새 파일 포함)
    python tools/path_gate.py --role ui --base b-h    # 태그·커밋도 기준이 된다 (그 뒤에 바뀐 파일만 본다)
    python tools/path_gate.py --role implementer --base main
    python tools/path_gate.py --list

위반이 있으면 목록을 출력하고 종료 코드 1. 병합 전 게이트로 쓴다.
gitignore된 파일(스냅샷, local/ 등)은 git이 보고하지 않으므로 대상이 아니다.
"""
import argparse
import fnmatch
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import git, utf8_stdout  # noqa: E402

# 어떤 역할도 바꿀 수 없다 (사용자 승인·총괄만)
FORBIDDEN = ["docs/DECISIONS.md", "tools/*", "tools/**", ".gitignore", ".env", ".streamlit/secrets.toml",
             "market_snapshot.json", "linefee/market_snapshot.json", "local/*", "local/**",
             # Track B: 총괄이 쓴 평가 계약·과제·정답 생성·가명 샘플 마스터, 로컬 전용 데이터
             "ui_eval/contract.py", "ui_eval/make_answers.py", "ui_eval/tasks_public.yaml",
             "data/counterparties.sample.csv", "data/counterparties.csv", "data/bank_params.json"]

ROLES = {
    # Track A
    "reviewer": ["reviews/findings_*.jsonl"],                    # R1, R2: 자기 발견사항 파일만
    "verifier": ["reviews/ledger.jsonl"],                        # V: tools/ledger.py verify 로만 기록
    "closer": ["reviews/ledger.jsonl"],                          # C: tools/ledger.py close 로만 기록
    "a1-run": ["reviews/**"],                                    # Run A1 전체가 끝난 뒤의 게이트 (발견사항 + 원장 + TRIAGE)
    "implementer": ["linefee/**", "app.py", "tests/**", "docs/METHODOLOGY_v1.md", "DESIGN.md", "*.py",
                    "requirements.txt", "reg/**", "README.md"],               # I: accept 항목이 가리키는 파일 (DECISIONS·tools·ui_eval 제외)
    # Track C (최종 점검): 섹터 리뷰어는 reviewer, V1·V2는 verifier 역할을 쓴다
    "critic": ["reviews/C_COVERAGE.md"],                          # K: 누락 점검 보고서만
    "c-run": ["reviews/**"],                                      # Run C 전체가 끝난 뒤의 게이트
    # Track B
    "harness": ["ui_eval/**", "tests/test_score.py"],            # H: 평가 하니스 (위 금지 목록의 ui_eval 파일 3개 제외)
    "ui": ["app.py", "ui/**", ".streamlit/secrets.toml.example",  # U0, U: 화면만. 엔진·하니스 금지
           "tests/test_app.py", "requirements.txt", "README.md"],  # 화면 테스트·실행 안내는 화면과 함께 맞춘다
    "persona": [],                                               # P: 아무것도 바꾸지 않는다 (로그는 gitignore된 ui_eval/runs/)
}
# ui_eval/ 은 harness만. implementer의 "*.py"는 루트의 .py만 가리킨다 (fnmatch의 *는 /도 넘으므로 아래에서 따로 처리)


def match(path: str, pattern: str) -> bool:
    if pattern.endswith("/**"):
        return path.startswith(pattern[:-2])
    if "/" not in pattern:
        return "/" not in path and fnmatch.fnmatchcase(path, pattern)
    head, tail = pattern.rsplit("/", 1)
    return path.rsplit("/", 1)[0] == head and fnmatch.fnmatchcase(path.rsplit("/", 1)[1], tail) if "/" in path else False


def changed(base: str) -> list:
    files = set()
    try:
        files |= set(git("diff", "--name-only", f"{base}...HEAD").split("\n"))
    except Exception:
        sys.exit(f"기준 브랜치 {base!r}와 비교할 수 없습니다.")
    files |= set(git("diff", "--name-only", "HEAD").split("\n"))                       # 스테이지 + 작업 트리
    files |= set(git("ls-files", "--others", "--exclude-standard").split("\n"))        # 추적 안 된 새 파일
    return sorted(f for f in files if f)


def main():
    utf8_stdout()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--role", choices=sorted(ROLES))
    ap.add_argument("--base", default="main")
    ap.add_argument("--list", action="store_true", help="역할별 허용 목록 출력")
    a = ap.parse_args()
    if a.list or not a.role:
        for role, pats in ROLES.items():
            print(f"{role:<12} {', '.join(pats) or '(없음)'}")
        print(f"{'(전체 금지)':<12} {', '.join(FORBIDDEN)}")
        return 0
    allowed = ROLES[a.role]
    files = changed(a.base)
    bad = []
    for f in files:
        if any(match(f, p) for p in FORBIDDEN):
            bad.append((f, "전체 금지"))
        elif not any(match(f, p) for p in allowed):
            bad.append((f, "허용 목록 밖"))
    print(f"역할 {a.role}, 기준 {a.base}: 바뀐 파일 {len(files)}개")
    for f in files:
        print("  " + f)
    if bad:
        print(f"\n위반 {len(bad)}개:")
        for f, why in bad:
            print(f"  {f}  ({why})")
        return 1
    print("통과")
    return 0


if __name__ == "__main__":
    sys.exit(main())
