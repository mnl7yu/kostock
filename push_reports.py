"""스크리닝 결과를 GitHub에 자동 커밋·푸시."""
from __future__ import annotations
import subprocess
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent


def push_reports(date: str | None = None) -> bool:
    date = date or datetime.today().strftime("%Y%m%d")

    files = [
        f"reports/{date}_screening.csv",
        f"reports/{date}_screening.md",
        "reports/backtest_2022_2024.md",
    ]
    # 존재하는 파일만
    existing = [f for f in files if (BASE_DIR / f).exists()]
    if not existing:
        print("[push] 커밋할 파일 없음")
        return False

    def run(cmd):
        return subprocess.run(cmd, cwd=BASE_DIR, capture_output=True, text=True)

    # git add
    run(["git", "add"] + existing)

    # git commit
    r = run(["git", "commit", "-m", f"[auto] {date} 스크리닝 결과 업데이트"])
    if r.returncode != 0 and "nothing to commit" in r.stdout + r.stderr:
        print("[push] 변경사항 없음, 스킵")
        return True
    if r.returncode != 0:
        print(f"[push] commit 실패: {r.stderr}")
        return False

    # git push
    r = run(["git", "push", "origin", "main"])
    if r.returncode != 0:
        print(f"[push] push 실패: {r.stderr}")
        return False

    print(f"[push] GitHub 푸시 완료: {', '.join(existing)}")
    return True


if __name__ == "__main__":
    push_reports()
