"""
yfinance를 이용한 해외 지수, 환율, 원자재 데이터 수집
"""
from __future__ import annotations

from typing import Any

import yfinance as yf


_SYMBOLS = {
    # 미국 지수
    "^GSPC":  "S&P 500",
    "^IXIC":  "나스닥",
    "^DJI":   "다우존스",
    "^VIX":   "VIX(공포지수)",
    # 아시아 지수
    "^N225":  "닛케이225",
    "000001.SS": "상해종합",
    # 환율 (원화 기준)
    "KRW=X":  "USD/KRW",
    "JPYKRW=X": "JPY/KRW",
    # 원자재
    "CL=F":   "WTI 원유",
    "GC=F":   "금",
}


def _safe_pct(current: float, prev: float) -> float:
    if prev and prev != 0:
        return round((current - prev) / prev * 100, 2)
    return 0.0


def get_macro_snapshot() -> dict[str, Any]:
    """
    전일 종가 기준 해외 지표 딕셔너리 반환.
    각 항목: { name, close, change_pct }
    """
    result: dict[str, dict] = {}
    for symbol, name in _SYMBOLS.items():
        try:
            ticker = yf.Ticker(symbol)
            hist = ticker.history(period="5d")
            if hist.empty or len(hist) < 1:
                continue
            close = float(hist["Close"].iloc[-1])
            prev  = float(hist["Close"].iloc[-2]) if len(hist) >= 2 else close
            result[symbol] = {
                "name": name,
                "close": round(close, 2),
                "change_pct": _safe_pct(close, prev),
            }
        except Exception as e:
            print(f"[macro] {symbol} 조회 실패: {e}")
    return result
