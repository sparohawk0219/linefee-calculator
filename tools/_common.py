"""tools 공통: 저장소 경로, 로컬 전용 디렉터리, 출력 인코딩."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent      # 이 체크아웃(메인 또는 worktree)


def utf8_stdout():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass


def git(*args: str, cwd: Path = ROOT) -> str:
    return subprocess.run(["git", "-c", "core.quotepath=false", *args], cwd=cwd, capture_output=True,
                          text=True, encoding="utf-8", check=True).stdout


def main_root() -> Path:
    """메인 체크아웃 경로. worktree에서 실행해도 메인을 가리킨다 (로컬 전용 파일은 메인에만 있다)."""
    try:
        common = Path(git("rev-parse", "--path-format=absolute", "--git-common-dir").strip())
        return common.parent
    except Exception:
        return ROOT


def local_dir() -> Path:
    """gitignore된 로컬 전용 디렉터리 (golden 기준값 등). 항상 메인 체크아웃의 local/."""
    d = main_root() / "local"
    d.mkdir(exist_ok=True)
    return d
