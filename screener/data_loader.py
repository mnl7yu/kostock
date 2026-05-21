"""
데이터 로더 — FDR + Naver Finance 기반
3단계 접근: 유니버스 → 히스토리 → 상세(후보 종목만)
외부 API 차단 시 직전 스크리닝 CSV로 자동 대체.
"""
from __future__ import annotations

import glob
import pickle
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import requests
from bs4 import BeautifulSoup

try:
    import FinanceDataReader as fdr
    FDR_OK = True
except ImportError:
    FDR_OK = False

CACHE_DIR = Path(__file__).parent.parent / ".cache" / "prices"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
REPORTS_DIR = Path(__file__).parent.parent / "reports"

# ── CSV 폴백 헬퍼 ─────────────────────────────────────────────────────────────

_CSV_UNIVERSE: pd.DataFrame | None = None


def _load_csv_universe() -> pd.DataFrame | None:
    global _CSV_UNIVERSE
    if _CSV_UNIVERSE is not None:
        return _CSV_UNIVERSE
    csvs = sorted(glob.glob(str(REPORTS_DIR / "*_screening.csv")))
    if not csvs:
        return None
    df = pd.read_csv(csvs[-1], index_col=0, encoding="utf-8-sig")
    df.index.name = "ticker"
    _CSV_UNIVERSE = df
    print(f"[data_loader] 캐시 CSV 폴백: {Path(csvs[-1]).name}  {len(df)}종목")
    return df


def _get_cached_row(ticker: str) -> "pd.Series | None":
    df = _load_csv_universe()
    if df is None or ticker not in df.index:
        return None
    return df.loc[ticker]


def _build_synthetic_history(row: "pd.Series", days: int = 65) -> pd.DataFrame:
    """CSV 행에서 synthetic OHLCV 생성 (compute_indicators 통과용)."""
    close = float(row.get("close", 10000))
    ma5   = float(row.get("ma5",  close * 1.02))
    ma20  = float(row.get("ma20", close * 0.98))
    vol_ratio      = float(row.get("vol_ratio", 1.0))
    pct_from_52w   = float(row.get("pct_from_52w_high", -10))

    dates = pd.date_range(end=pd.Timestamp.today(), periods=days, freq="B")

    p0_20 = max(2 * ma20 - close, close * 0.5)
    p0_5  = max(2 * ma5  - close, close * 0.5)

    earlier = np.linspace(p0_20 * 0.95, p0_20, days - 20)
    last_20 = np.linspace(p0_20, close, 20)
    prices  = np.concatenate([earlier, last_20])
    prices[-5:] = np.linspace(p0_5, close, 5)
    prices  = np.clip(prices, 1.0, None)

    base_vol = 100_000.0
    vols = np.full(days, base_vol)
    vols[-1] = base_vol * vol_ratio

    high_52w = close / (1 + pct_from_52w / 100) if pct_from_52w < 0 else close * 1.05

    df_hist = pd.DataFrame(
        {
            "Open":   prices * 0.998,
            "High":   prices * 1.005,
            "Low":    prices * 0.995,
            "Close":  prices,
            "Volume": vols,
        },
        index=dates,
    )
    df_hist.iloc[0, df_hist.columns.get_loc("High")] = high_52w
    return df_hist

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://finance.naver.com/",
}

# ── 1단계: 전체 유니버스 ─────────────────────────────────────────────────────

