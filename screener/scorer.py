"""
종목 종합 점수 산출 (0~100점)
각 지표를 정규화하여 가중치 적용.
"""
from __future__ import annotations

from typing import Any

# ── 가중치 정의 ───────────────────────────────────────────────────────────────

WEIGHTS = {
    # 수급 (45점)
    "foreign_consec":    20,   # 외국인 연속 순매수 (최대 20점)
    "inst_turn":         10,   # 기관 순매수 전환 (0 or 10)
    "volume_surge":      15,   # 거래량 서지 강도

    # 기술적 (30점)
    "golden_cross":      10,   # 골든크로스 (0 or 10)
    "near_52w_high":     10,   # 52주 신고가 근접
    "momentum_20d":      10,   # 20일 모멘텀

    # 밸류에이션 (15점)
    "per_score":          8,   # PER 적정성
    "pbr_score":          7,   # PBR 적정성

    # 성장 (10점)
    "eps_growth":        10,   # EPS 성장률
}

assert sum(WEIGHTS.values()) == 100, f"가중치 합 != 100: {sum(WEIGHTS.values())}"


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def calculate_score(
    indicators: dict[str, Any],
    valuation: dict[str, Any],
    foreign: dict[str, Any],
    institutional: dict[str, Any],
) -> tuple[float, dict[str, float]]:
    """
    종합 점수 계산.
    반환: (total_score, breakdown_dict)
    """
    breakdown: dict[str, float] = {}

    # ── 수급 ─────────────────────────────────────────────────────────────────

    # 외국인 연속 순매수 (3일 = 50%, 5일 = 100%, 10일+ = 150% → 캡)
    consec = foreign.get("consecutive_buy", 0)
    breakdown["foreign_consec"] = _clamp(consec / 5.0) * WEIGHTS["foreign_consec"]

    # 기관 전환
    breakdown["inst_turn"] = WEIGHTS["inst_turn"] if institutional.get("turned_positive") else 0

    # 거래량 서지 (1.5배 = 50%, 2배 = 75%, 3배+ = 100%)
    vol_ratio = indicators.get("vol_ratio", 0)
    breakdown["volume_surge"] = _clamp((vol_ratio - 1.0) / 2.0) * WEIGHTS["volume_surge"]

    # ── 기술적 ────────────────────────────────────────────────────────────────

    # 골든크로스
    breakdown["golden_cross"] = WEIGHTS["golden_cross"] if indicators.get("golden_cross") else 0

    # 52주 신고가 근접 (0% = 100점, -10% = 50%, -20% = 0%)
    pct = indicators.get("pct_from_52w_high", -100)
    breakdown["near_52w_high"] = _clamp(1 + pct / 20.0) * WEIGHTS["near_52w_high"]

    # 20일 모멘텀 (0% = 0%, +10% = 50%, +20%+ = 100%)
    mom = indicators.get("momentum_20d", 0)
    breakdown["momentum_20d"] = _clamp(mom / 20.0) * WEIGHTS["momentum_20d"]

    # ── 밸류에이션 ────────────────────────────────────────────────────────────

    # PER 점수 (10이 최적, 5~40 구간)
    per = valuation.get("per", 0)
    if 5 <= per <= 40:
        # PER 10 근처에서 최고점
        per_score = 1.0 - abs(per - 10) / 30.0
    else:
        per_score = 0.0
    breakdown["per_score"] = _clamp(per_score) * WEIGHTS["per_score"]

    # PBR 점수 (1.0 근처 최적, 0.3~5 구간)
    pbr = valuation.get("pbr", 0)
    if 0.3 <= pbr <= 5:
        pbr_score = 1.0 - abs(pbr - 1.5) / 3.5
    else:
        pbr_score = 0.0
    breakdown["pbr_score"] = _clamp(pbr_score) * WEIGHTS["pbr_score"]

    # ── 성장 ──────────────────────────────────────────────────────────────────

    # EPS 성장률 (10% = 50%, 30%+ = 100%)
    eps_growth = valuation.get("eps_growth_pct", 0)
    breakdown["eps_growth"] = _clamp(eps_growth / 30.0) * WEIGHTS["eps_growth"]

    total = sum(breakdown.values())
    return round(total, 1), breakdown
