"""
백테스트 실행 스크립트
Usage:
  python run_backtest.py
  python run_backtest.py --start 2022-01-01 --end 2024-12-31
  python run_backtest.py --universe 100 --hold 20
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from backtester.simple_backtest import run_backtest


def main():
    parser = argparse.ArgumentParser(description="성장주 스크리닝 백테스트")
    parser.add_argument("--start", default="2022-01-01", help="백테스트 시작일 (YYYY-MM-DD)")
    parser.add_argument("--end",   default="2024-12-31", help="백테스트 종료일 (YYYY-MM-DD)")
    parser.add_argument("--universe", type=int, default=200, help="유니버스 크기 (시가총액 상위 N개)")
    parser.add_argument("--quiet", action="store_true", help="진행 출력 최소화")
    args = parser.parse_args()

    print(f"[run_backtest] {args.start} ~ {args.end}  universe={args.universe}")
    print("[run_backtest] 백테스트 실행 중... (시간이 걸릴 수 있습니다)")

    result = run_backtest(
        start=args.start,
        end=args.end,
        universe_size=args.universe,
        verbose=not args.quiet,
    )

    if not result or "error" in result:
        print(f"[run_backtest] 결과 없음: {result}")
        sys.exit(1)

    report_dir = Path(__file__).parent / "reports"
    start_tag = args.start[:4] + "_" + args.start[5:7] + "_" + args.start[8:]
    end_tag   = args.end[:4]   + "_" + args.end[5:7]   + "_" + args.end[8:]
    print(f"\n차트  : {report_dir}/backtest_2022_2024.png")
    print(f"리포트: {report_dir}/backtest_2022_2024.md")


if __name__ == "__main__":
    main()
