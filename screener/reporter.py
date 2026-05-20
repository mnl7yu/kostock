"""
스크리닝 결과 출력:
- CSV 저장
- 마크다운 리포트 저장 (신규/이탈 종목 표시)
- 텔레그램 전송
"""
from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

import config
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

REPORTS_DIR = config.REPORTS_DIR
HISTORY_FILE = config.BASE_DIR / ".cache" / "screening_history.json"


# ── 히스토리 관리 ────────────────────────────────────────────────────────────

def _load_history() -> dict[str, list[str]]:
    """지난 30일 스크리닝 히스토리 로드."""
    if HISTORY_FILE.exists():
        try:
            return json.loads(HISTORY_FILE.read_text())
        except Exception:
            pass
    return {}


def _save_history(history: dict[str, list[str]]) -> None:
    # 30일 초과 항목 제거
    if len(history) > 30:
        oldest_keys = sorted(history.keys())[:-30]
        for k in oldest_keys:
            del history[k]
    HISTORY_FILE.write_text(json.dumps(history, ensure_ascii=False))


def get_entry_exit(tickers_today: list[str], date: str) -> dict[str, list[str]]:
    """신규 진입 / 이탈 종목 계산."""
    history = _load_history()
    yesterday_tickers: list[str] = []
    for prev_date in sorted(history.keys(), reverse=True)[:5]:
        if prev_date < date:
            yesterday_tickers = history[prev_date]
            break

    today_set = set(tickers_today)
    prev_set = set(yesterday_tickers)
    return {
        "new_entry": sorted(today_set - prev_set),
        "exit":      sorted(prev_set - today_set),
        "continued": sorted(today_set & prev_set),
    }


def update_history(tickers: list[str], date: str) -> None:
    history = _load_history()
    history[date] = tickers
    _save_history(history)


# ── CSV 저장 ─────────────────────────────────────────────────────────────────

def save_csv(df: pd.DataFrame, date: str) -> Path:
    path = REPORTS_DIR / f"{date}_screening.csv"
    df.to_csv(path, encoding="utf-8-sig")
    print(f"[reporter] CSV 저장: {path}")
    return path


# ── 마크다운 리포트 ───────────────────────────────────────────────────────────

def _arrow(v: float) -> str:
    return "▲" if v >= 0 else "▼"


