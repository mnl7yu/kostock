"""
Claude API를 이용한 시황 분석 리포트 생성
- 프롬프트 캐싱으로 system prompt 비용 절감
- morning: 오전 시황 브리핑 / closing: 마감 종합 분석
"""
from __future__ import annotations

import json
from typing import Any

import anthropic

import config

_CLIENT: anthropic.Anthropic | None = None


def _client() -> anthropic.Anthropic:
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    return _CLIENT


# ── 시스템 프롬프트 (캐시 대상) ────────────────────────────────────────────────

_SYSTEM_PROMPT = """당신은 10년 경력의 국내 증권사 수석 애널리스트입니다.
KOSPI/KOSDAQ 시장 데이터를 기반으로 기관·외국인 수급, 업종 로테이션,
테마 흐름, 거시 지표 연동성을 종합해 전문적이고 날카로운 시황 리포트를 작성합니다.

[작성 원칙]
1. 수치는 반드시 인용하고 전일 대비 변화를 명확히 표기하세요.
2. 단순 데이터 나열이 아닌 '왜 그랬는가', '앞으로 어떻게 될 것인가'를 분석하세요.
3. 관심종목은 개별 리스크와 기회 요인을 구체적으로 언급하세요.
4. 마크다운 형식으로 작성하되, 이모지를 적절히 활용해 가독성을 높이세요.
5. 투자 의사결정에 실질적으로 도움이 되는 인사이트를 제공하세요.
6. 불확실한 내용은 '~가능성', '~예상' 등으로 명확히 표현하세요."""


# ── 데이터 → 텍스트 변환 헬퍼 ─────────────────────────────────────────────────

def _arrow(pct: float) -> str:
    return "▲" if pct >= 0 else "▼"


def _fmt_index(index_data: dict) -> str:
    lines = []
    for name, d in index_data.items():
        if not d:
            continue
        lines.append(
            f"- **{name}**: {d['close']:,.2f}pt "
            f"({_arrow(d['change_pct'])} {abs(d['change_pct']):.2f}%)"
        )
    return "\n".join(lines)


def _fmt_macro(macro: dict) -> str:
    lines = []
    for _, d in macro.items():
        if not d:
            continue
        lines.append(
            f"- **{d['name']}**: {d['close']:,} "
            f"({_arrow(d['change_pct'])} {abs(d['change_pct']):.2f}%)"
        )
    return "\n".join(lines)


def _fmt_movers(movers: dict, top_n: int = 5) -> str:
    gainers = movers.get("gainers", [])[:top_n]
    losers = movers.get("losers", [])[:top_n]
    g_str = ", ".join(
        f"{s['name']}({s['change_pct']:+.1f}%)" for s in gainers
    )
    l_str = ", ".join(
        f"{s['name']}({s['change_pct']:+.1f}%)" for s in losers
    )
    return f"상승: {g_str}\n하락: {l_str}"


def _fmt_breadth(breadth: dict) -> str:
    kb = breadth.get("KOSPI", {})
    kd = breadth.get("KOSDAQ", {})
    lines = []
    if kb:
        lines.append(
            f"KOSPI 상승 {kb.get('advance',0)} / 하락 {kb.get('decline',0)} / 보합 {kb.get('unchanged',0)}"
        )
    if kd:
        lines.append(
            f"KOSDAQ 상승 {kd.get('advance',0)} / 하락 {kd.get('decline',0)} / 보합 {kd.get('unchanged',0)}"
        )
    return "\n".join(lines)


def _fmt_sectors(sectors: list[dict], top_n: int = 5) -> str:
    if not sectors:
        return "데이터 없음"
    top = sectors[:top_n]
    bot = sectors[-top_n:]
    t_str = ", ".join(f"{s['name']}({s['change_pct']:+.1f}%)" for s in top)
    b_str = ", ".join(f"{s['name']}({s['change_pct']:+.1f}%)" for s in reversed(bot))
    return f"강세 업종: {t_str}\n약세 업종: {b_str}"


def _fmt_watchlist(stocks: list[dict]) -> str:
    if not stocks:
        return "관심종목 없음"
    lines = []
    for s in stocks:
        lines.append(
            f"- **{s.get('name','?')}** ({s.get('ticker','?')}): "
            f"{s.get('close', 0):,}원 ({_arrow(s.get('change_pct', 0))} "
            f"{abs(s.get('change_pct', 0)):.2f}%)"
        )
    return "\n".join(lines)


# ── 프롬프트 빌더 ─────────────────────────────────────────────────────────────

def _build_morning_prompt(market: dict, macro: dict, news: list[dict]) -> str:
    date_str = market.get("date", "")
    formatted = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}" if len(date_str) == 8 else date_str

    news_str = "\n".join(f"- {n['title']}" for n in news[:8]) if news else "수집된 뉴스 없음"

    return f"""아래 데이터를 바탕으로 **{formatted} 오전 시황 브리핑**을 작성해주세요.

## 📊 전일 국내 증시 마감
{_fmt_index(market.get('index', {}))}

## 🌐 해외 시장 동향
{_fmt_macro(macro)}

## 📰 주요 뉴스 헤드라인
{news_str}

## 🔍 관심종목 전일 종가
{_fmt_watchlist(market.get('watchlist', []))}

## 시장 폭 (전일)
{_fmt_breadth(market.get('breadth', {}))}

---
[리포트 구성]
1. **오전 시황 요약** (3~4줄 핵심 정리)
2. **해외 시장 영향 분석** (미국·아시아 시장이 오늘 국내 증시에 미칠 영향)
3. **환율·원자재 체크** (투자 포인트)
4. **관심종목 오전 전략** (매 종목별 간략 코멘트)
5. **오늘 주목할 포인트** (이벤트, 발표, 수급 포인트)"""


