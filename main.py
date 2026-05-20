#!/usr/bin/env python3
"""
한국 주식 자동 분석 시스템
Usage:
    python main.py morning   # 오전 시황 브리핑 (08:30)
    python main.py closing   # 마감 종합 분석  (15:40)
    python main.py morning --no-telegram   # 텔레그램 전송 없이 파일만 저장
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from datetime import datetime
from pathlib import Path

import config
from analyzers.claude_analyst import generate_report
from collectors.macro import get_macro_snapshot
from collectors.market_data import get_market_snapshot
from collectors.news import get_market_news
from notifiers.telegram import send_report


# ── 유틸 ─────────────────────────────────────────────────────────────────────

def load_watchlist() -> list[dict]:
    with open(config.WATCHLIST_PATH, encoding="utf-8") as f:
        return json.load(f)["stocks"]


def save_report(content: str, mode: str) -> Path:
    """리포트를 reports/{YYYYMMDD}_{mode}.md 로 저장."""
    today = datetime.today().strftime("%Y%m%d")
    filename = f"{today}_{mode}.md"
    path = config.REPORTS_DIR / filename
    path.write_text(content, encoding="utf-8")
    print(f"[main] 리포트 저장: {path}")
    return path


def build_header(mode: str, date: str) -> str:
    label = "오전 시황 브리핑" if mode == "morning" else "마감 종합 분석"
    formatted = f"{date[:4]}-{date[4:6]}-{date[6:]}" if len(date) == 8 else date
    now = datetime.now().strftime("%H:%M")
    return f"# 📊 {formatted} {label}\n_생성: {now}_\n\n"


# ── 메인 파이프라인 ───────────────────────────────────────────────────────────

def run(mode: str, send_telegram: bool = True) -> None:
    print(f"\n{'='*50}")
    print(f"[main] 모드: {mode.upper()}  |  텔레그램: {send_telegram}")
    print(f"{'='*50}\n")

    config.validate()

    # 1. 데이터 수집
    print("[main] 1/4 시장 데이터 수집 중...")
    watchlist = load_watchlist()
    market = get_market_snapshot(watchlist, mode=mode)

    print("[main] 2/4 매크로 데이터 수집 중...")
    macro = get_macro_snapshot()

    print("[main] 3/4 뉴스 수집 중...")
    news = get_market_news(limit=10)

    # 2. Claude 분석
    print("[main] 4/4 Claude 분석 리포트 생성 중...")
    report_body = generate_report(market, macro, news, mode=mode)

    # 3. 헤더 조합 + 저장
    header = build_header(mode, market.get("date", ""))
    full_report = header + report_body
    report_path = save_report(full_report, mode)

    # 4. 텔레그램 전송
    if send_telegram:
        print("[main] 텔레그램 전송 중...")
        send_report(full_report, report_path)
        print("[main] 텔레그램 전송 완료!")

    print(f"\n[main] ✅ 완료! 리포트: {report_path}\n")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="한국 주식 자동 분석 시스템")
    parser.add_argument(
        "mode",
        choices=["morning", "closing"],
        help="morning=오전시황(08:30) / closing=마감분석(15:40)",
    )
    parser.add_argument(
        "--no-telegram",
        action="store_true",
        help="텔레그램 전송을 생략하고 파일만 저장",
    )
    args = parser.parse_args()

    try:
        run(args.mode, send_telegram=not args.no_telegram)
    except Exception:
        print("\n[main] ❌ 오류 발생:")
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