def save_markdown(
    df: pd.DataFrame,
    date: str,
    entry_exit: dict[str, list[str]],
    news_map: dict[str, list[str]] | None = None,
) -> Path:
    """
    마크다운 리포트 생성.
    df: 스크리닝 결과 (score 내림차순 정렬)
    """
    formatted = f"{date[:4]}-{date[4:6]}-{date[6:]}"
    lines = [
        f"# 🔍 {formatted} 성장주 스크리닝 리포트\n",
        f"_생성: {datetime.now().strftime('%H:%M')} · 통과 종목: {len(df)}개_\n",
    ]

    # ── 신규/이탈 알림 ────────────────────────────────────────────────────────
    if entry_exit.get("new_entry"):
        new_names = []
        for t in entry_exit["new_entry"]:
            row = df[df.index == t]
            name = row.iloc[0]["name"] if len(row) > 0 and "name" in row.columns else t
            new_names.append(f"**{name}**({t})")
        lines.append(f"## 🆕 신규 진입 종목 ({len(entry_exit['new_entry'])}개)\n")
        lines.append(", ".join(new_names) + "\n")

    if entry_exit.get("exit"):
        lines.append(f"## 🚪 이탈 종목 ({len(entry_exit['exit'])}개)\n")
        lines.append(", ".join(entry_exit["exit"]) + "\n")

    # ── 종목별 상세 ───────────────────────────────────────────────────────────
    lines.append("## 📋 스크리닝 통과 종목 (종합점수 순)\n")

    # 요약 테이블
    lines.append("| 순위 | 종목명 | 코드 | 현재가 | 등락률 | PER | PBR | 외국인연속 | 거래량비 | 52W高比 | 종합점수 |")
    lines.append("|------|--------|------|--------|--------|-----|-----|-----------|----------|---------|---------|")

    for rank, (ticker, row) in enumerate(df.iterrows(), 1):
        is_new = "🆕 " if ticker in entry_exit.get("new_entry", []) else ""
        name = row.get("name", ticker)
        close = row.get("close", 0)
        chg = row.get("change_pct", 0)
        per = row.get("per", 0)
        pbr = row.get("pbr", 0)
        consec = row.get("foreign_consec", 0)
        vol_r = row.get("vol_ratio", 0)
        w52 = row.get("pct_from_52w_high", 0)
        score = row.get("score", 0)

        lines.append(
            f"| {rank} | {is_new}{name} | {ticker} "
            f"| {close:,}원 | {_arrow(chg)}{abs(chg):.1f}% "
            f"| {per:.1f} | {pbr:.2f} "
            f"| {int(consec)}일 | {vol_r:.1f}x "
            f"| {w52:.1f}% | **{score:.0f}점** |"
        )

    # ── 종목별 상세 분석 ──────────────────────────────────────────────────────
    lines.append("\n## 🔎 종목별 상세\n")
    for ticker, row in df.iterrows():
        name = row.get("name", ticker)
        is_new = " 🆕 신규" if ticker in entry_exit.get("new_entry", []) else ""
        score = row.get("score", 0)
        lines.append(f"### {name} ({ticker}){is_new} — {score:.0f}점\n")

        # 조건 체크 뱃지
        badges = []
        if row.get("golden_cross"):   badges.append("✅ 골든크로스")
        else:                          badges.append("❌ 골든크로스")
        if row.get("near_52w_high"):  badges.append("✅ 52주고가근접")
        else:                          badges.append("❌ 52주고가근접")
        if row.get("volume_surge"):   badges.append("✅ 거래량서지")
        else:                          badges.append("⚠️ 거래량보통")
        if row.get("per_ok"):         badges.append("✅ PER적정")
        else:                          badges.append("⚠️ PER이상")
        if row.get("pbr_ok"):         badges.append("✅ PBR적정")
        else:                          badges.append("⚠️ PBR이상")
        if row.get("foreign_consec", 0) >= 3: badges.append(f"✅ 외국인{int(row.get('foreign_consec',0))}일연속매수")
        else:                          badges.append(f"⚠️ 외국인수급미확인")
        if row.get("inst_turn"):      badges.append("✅ 기관전환")

        lines.append("  ".join(badges) + "\n")

        close = row.get("close", 0)
        per = row.get("per", 0)
        pbr = row.get("pbr", 0)
        chg = row.get("change_pct", 0)
        w52 = row.get("pct_from_52w_high", 0)
        rsi = row.get("rsi", 0)
        mom = row.get("momentum_20d", 0)
        marcap = row.get("marcap", 0)

        lines.append(
            f"- **현재가**: {close:,}원 ({_arrow(chg)}{abs(chg):.1f}%)  "
            f"**시가총액**: {marcap/1e12:.2f}조  "
            f"**PER**: {per:.1f}배  **PBR**: {pbr:.2f}배\n"
        )
        lines.append(
            f"- **20일 모멘텀**: {_arrow(mom)}{abs(mom):.1f}%  "
            f"**RSI(14)**: {rsi:.0f}  "
            f"**52주고가比**: {w52:.1f}%\n"
        )

        # 뉴스
        if news_map and ticker in news_map:
            lines.append("- **최근 뉴스**:")
            for headline in news_map[ticker][:3]:
                lines.append(f"  - {headline}")
            lines.append("")

    lines.append("\n---")
    lines.append(
        "_스크리닝 조건: 골든크로스(5MA>20MA) + 52주고가 -20%이내 + PER 5~40 + PBR 0.3~5_\n"
        "_수급 데이터: 외국인 연속순매수 · 기관 순매수전환 (네이버 금융)_"
    )

    path = REPORTS_DIR / f"{date}_screening.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[reporter] MD 저장: {path}")
    return path


# ── 텔레그램 전송 ────────────────────────────────────────────────────────────

def _tg_send(token: str, chat: str, text: str) -> None:
    """4096자 초과 시 분할 전송. parse_mode 없이 순수 텍스트."""
    import requests as req
    for i in range(0, len(text), 4096):
        req.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": text[i:i+4096]},
            timeout=15,
        )