def get_universe(min_marcap_bil: int = 500) -> pd.DataFrame:
    """
    KOSPI + KOSDAQ 전 종목 현재 시세.
    min_marcap_bil: 최소 시가총액 (억원). 기본 500억 이상만 포함.
    반환: Code, Name, Market, Close, Volume, Amount, Marcap, Shares
    외부 API 차단 시 직전 스크리닝 CSV로 대체.
    """
    if FDR_OK:
        try:
            df_k = fdr.StockListing("KOSPI")
            df_d = fdr.StockListing("KOSDAQ")
            df = pd.concat([df_k, df_d], ignore_index=True)

            df = df.rename(columns={
                "Code": "ticker",
                "Name": "name",
                "Market": "market",
                "Close": "close",
                "Volume": "volume",
                "Amount": "amount",
                "Marcap": "marcap",
                "Stocks": "shares",
                "Changes": "change",
                "ChagesRatio": "change_pct",
            })

            df["marcap"] = pd.to_numeric(df["marcap"], errors="coerce").fillna(0)
            df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0)
            df["close"]  = pd.to_numeric(df["close"],  errors="coerce").fillna(0)

            df = df[df["ticker"].str.match(r"^\d{6}$")]
            df = df[~df["ticker"].str.endswith(("5", "7", "9"))]
            df = df[df["marcap"] >= min_marcap_bil * 1e8]
            df = df[df["volume"] > 10_000]
            df = df[df["close"] > 500]

            df = df.set_index("ticker")
            df.index.name = "ticker"
            return df[["name", "market", "close", "change_pct", "volume", "amount", "marcap", "shares"]]
        except Exception as e:
            print(f"[data_loader] FDR StockListing 실패: {e} — CSV 폴백 사용")

    # ── CSV 폴백 ──────────────────────────────────────────────────────────────
    cached = _load_csv_universe()
    if cached is None:
        raise RuntimeError("FDR 불가 + 캐시 CSV 없음: 데이터 로드 불가")

    df = cached.copy()
    df["marcap"] = pd.to_numeric(df["marcap"], errors="coerce").fillna(0)
    df = df[df["marcap"] >= min_marcap_bil * 1e8]

    for col in ("name", "market", "close", "change_pct", "marcap"):
        if col not in df.columns:
            df[col] = 0

    # 없는 열은 0으로 채워서 반환 (기술적 지표는 여기에 포함됨)
    keep = [c for c in ["name", "market", "close", "change_pct", "marcap"] if c in df.columns]
    extra = [c for c in df.columns if c not in keep]
    return df[keep + extra]


# ── 2단계: OHLCV 히스토리 ────────────────────────────────────────────────────

