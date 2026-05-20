"""
스크리닝 필터 — 기술적/수급/밸류에이션/성장성 조건
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from typing import Any


# ── 기술적 지표 계산 ─────────────────────────────────────────────────────────

def compute_indicators(df: pd.DataFrame) -> dict[str, Any]:
    """
    OHLCV 히스토리에서 주요 기술적 지표 계산.
    df: Close, Volume, High, Low 포함 DataFrame (최소 60일)
    """
    result: dict[str, Any] = {}
    if df is None or len(df) < 21:
        return result

    close = df["Close"].astype(float)
    volume = df["Volume"].astype(float)

    # 이동평균
    ma5 = close.rolling(5).mean()
    ma20 = close.rolling(20).mean()
    ma60 = close.rolling(60).mean() if len(close) >= 60 else close.rolling(len(close)).mean()

    result["ma5"] = ma5.iloc[-1]
    result["ma20"] = ma20.iloc[-1]
    result["ma60"] = ma60.iloc[-1]

    # 골든크로스: 5일선 > 20일선, 최근 5일 내에 교차 발생
    result["golden_cross"] = bool(ma5.iloc[-1] > ma20.iloc[-1])
    result["golden_cross_recent"] = False
    if len(ma5) >= 6:
        prev_diff = ma5.iloc[-6] - ma20.iloc[-6]
        curr_diff = ma5.iloc[-1] - ma20.iloc[-1]
        result["golden_cross_recent"] = bool(prev_diff <= 0 and curr_diff > 0)

    # 52주 최고가 대비 위치
    high_52w = df["High"].astype(float).rolling(min(252, len(df))).max().iloc[-1]
    low_52w = df["Low"].astype(float).rolling(min(252, len(df))).min().iloc[-1]
    result["high_52w"] = high_52w
    result["low_52w"] = low_52w
    result["pct_from_52w_high"] = (close.iloc[-1] - high_52w) / high_52w * 100  # 음수
    result["near_52w_high"] = result["pct_from_52w_high"] >= -20.0   # -20% 이내

    # 거래량 서지
    vol_20avg = volume.rolling(20).mean().iloc[-1]
    vol_today = volume.iloc[-1]
    result["vol_20avg"] = vol_20avg
    result["vol_ratio"] = vol_today / vol_20avg if vol_20avg > 0 else 0
    result["volume_surge"] = result["vol_ratio"] >= 1.5

    # 가격 모멘텀 (20일 수익률)
    result["momentum_20d"] = (close.iloc[-1] / close.iloc[-20] - 1) * 100 if len(close) >= 20 else 0

    # RSI (14일)
    result["rsi"] = _calc_rsi(close, 14)

    # 현재가
    result["close"] = close.iloc[-1]
    result["open"] = df["Open"].iloc[-1] if "Open" in df.columns else close.iloc[-1]

    return result


def _calc_rsi(prices: pd.Series, period: int = 14) -> float:
    if len(prices) < period + 1:
        return 50.0
    delta = prices.diff().dropna()
    gain = delta.clip(lower=0).rolling(period).mean().iloc[-1]
    loss = (-delta.clip(upper=0)).rolling(period).mean().iloc[-1]
    if loss == 0:
        return 100.0
    rs = gain / loss
    return round(100 - 100 / (1 + rs), 1)


# ── 개별 필터 함수 ────────────────────────────────────────────────────────────

def check_golden_cross(ind: dict) -> bool:
    """5MA > 20MA"""
    return ind.get("golden_cross", False)


def check_near_52w_high(ind: dict, threshold: float = -20.0) -> bool:
    """52주 고가 대비 threshold% 이내"""
    return ind.get("pct_from_52w_high", -100) >= threshold


def check_volume_surge(ind: dict, multiplier: float = 1.5) -> bool:
    """거래량 20일 평균 대비 multiplier배 이상"""
    return ind.get("vol_ratio", 0) >= multiplier


def check_per(per: float, min_per: float = 5, max_per: float = 40) -> bool:
    """PER 범위 체크"""
    return min_per <= per <= max_per if per > 0 else False


def check_pbr(pbr: float, min_pbr: float = 0.3, max_pbr: float = 5.0) -> bool:
    """PBR 범위 체크"""
    return min_pbr <= pbr <= max_pbr if pbr > 0 else False


def check_eps_growth(eps_current: float, eps_prev: float, threshold: float = 10.0) -> bool:
    """EPS YoY 성장률 threshold% 이상"""
    if eps_prev <= 0 or eps_current <= 0:
        return False
    growth = (eps_current - eps_prev) / abs(eps_prev) * 100
    return growth >= threshold


def check_foreign_consecutive(data: dict, min_days: int = 3) -> bool:
    """외국인 연속 순매수 min_days일 이상"""
    return data.get("consecutive_buy", 0) >= min_days


def check_institutional_turn(data: dict) -> bool:
    """기관 순매수 전환 (전주 대비)"""
    return data.get("turned_positive", False)


# ── 전체 스크리닝 적용 ────────────────────────────────────────────────────────

class ScreeningConfig:
    """스크리닝 파라미터 설정."""
    # 밸류에이션
    per_min: float = 5.0
    per_max: float = 40.0
    pbr_min: float = 0.3
    pbr_max: float = 5.0
    # 기술적
    vol_surge_ratio: float = 1.5
    high_52w_threshold: float = -20.0
    # 수급
    foreign_consec_days: int = 3
    # 성장
    eps_growth_min: float = 10.0
    # 유니버스
    min_marcap_bil: int = 500
    # 스크리닝 모드: 'strict'(모든 조건), 'moderate'(필수만), 'loose'(기술적만)
    mode: str = "moderate"


def apply_filters(
    ticker: str,
    universe_row: pd.Series,
    indicators: dict,
    valuation: dict,
    foreign: dict,
    institutional: dict,
    cfg: ScreeningConfig | None = None,
) -> dict[str, bool]:
    """
    종목에 대해 모든 필터를 적용하고 조건별 통과 여부를 반환.
    """
    if cfg is None:
        cfg = ScreeningConfig()

    checks = {
        # 기술적 (높은 신뢰도)
        "golden_cross":    check_golden_cross(indicators),
        "near_52w_high":   check_near_52w_high(indicators, cfg.high_52w_threshold),
        "volume_surge":    check_volume_surge(indicators, cfg.vol_surge_ratio),
        # 밸류에이션
        "per_ok":          check_per(valuation.get("per", 0), cfg.per_min, cfg.per_max),
        "pbr_ok":          check_pbr(valuation.get("pbr", 0), cfg.pbr_min, cfg.pbr_max),
        # 수급
        "foreign_consec":  check_foreign_consecutive(foreign, cfg.foreign_consec_days),
        "inst_turn":       check_institutional_turn(institutional),
        # 성장
        "eps_growth":      False,  # EPS YoY는 별도 계산 필요
    }

    # 필수 조건 (모드별)
    if cfg.mode == "strict":
        must_pass = ["golden_cross", "near_52w_high", "volume_surge",
                     "per_ok", "pbr_ok", "foreign_consec"]
    elif cfg.mode == "moderate":
        must_pass = ["golden_cross", "near_52w_high",
                     "per_ok", "pbr_ok"]
    else:  # loose
        must_pass = ["golden_cross", "near_52w_high"]

    checks["__passed__"] = all(checks[k] for k in must_pass)
    return checks