def send_telegram_result(
    df: pd.DataFrame,
    date: str,
    entry_exit: dict[str, list[str]],
    report_path: Path,
) -> None:
    """텔레그램으로 스크리닝 결과 전송 (텍스트 2개 메시지)."""
    TOKEN = config.TELEGRAM_BOT_TOKEN
    CHAT  = config.TELEGRAM_CHAT_ID
    formatted = f"{date[:4]}-{date[4:6]}-{date[6:]}"

    # ── 메시지 1: 요약 ────────────────────────────────────────────────────────
    lines = [f"🔍 {formatted} 성장주 스크리닝  {len(df)}개 통과"]
    lines.append("─" * 30)

    if entry_exit.get("new_entry"):
        new_names = []
        for t in entry_exit["new_entry"][:6]:
            row = df[df.index == t]
            n = row.iloc[0]["name"] if len(row) > 0 and "name" in row.columns else t
            new_names.append(n)
        extra = f" 외 {len(entry_exit['new_entry'])-6}개" if len(entry_exit["new_entry"]) > 6 else ""
        lines.append(f"🆕 신규 진입: {', '.join(new_names)}{extra}")

    if entry_exit.get("exit"):
        lines.append(f"🚪 이탈: {len(entry_exit['exit'])}개")

    lines.append("")
    lines.append("종합점수 TOP 10")
    lines.append("─" * 30)

    for rank, (ticker, row) in enumerate(df.head(10).iterrows(), 1):
        name  = row.get("name", ticker)
        score = row.get("score", 0)
        chg   = row.get("change_pct", 0)
        consec = int(row.get("foreign_consec", 0))
        per   = row.get("per", 0)
        close = row.get("close", 0)
        is_new = "🆕 " if ticker in entry_exit.get("new_entry", []) else "   "
        lines.append(
            f"{rank:2}. {is_new}{name}({ticker})\n"
            f"     {close:,.0f}원  {_arrow(chg)}{abs(chg):.1f}%  "
            f"PER {per:.0f}  외국인{consec}일  {score:.0f}점"
        )

    try:
        _tg_send(TOKEN, CHAT, "\n".join(lines))
        print("[reporter] 텔레그램 요약 전송 완료")
    except Exception as e:
        print(f"[reporter] 텔레그램 요약 전송 실패: {e}")

    # ── 메시지 2: 종목별 상세 ────────────────────────────────────────────────
    detail_lines = [f"📋 {formatted} 종목별 상세"]
    detail_lines.append("─" * 30)

    for ticker, row in df.iterrows():
        name   = row.get("name", ticker)
        score  = row.get("score", 0)
        close  = row.get("close", 0)
        chg    = row.get("change_pct", 0)
        per    = row.get("per", 0)
        pbr    = row.get("pbr", 0)
        rsi    = row.get("rsi", 0)
        mom    = row.get("momentum_20d", 0)
        vol_r  = row.get("vol_ratio", 0)
        w52    = row.get("pct_from_52w_high", 0)
        consec = int(row.get("foreign_consec", 0))
        marcap = row.get("marcap", 0)

        checks = []
        if row.get("golden_cross"):  checks.append("✅골든크로스")
        if row.get("near_52w_high"): checks.append("✅52W근접")
        if row.get("volume_surge"):  checks.append("✅거래량급증")
        if consec >= 3:              checks.append(f"✅외국인{consec}일")
        if row.get("inst_turn"):     checks.append("✅기관전환")

        detail_lines.append(
            f"\n{name} ({ticker})  {score:.0f}점\n"
            f"  {close:,.0f}원  {_arrow(chg)}{abs(chg):.1f}%  "
            f"시총 {marcap/1e12:.1f}조\n"
            f"  PER {per:.1f}  PBR {pbr:.2f}  RSI {rsi:.0f}\n"
            f"  모멘텀20 {_arrow(mom)}{abs(mom):.1f}%  "
            f"거래량비 {vol_r:.1f}x  52W고가比 {w52:.1f}%\n"
            f"  {' '.join(checks)}"
        )

    try:
        _tg_send(TOKEN, CHAT, "\n".join(detail_lines))
        print("[reporter] 텔레그램 상세 전송 완료")
    except Exception as e:
        print(f"[reporter] 텔레그램 상세 전송 실패: {e}")