def get_price_history(ticker: str, days: int = 120, use_cache: bool = True) -> pd.DataFrame:
    """
    종목 OHLCV 히스토리. 캐시 있으면 재사용 (당일 한정).
    반환: DataFrame[Open, High, Low, Close, Volume, Change] index=Date
    외부 API 차단 시 CSV 기반 synthetic 히스토리로 대체.
    """
    cache_file = CACHE_DIR / f"{datetime.today().strftime('%Y%m%d')}_{ticker}.pkl"

    if use_cache and cache_file.exists():
        try:
            return pickle.load(open(cache_file, "rb"))
        except Exception:
            pass

    if FDR_OK:
        end = datetime.today()
        start = end - timedelta(days=days * 1.6)
        try:
            df = fdr.DataReader(ticker, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
            df = df.tail(days)
            if use_cache and not df.empty:
                pickle.dump(df, open(cache_file, "wb"))
            return df
        except Exception:
            pass  # fall through to CSV fallback

    # ── CSV 폴백: synthetic OHLCV ─────────────────────────────────────────────
    row = _get_cached_row(ticker)
    if row is not None:
        return _build_synthetic_history(row, days=max(days, 65))
    return pd.DataFrame()


def get_price_history_range(
    ticker: str, start: str, end: str
) -> pd.DataFrame:
    """백테스팅용 - 특정 기간 히스토리."""
    cache_key = f"bt_{ticker}_{start[:4]}"
    cache_file = CACHE_DIR / f"{cache_key}.pkl"
    if cache_file.exists():
        try:
            return pickle.load(open(cache_file, "rb"))
        except Exception:
            pass
    try:
        df = fdr.DataReader(ticker, start, end)
        if not df.empty:
            pickle.dump(df, open(cache_file, "wb"))
        return df
    except Exception:
        return pd.DataFrame()


# ── 3단계: 상세 재무 (후보 종목 한정) ────────────────────────────────────────

def _naver_get(url: str) -> requests.Response:
    time.sleep(0.15)  # 네이버 과부하 방지
    return requests.get(url, headers=HEADERS, timeout=12)


def get_per_pbr_eps(ticker: str) -> dict[str, float]:
    """
    네이버 모바일 금융 JSON API에서 PER, PBR, EPS, BPS 수집.
    endpoint: m.stock.naver.com/api/stock/{ticker}/finance/annual
    외부 API 차단 시 CSV 캐시 값 반환.
    """
    result = {"per": 0.0, "pbr": 0.0, "eps": 0.0, "bps": 0.0}
    try:
        r = _naver_get(
            f"https://m.stock.naver.com/api/stock/{ticker}/finance/annual"
        )
        data = r.json()
        info = data.get("financeInfo", {})
        titles = info.get("trTitleList", [])
        rows   = info.get("rowList", [])

        confirmed = [t["key"] for t in titles if t.get("isConsensus") == "N"]
        if not confirmed:
            raise ValueError("no confirmed period")
        latest_key = confirmed[-1]

        def _val(row_title: str) -> float:
            for row in rows:
                if row.get("title") == row_title:
                    v = row.get("columns", {}).get(latest_key, {}).get("value", "")
                    try:
                        return float(str(v).replace(",", "").strip())
                    except Exception:
                        return 0.0
            return 0.0

        result["per"] = _val("PER")
        result["pbr"] = _val("PBR")
        result["eps"] = _val("EPS")
        result["bps"] = _val("BPS")
        return result

    except Exception:
        pass

    # ── CSV 폴백 ──────────────────────────────────────────────────────────────
    row = _get_cached_row(ticker)
    if row is not None:
        result["per"] = float(row.get("per", 0) or 0)
        result["pbr"] = float(row.get("pbr", 0) or 0)
        result["eps"] = float(row.get("eps", 0) or 0)
    return result


def get_foreign_buying(ticker: str, days: int = 5) -> dict[str, Any]:
    """
    외국인 순매수 데이터 (최근 N일).
    외부 API 차단 시 CSV 캐시 값 반환.
    """
    result = {"daily_net": [], "consecutive_buy": 0, "ownership_ratio": 0.0}
    try:
        r = _naver_get(f"https://finance.naver.com/item/frgn.naver?code={ticker}")
        if "not in allowlist" in r.text.lower():
            raise ConnectionError("blocked")
        soup = BeautifulSoup(r.text, "html.parser")
        rows = soup.select("table.type2 tr")

        daily_net = []
        for row in rows:
            cells = row.select("td")
            if len(cells) < 8:
                continue
            date_txt = cells[0].get_text(strip=True)
            if not re.match(r"\d{4}\.\d{2}\.\d{2}", date_txt):
                continue
            net_txt = cells[4].get_text(strip=True).replace(",", "").replace("+", "")
            try:
                daily_net.append(int(net_txt))
            except Exception:
                daily_net.append(0)
            if len(daily_net) >= days:
                break

        result["daily_net"] = daily_net
        consec = 0
        for v in daily_net:
            if v > 0:
                consec += 1
            else:
                break
        result["consecutive_buy"] = consec

        ratio_m = re.search(r"외국인\s*지분\s*율[^\d]*([\d.]+)%", soup.get_text())
        if ratio_m:
            result["ownership_ratio"] = float(ratio_m.group(1))
        return result

    except Exception:
        pass

    # ── CSV 폴백 ──────────────────────────────────────────────────────────────
    row = _get_cached_row(ticker)
    if row is not None:
        consec = int(row.get("foreign_consec", 0) or 0)
        result["consecutive_buy"] = consec
        result["daily_net"] = [1] * consec
    return result


def get_institutional_trend(ticker: str) -> dict[str, Any]:
    """
    기관 순매수 동향 (이번 주 vs 전주 비교).
    외부 API 차단 시 CSV 캐시 값 반환.
    """
    result = {"this_week": 0, "last_week": 0, "turned_positive": False}
    try:
        r = _naver_get(f"https://finance.naver.com/item/sise_investor.naver?code={ticker}")
        if "not in allowlist" in r.text.lower():
            raise ConnectionError("blocked")
        soup = BeautifulSoup(r.text, "html.parser")
        rows = soup.select("table.type2 tr")

        inst_vals = []
        for row in rows:
            cells = row.select("td")
            if len(cells) < 6:
                continue
            date_txt = cells[0].get_text(strip=True)
            if not re.match(r"\d{4}\.\d{2}\.\d{2}", date_txt):
                continue
            try:
                val = int(cells[2].get_text(strip=True).replace(",", "").replace("+", "") or "0")
                inst_vals.append(val)
            except Exception:
                inst_vals.append(0)
            if len(inst_vals) >= 10:
                break

        if inst_vals:
            result["this_week"] = sum(inst_vals[:5])
            result["last_week"] = sum(inst_vals[5:10])
            result["turned_positive"] = (
                result["this_week"] > 0 and result["last_week"] <= 0
            )
        return result

    except Exception:
        pass

    # ── CSV 폴백 ──────────────────────────────────────────────────────────────
    row = _get_cached_row(ticker)
    if row is not None:
        turned = bool(row.get("inst_turn", False))
        result["turned_positive"] = turned
        if turned:
            result["this_week"] = 1
    return result


def get_stock_news(ticker: str, limit: int = 3) -> list[str]:
    """최근 뉴스 헤드라인."""
    headlines = []
    try:
        r = _naver_get(f"https://finance.naver.com/item/news_news.naver?code={ticker}&page=1")
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.select("td.title a"):
            title = a.get_text(strip=True)
            if title:
                headlines.append(title)
            if len(headlines) >= limit:
                break
    except Exception:
        pass
    return headlines