def _build_closing_prompt(market: dict, macro: dict, news: list[dict]) -> str:
    date_str = market.get("date", "")
    formatted = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}" if len(date_str) == 8 else date_str

    news_str = "\n".join(f"- {n['title']}" for n in news[:8]) if news else "수집된 뉴스 없음"

    return f"""아래 데이터를 바탕으로 **{formatted} 마감 종합 분석 리포트**를 작성해주세요.

## 📊 오늘 국내 증시
{_fmt_index(market.get('index', {}))}

## 시장 폭
{_fmt_breadth(market.get('breadth', {}))}

## 🚀 KOSPI 상위 등락 종목
{_fmt_movers(market.get('movers', {}).get('KOSPI', {}), 7)}

## 💹 KOSDAQ 상위 등락 종목
{_fmt_movers(market.get('movers', {}).get('KOSDAQ', {}), 7)}

## 🏭 업종 동향
{_fmt_sectors(market.get('sectors', []), 5)}

## 🌐 해외 지표
{_fmt_macro(macro)}

## 📰 오늘의 주요 뉴스
{news_str}

## 🔍 관심종목 종가
{_fmt_watchlist(market.get('watchlist', []))}

---
[리포트 구성]
1. **마감 총평** (오늘 장의 핵심 흐름 3~5줄)
2. **수급 분석** (외국인·기관 추정 동향, 테마별 수급)
3. **업종 로테이션** (강세/약세 업종 배경 분석)
4. **관심종목 심층 분석** (각 종목별 오늘의 움직임 해석, 단기 전망)
5. **내일 시장 전망** (시나리오별 전략, 주목할 이벤트)
6. **투자 전략 제언** (리스크 요인 포함)"""


# ── 메인 함수 ─────────────────────────────────────────────────────────────────

def _data_only_report(market: dict[str, Any], macro: dict[str, Any], mode: str) -> str:
    """Claude API 없이 수집 데이터만으로 구조화된 리포트 생성."""
    date_str = market.get("date", "")
    formatted = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}" if len(date_str) == 8 else date_str
    label = "오전 시황 브리핑" if mode == "morning" else "마감 종합 분석"

    lines = [f"## 📊 {formatted} {label}\n"]

    # 국내 지수
    lines.append("### 🇰🇷 국내 지수")
    for name, d in market.get("index", {}).items():
        if d and d.get("close"):
            arr = "▲" if d["change_pct"] >= 0 else "▼"
            lines.append(f"- **{name}**: {d['close']:,.2f}pt  {arr} {abs(d['change_pct']):.2f}%")

    # 시장 폭
    lines.append("\n### 📊 시장 폭")
    for mkt, b in market.get("breadth", {}).items():
        if b:
            lines.append(
                f"- **{mkt}**: 상승 {b.get('advance',0)} / 하락 {b.get('decline',0)} / 보합 {b.get('unchanged',0)}"
            )

    # 해외 지표
    lines.append("\n### 🌐 해외 지표")
    for _, d in macro.items():
        if d and d.get("close"):
            arr = "▲" if d["change_pct"] >= 0 else "▼"
            lines.append(f"- **{d['name']}**: {d['close']:,}  {arr} {abs(d['change_pct']):.2f}%")

    # 상위 등락 (KOSPI)
    movers_k = market.get("movers", {}).get("KOSPI", {})
    gainers = movers_k.get("gainers", [])[:5]
    losers  = movers_k.get("losers", [])[:5]
    if gainers:
        lines.append("\n### 🚀 KOSPI 상승 TOP 5")
        for s in gainers:
            lines.append(f"- {s['name']} ({s['ticker']}): {s['close']:,}원  ▲{s['change_pct']:.2f}%")
    if losers:
        lines.append("\n### 📉 KOSPI 하락 TOP 5")
        for s in losers:
            lines.append(f"- {s['name']} ({s['ticker']}): {s['close']:,}원  ▼{abs(s['change_pct']):.2f}%")

    # 관심종목
    wl = market.get("watchlist", [])
    if wl:
        lines.append("\n### 🔍 관심종목")
        for s in wl:
            arr = "▲" if s.get("change_pct", 0) >= 0 else "▼"
            lines.append(
                f"- **{s.get('name','?')}** ({s.get('ticker','')}): "
                f"{s.get('close',0):,}원  {arr}{abs(s.get('change_pct',0)):.2f}%  "
                f"거래량 {s.get('volume',0):,}"
            )

    lines.append("\n---")
    lines.append("_⚠️ ANTHROPIC_API_KEY 미설정 — AI 분석 없이 데이터 요약만 제공됩니다._")
    lines.append("_.env 파일에 ANTHROPIC_API_KEY=sk-ant-... 를 추가하면 AI 전문 분석이 활성화됩니다._")

    return "\n".join(lines)


def generate_report(
    market: dict[str, Any],
    macro: dict[str, Any],
    news: list[dict],
    mode: str = "closing",
) -> str:
    """
    Claude API로 분석 리포트 생성.
    API 키 없으면 데이터 요약 리포트로 폴백.
    mode: 'morning' | 'closing'
    """
    if not config.ANTHROPIC_API_KEY:
        print("[analyst] ANTHROPIC_API_KEY 없음 → 데이터 요약 리포트로 대체")
        return _data_only_report(market, macro, mode)

    if mode == "morning":
        user_prompt = _build_morning_prompt(market, macro, news)
    else:
        user_prompt = _build_closing_prompt(market, macro, news)

    response = _client().messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        system=[
            {
                "type": "text",
                "text": _SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_prompt}],
    )

    return response.content[0].text
