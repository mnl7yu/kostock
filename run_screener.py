"""
성장주 스크리닝 실행 스크립트
Usage:
  python run_screener.py
  python run_screener.py --mode strict
  python run_screener.py --mode loose --min-marcap 300
  python run_screener.py --no-telegram
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import config

# 주말이면 자동 종료 (launchd는 요일 필터 미지원)
if datetime.today().weekday() >= 5 and "--force" not in sys.argv:
    print(f"[run_screener] 주말({datetime.today().strftime('%A')}) — 스크리닝 건너뜀")
    sys.exit(0)
from screener.screener import run_screening
from screener.reporter import save_csv, save_markdown, get_entry_exit, update_history, send_telegram_result


def main():
    parser = argparse.ArgumentParser(description="성장주 스크리닝")
    parser.add_argument("--mode", choices=["strict", "moderate", "loose"], default="moderate")
    parser.add_argument("--min-marcap", type=int, default=500, help="최소 시가총액 (억원)")
    parser.add_argument("--max-candidates", type=int, default=150, help="Stage 2 처리 최대 종목수")
    parser.add_argument("--no-telegram", action="store_true", help="텔레그램 전송 생략")
    parser.add_argument("--no-news", action="store_true", help="뉴스 수집 생략")
    args = parser.parse_args()

    date = datetime.today().strftime("%Y%m%d")
    print(f"[run_screener] {date} 스크리닝 시작  mode={args.mode}  min_marcap={args.min_marcap}억")

    # 스크리닝 실행
    df = run_screening(
        mode=args.mode,
        min_marcap_bil=args.min_marcap,
        max_candidates=args.max_candidates,
        fetch_news=not args.no_news,
        verbose=True,
    )

    if df.empty:
        print("[run_screener] 통과 종목 없음. 종료.")
        sys.exit(0)

    news_map = df.attrs.get("news_map", {})
    tickers_today = df.index.tolist()

    # 신규/이탈 계산
    entry_exit = get_entry_exit(tickers_today, date)
    update_history(tickers_today, date)

    # 저장
    csv_path = save_csv(df, date)
    md_path  = save_markdown(df, date, entry_exit, news_map)

    print(f"[run_screener] 결과 저장 완료")
    print(f"  CSV : {csv_path}")
    print(f"  MD  : {md_path}")
    print(f"  신규진입: {entry_exit['new_entry']}")
    print(f"  이탈    : {entry_exit['exit']}")

    # 텔레그램
    if not args.no_telegram:
        try:
            send_telegram_result(df, date, entry_exit, md_path)
            print("[run_screener] 텔레그램 전송 완료")
        except Exception as e:
            print(f"[run_screener] 텔레그램 전송 실패: {e}")

    # GitHub 자동 푸시
    try:
        from push_reports import push_reports
        push_reports(date)
    except Exception as e:
        print(f"[run_screener] GitHub 푸시 실패: {e}")

    print(f"[run_screener] 완료. 통과 종목 {len(df)}개")


if __name__ == "__main__":
    main()
