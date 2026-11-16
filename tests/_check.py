"""검증 스크립트 공용 단정 도구. 실패를 모아 두었다가 finish()에서 종료 코드 1로 끝낸다."""
import sys


class Checker:
    def __init__(self):
        self.fails: list[str] = []
        self.n = 0

    def ok(self, label: str, cond: bool, detail: str = "") -> bool:
        self.n += 1
        if not cond:
            self.fails.append(label)
        print(f"    {'ok  ' if cond else '실패'} {label}" + (f" ({detail})" if detail else ""))
        return cond

    def near(self, label: str, got: float, want: float, tol: float) -> bool:
        """|got - want| <= tol. 기대값은 파일 위쪽 EXPECTED 표에서 가져온다."""
        return self.ok(label, abs(got - want) <= tol, f"{got:.4f}, 기대 {want:.4f} ± {tol}")

    def finish(self) -> None:
        print(f"단정 {self.n}개, 실패: " + (", ".join(self.fails) if self.fails else "없음"))
        sys.exit(1 if self.fails else 0)
