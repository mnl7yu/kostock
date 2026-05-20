"""
메인 스크리닝 엔진 — 전체 파이프라인 실행
1단계: 유니버스 필터 (시가총액, 거래량)
2단계: 기술적 필터 (OHLCV 히스토리 기반)
3단계: 상세 필터 (PER/PBR, 외국인/기관 수급)
4단계: 점수 산출 및 정렬
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any

import pandas as pd

from .data_loader import (
    get_universe,
    get_price_history,
    get_per_pbr_eps,
    get_foreign_buying,
    get_institutional_trend,
    get_stock_news,
)
from .filters import (
    ScreeningConfig,
    apply_filters,
    compute_indicators,
)
from .scorer import calculate_score


def _process_stage2(ticker: str, row: pd.Series) -> dict[str, Any] | None:
    """Stage 2: 기술적 지표 계산 (히스토리 필요)."""
    df_hist = get_price_history(ticker, days=260)  # 52주 + 여유
    if df_hist is None or len(df_hist) < 21:
        return None
    ind = compute_indicators(df_hist)
    if not ind:
        return None
    ind["ticker"] = ticker
    ind["name"] = row.get("name", ticker)
    ind["market"] = row.get("market", "")
    ind["marcap"] = float(row.get("marcap", 0))
    ind["change_pct"] = float(row.get("change_pct", 0))
    return ind


def _process_stage3(ticker: str, ind: dict) -> dict[str, Any]:
    """Stage 3: 재무/수급 데이터 수집."""
    val = get_per_pbr_eps(ticker)
    foreign = get_foreign_buying(ticker, days=5)
    inst = get_institutional_trend(ticker)
    return {**ind, **val, "foreign_consec": foreign.get("consecutive_buy", 0),
            "foreign_net": foreign.get("daily_net", []),
            "inst_turn": inst.get("turned_positive", False)}


def run_screening(
    mode: str = "moderate",
    min_marcap_bil: int = 500,
    max_candidates: int = 150,
    fetch_news: bool = True,
    verbose: bool = True,
) -> pd.DataFrame:
    """
    전체 스크리닝 파이프라인 실행.
    반환: 스크리닝 통과 종목 DataFrame (score 내림차순)
    """
    cfg = ScreeningConfig()
    cfg.mode = mode
    cfg.min_marcap_bil = min_marcap_bil

    t0 = time.time()

    # ── Stage 1: 유니버스 ─────────────────────────────────────────────────────
    if verbose:
        print("[screener] Stage 1: 유니버스 로드 중...")
    universe = get_universe(min_marcap_bil=min_marcap_bil)
    if verbose:
        print(f"[screener] 유니버스: {len(universe)}개 종목")

    # ── Stage 2: 기술적 지표 (병렬) ──────────────────────────────────────────
    if verbose:
        print(f"[screener] Stage 2: 기술적 지표 계산 중 (최대 {max_candidates}개)...")

    # 시가총액 상위 종목 우선 처리
    universe_sorted = universe.sort_values("marcap", ascending=False)
    candidates = universe_sorted.head(max_candidates)

    stage2_results: list[dict] = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {
            executor.submit(_process_stage2, ticker, row): ticker
            for ticker, row in candidates.iterrows()
        }
        done = 0
        for future in as_completed(futures):
            done += 1
            result = future.result()
            if result:
                stage2_results.append(result)
            if verbose and done % 30 == 0:
                print(f"[screener]   {done}/{len(candidates)} 처리 중...")

    if verbose:
        print(f"[screener] Stage 2 완료: {len(stage2_results)}개 히스토리 확보")

    # 기술적 사전 필터 적용 (빠른 축소)
    tech_passed = []
    for ind in stage2_results:
        # 골든크로스 OR (52주고가근접 AND 거래량서지) 중 하나라도
        if ind.get("golden_cross") and ind.get("near_52w_high"):
            tech_passed.append(ind)

    if verbose:
        print(f"[screener] 기술적 필터 통과: {len(tech_passed)}개")

    if not tech_passed:
        print("[screener] 기술적 필터 통과 종목 없음")
        return pd.DataFrame()

    # ── Stage 3: 재무/수급 (후보 한정, 병렬) ─────────────────────────────────
    if verbose:
        print(f"[screener] Stage 3: 재무/수급 수집 중 ({len(tech_passed)}개)...")

    stage3_results: list[dict] = []
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {
            executor.submit(_process_stage3, ind["ticker"], ind): ind["ticker"]
            for ind in tech_passed
        }
        for future in as_completed(futures):
            result = future.result()
            stage3_results.append(result)

    # ── Stage 4: 전체 필터 + 점수 산출 ───────────────────────────────────────
    if verbose:
        print("[screener] Stage 4: 필터 적용 및 점수 산출 중...")

    rows = []
    for d in stage3_results:
        ticker = d["ticker"]

        val = {"per": d.get("per", 0), "pbr": d.get("pbr", 0),
               "eps": d.get("eps", 0), "eps_growth_pct": 0}
        foreign = {"consecutive_buy": d.get("foreign_consec", 0)}
        inst = {"turned_positive": d.get("inst_turn", False)}

        checks = apply_filters(
            ticker, pd.Series(d), d, val, foreign, inst, cfg
        )

        if not checks.get("__passed__"):
            continue

        score, breakdown = calculate_score(d, val, foreign, inst)

        row = {
            "name":              d.get("name", ticker),
            "market":            d.get("market", ""),
            "close":             d.get("close", 0),
            "change_pct":        d.get("change_pct", 0),
            "marcap":            d.get("marcap", 0),
            "per":               val["per"],
            "pbr":               val["pbr"],
            "eps":               val.get("eps", 0),
            "ma5":               d.get("ma5", 0),
            "ma20":              d.get("ma20", 0),
            "golden_cross":      checks["golden_cross"],
            "near_52w_high":     checks["near_52w_high"],
            "volume_surge":      checks["volume_surge"],
            "per_ok":            checks["per_ok"],
            "pbr_ok":            checks["pbr_ok"],
            "foreign_consec":    d.get("foreign_consec", 0),
            "inst_turn":         d.get("inst_turn", False),
            "vol_ratio":         d.get("vol_ratio", 0),
            "pct_from_52w_high": d.get("pct_from_52w_high", 0),
            "momentum_20d":      d.get("momentum_20d", 0),
            "rsi":               d.get("rsi", 50),
            "score":             score,
            **{f"score_{k}": v for k, v in breakdown.items()},
        }
        rows.append((ticker, row))

    if not rows:
        print("[screener] 최종 통과 종목 없음")
        return pd.DataFrame()

    df = pd.DataFrame(
        [r for _, r in rows],
        index=[t for t, _ in rows]
    )
    df.index.name = "ticker"
    df = df.sort_values("score", ascending=False)

    # ── Stage 5: 뉴스 (상위 20개만) ─────────────────────────────────────────
    news_map: dict[str, list[str]] = {}
    if fetch_news:
        if verbose:
            print("[screener] Stage 5: 뉴스 수집 중...")
        for ticker in df.head(20).index:
            news_map[ticker] = get_stock_news(ticker, 3)

    elapsed = time.time() - t0
    if verbose:
        print(f"[screener] 완료: {len(df)}개 통과 종목  ({elapsed:.0f}초 소요)")

    df.attrs["news_map"] = news_map
    return df
