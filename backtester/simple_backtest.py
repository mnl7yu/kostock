"""
단순 백테스팅 — 기술적 스크리닝 기반
- 기간: 2022-01-01 ~ 2024-12-31
- 매주 월요일 스크리닝 → 조건 통과 종목 종가 매수
- 20거래일 후 종가 무조건 매도
- 최대 10종목, 균등 분배 (10%)
- 수수료: 0.015% 편도
"""
from __future__ import annotations

import pickle
import warnings
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

try:
    import FinanceDataReader as fdr
    FDR_OK = True
except ImportError:
    FDR_OK = False

CACHE_DIR = Path(__file__).parent.parent / ".cache" / "bt"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

REPORTS_DIR = Path(__file__).parent.parent / "reports"
REPORTS_DIR.mkdir(exist_ok=True)

COMMISSION = 0.00015   # 편도 0.015%
MAX_POSITIONS = 10
HOLD_DAYS = 20         # 보유 기간 (거래일)

# ── 유니버스 (백테스팅용 상위 종목) ─────────────────────────────────────────

def _get_bt_universe(top_n: int = 200) -> list[str]:
    """현재 기준 KOSPI 상위 N개 종목 (시가총액 순)."""
    cache = CACHE_DIR / "universe.pkl"
    if cache.exists():
        try:
            return pickle.load(open(cache, "rb"))
        except Exception:
            pass
    if not FDR_OK:
        return []
    df = fdr.StockListing("KOSPI")
    df["Marcap"] = pd.to_numeric(df["Marcap"], errors="coerce").fillna(0)
    df = df[df["Code"].str.match(r"^\d{6}$")]
    df = df[~df["Code"].str.endswith(("5", "7", "9"))]
    df = df.sort_values("Marcap", ascending=False).head(top_n)
    tickers = df["Code"].tolist()
    pickle.dump(tickers, open(cache, "wb"))
    return tickers


def _load_history(ticker: str, start: str, end: str) -> pd.DataFrame:
    """캐시 우선 OHLCV 로드."""
    cache_file = CACHE_DIR / f"{ticker}.pkl"
    if cache_file.exists():
        try:
            df = pickle.load(open(cache_file, "rb"))
            return df.loc[start:end]
        except Exception:
            pass
    if not FDR_OK:
        return pd.DataFrame()
    try:
        df = fdr.DataReader(ticker, "2021-06-01", "2025-01-31")
        if not df.empty:
            pickle.dump(df, open(cache_file, "wb"))
        return df.loc[start:end]
    except Exception:
        return pd.DataFrame()


# ── 기술적 스크리닝 (백테스팅 버전) ─────────────────────────────────────────

def _screen_on_date(
    ticker: str, all_data: pd.DataFrame, screen_date: pd.Timestamp
) -> bool:
    """
    특정 날짜 기준 기술적 스크리닝 조건 체크.
    - 5MA > 20MA (골든크로스)
    - 52주 신고가 대비 -20% 이내
    - 거래량 20일 평균 대비 150% 이상
    """
    # 해당 날짜까지의 데이터만 사용 (미래 정보 사용 방지)
    df = all_data.loc[:screen_date]
    if len(df) < 21:
        return False

    close = df["Close"].astype(float)
    volume = df["Volume"].astype(float)

    ma5 = close.rolling(5).mean().iloc[-1]
    ma20 = close.rolling(20).mean().iloc[-1]

    if ma5 <= ma20:
        return False

    high_52w = df["High"].astype(float).rolling(min(252, len(df))).max().iloc[-1]
    if close.iloc[-1] < high_52w * 0.80:
        return False

    vol_20avg = volume.rolling(20).mean().iloc[-1]
    if volume.iloc[-1] < vol_20avg * 1.5:
        return False

    return True


def _get_price_on_date(df: pd.DataFrame, target_date: pd.Timestamp, forward: bool = False) -> float | None:
    """특정 날짜 종가. 없으면 다음/이전 거래일 사용."""
    if df.empty:
        return None
    idx = df.index
    if target_date in idx:
        return float(df.loc[target_date, "Close"])
    # 가장 가까운 날짜
    if forward:
        future = idx[idx >= target_date]
        if len(future) == 0:
            return None
        return float(df.loc[future[0], "Close"])
    else:
        past = idx[idx <= target_date]
        if len(past) == 0:
            return None
        return float(df.loc[past[-1], "Close"])


def _nth_trading_day(df: pd.DataFrame, start_date: pd.Timestamp, n: int) -> pd.Timestamp | None:
    """start_date 이후 n번째 거래일 반환."""
    future_dates = df.index[df.index > start_date]
    if len(future_dates) < n:
        return None
    return future_dates[n - 1]


# ── 메인 백테스팅 ─────────────────────────────────────────────────────────────

def run_backtest(
    start: str = "2022-01-01",
    end: str = "2024-12-31",
    universe_size: int = 150,
    verbose: bool = True,
) -> dict[str, Any]:
    """
    백테스팅 실행.
    반환: 결과 딕셔너리 (equity_curve, stats, trade_log)
    """
    if not FDR_OK:
        raise RuntimeError("FinanceDataReader 필요: pip install finance-datareader")

    if verbose:
        print(f"\n{'='*60}")
        print(f"백테스팅 시작: {start} ~ {end}")
        print(f"유니버스: KOSPI 상위 {universe_size}개 | 보유기간: {HOLD_DAYS}일")
        print(f"{'='*60}\n")

    # ── 유니버스 및 데이터 로드 ────────────────────────────────────────────────
    universe = _get_bt_universe(universe_size)
    if verbose:
        print(f"[bt] 종목 데이터 다운로드 중... ({len(universe)}개, 캐시 활용)")

    price_data: dict[str, pd.DataFrame] = {}
    for i, ticker in enumerate(universe):
        df = _load_history(ticker, start, end)
        if not df.empty and len(df) > 30:
            price_data[ticker] = df
        if verbose and (i + 1) % 50 == 0:
            print(f"[bt]   {i+1}/{len(universe)} 로드 완료")

    if verbose:
        print(f"[bt] 유효 종목: {len(price_data)}개\n")

    # ── KOSPI 벤치마크 ─────────────────────────────────────────────────────────
    try:
        kospi = fdr.DataReader("KS11", start, end)["Close"].astype(float)
    except Exception:
        kospi = None

    # ── 월요일 목록 생성 ──────────────────────────────────────────────────────
    start_dt = pd.Timestamp(start)
    end_dt = pd.Timestamp(end)
    all_mondays = pd.date_range(start_dt, end_dt, freq="W-MON")

    # ── 포트폴리오 시뮬레이션 ─────────────────────────────────────────────────
    cash = 100_000_000        # 초기 자금 1억원
    positions: dict[str, dict] = {}   # ticker → {buy_price, buy_date, sell_date, shares, weight}
    trade_log: list[dict] = []
    equity_curve: list[dict] = []
    ticker_count: dict[str, int] = {}

    # 거래일 기준 포트폴리오 가치 계산용
    all_dates = sorted(set(
        d for df in price_data.values() for d in df.index
    ))
    all_dates = [d for d in all_dates if start_dt <= d <= end_dt]

    date_set = set(all_dates)
    monday_set = set(all_mondays)

    if verbose:
        print(f"[bt] 시뮬레이션 시작... (거래일 {len(all_dates)}개, 월요일 {len(all_mondays)}개)")

    for date in all_dates:
        # ── 매도 처리 ──────────────────────────────────────────────────────────
        to_sell = []
        for ticker, pos in positions.items():
            sell_date = pos["sell_date"]
            if date >= sell_date:
                # 매도 실행
                sell_df = price_data.get(ticker)
                sell_price = _get_price_on_date(sell_df, sell_date, forward=True) if sell_df is not None else None
                if sell_price:
                    sell_value = pos["shares"] * sell_price * (1 - COMMISSION)
                    pnl_pct = (sell_price - pos["buy_price"]) / pos["buy_price"] * 100
                    cash += sell_value
                    trade_log.append({
                        "ticker": ticker,
                        "buy_date": pos["buy_date"].strftime("%Y-%m-%d"),
                        "sell_date": date.strftime("%Y-%m-%d"),
                        "buy_price": pos["buy_price"],
                        "sell_price": sell_price,
                        "pnl_pct": round(pnl_pct, 2),
                        "hold_days": (date - pos["buy_date"]).days,
                        "profit": round(sell_value - pos["cost"], 0),
                    })
                else:
                    cash += pos["cost"]  # fallback
                to_sell.append(ticker)

        for t in to_sell:
            del positions[t]

        # ── 매수 처리 (월요일에만) ─────────────────────────────────────────────
        if date in monday_set and len(positions) < MAX_POSITIONS:
            # 스크리닝 실행
            screened = []
            for ticker, df in price_data.items():
                if ticker in positions:
                    continue
                if date not in df.index:
                    continue
                if _screen_on_date(ticker, df, date):
                    buy_price = _get_price_on_date(df, date)
                    if buy_price and buy_price > 0:
                        screened.append((ticker, buy_price))

            # 가중치 기반 선택 (매수 슬롯 한도)
            available_slots = MAX_POSITIONS - len(positions)
            buy_list = screened[:available_slots]

            for ticker, buy_price in buy_list:
                # 균등 분배 (현재 포트폴리오 기준)
                portfolio_value = cash + sum(
                    _get_price_on_date(price_data[t], date, True) * p["shares"]
                    for t, p in positions.items()
                    if t in price_data
                ) if positions else cash

                alloc = portfolio_value * (1 / MAX_POSITIONS)
                alloc = min(alloc, cash * 0.95)  # 현금 한도
                if alloc < 100_000:
                    continue

                shares = int(alloc / buy_price)
                if shares <= 0:
                    continue

                cost = shares * buy_price * (1 + COMMISSION)
                if cost > cash:
                    continue

                # 매도일 = 매수 후 HOLD_DAYS 거래일
                ticker_df = price_data[ticker]
                sell_date = _nth_trading_day(ticker_df, date, HOLD_DAYS)
                if sell_date is None:
                    continue

                cash -= cost
                positions[ticker] = {
                    "buy_price": buy_price,
                    "buy_date": date,
                    "sell_date": sell_date,
                    "shares": shares,
                    "cost": cost,
                }
                ticker_count[ticker] = ticker_count.get(ticker, 0) + 1

        # ── 포트폴리오 가치 기록 ──────────────────────────────────────────────
        holdings_value = 0
        for ticker, pos in positions.items():
            df = price_data.get(ticker)
            price = _get_price_on_date(df, date) if df is not None else None
            if price:
                holdings_value += pos["shares"] * price

        total_value = cash + holdings_value
        equity_curve.append({
            "date": date,
            "value": total_value,
            "cash": cash,
            "positions": len(positions),
        })

    # ── 결과 계산 ─────────────────────────────────────────────────────────────
    if not equity_curve:
        return {"error": "거래 데이터 없음"}

    eq_df = pd.DataFrame(equity_curve).set_index("date")
    eq_df["returns"] = eq_df["value"].pct_change().fillna(0)
    eq_df["cumulative"] = (1 + eq_df["returns"]).cumprod()

    initial = 100_000_000
    final = eq_df["value"].iloc[-1]
    total_return = (final / initial - 1) * 100

    # MDD
    roll_max = eq_df["value"].cummax()
    drawdown = (eq_df["value"] - roll_max) / roll_max * 100
    mdd = drawdown.min()

    # 연도별 수익률
    yearly = {}
    for year in range(2022, 2025):
        mask = eq_df.index.year == year
        if mask.sum() > 0:
            y_data = eq_df[mask]["value"]
            y_ret = (y_data.iloc[-1] / y_data.iloc[0] - 1) * 100
            yearly[str(year)] = round(y_ret, 2)

    # 거래 통계
    if trade_log:
        tl_df = pd.DataFrame(trade_log)
        win_trades = tl_df[tl_df["pnl_pct"] > 0]
        win_rate = len(win_trades) / len(tl_df) * 100
        avg_pnl = tl_df["pnl_pct"].mean()
        avg_hold = tl_df["hold_days"].mean()
        top_tickers = (
            pd.Series(ticker_count).sort_values(ascending=False).head(10)
        )
    else:
        win_rate = avg_pnl = avg_hold = 0
        tl_df = pd.DataFrame()
        top_tickers = pd.Series()

    # KOSPI 알파
    alpha = None
    if kospi is not None and not kospi.empty:
        try:
            k_start = float(kospi.iloc[0])
            k_end = float(kospi.iloc[-1])
            kospi_ret = (k_end / k_start - 1) * 100
            alpha = total_return - kospi_ret
        except Exception:
            pass

    stats = {
        "initial":      initial,
        "final":        round(final, 0),
        "total_return": round(total_return, 2),
        "kospi_return": round(kospi_ret if alpha is not None else 0, 2),
        "alpha":        round(alpha, 2) if alpha is not None else None,
        "mdd":          round(mdd, 2),
        "win_rate":     round(win_rate, 2),
        "avg_pnl":      round(avg_pnl, 2),
        "avg_hold_days": round(avg_hold, 1),
        "total_trades": len(tl_df),
        "yearly":       yearly,
        "top_tickers":  top_tickers.to_dict(),
    }

    results = {
        "stats":        stats,
        "equity_curve": eq_df,
        "trade_log":    tl_df,
        "kospi":        kospi,
    }

    if verbose:
        _print_summary(stats)

    # ── 그래프 + 리포트 저장 ──────────────────────────────────────────────────
    _save_chart(results, start, end)
    _save_report(results, start, end)

    return results


# ── 출력 헬퍼 ─────────────────────────────────────────────────────────────────

def _print_summary(stats: dict) -> None:
    print(f"\n{'='*50}")
    print("📊 백테스팅 결과 요약")
    print(f"{'='*50}")
    print(f"  총 수익률:       {stats['total_return']:+.2f}%")
    if stats.get("alpha") is not None:
        print(f"  KOSPI 수익률:    {stats['kospi_return']:+.2f}%")
        print(f"  초과수익 (알파): {stats['alpha']:+.2f}%")
    print(f"  최대 낙폭 (MDD): {stats['mdd']:.2f}%")
    print(f"  승률:            {stats['win_rate']:.1f}%")
    print(f"  평균 수익률/건:  {stats['avg_pnl']:+.2f}%")
    print(f"  평균 보유일:     {stats['avg_hold_days']:.1f}일")
    print(f"  총 거래 수:      {stats['total_trades']}건")
    print(f"\n  연도별 수익률:")
    for yr, ret in stats.get("yearly", {}).items():
        print(f"    {yr}: {ret:+.2f}%")
    print(f"\n  가장 많이 선택된 종목 TOP 10:")
    for ticker, cnt in list(stats.get("top_tickers", {}).items())[:10]:
        print(f"    {ticker}: {cnt}회")
    print(f"{'='*50}\n")


def _save_chart(results: dict, start: str, end: str) -> None:
    """수익률 곡선 그래프 저장."""
    eq_df = results["equity_curve"]
    kospi = results.get("kospi")

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 9), gridspec_kw={"height_ratios": [3, 1]})
    fig.patch.set_facecolor("#0d1117")
    for ax in [ax1, ax2]:
        ax.set_facecolor("#161b22")
        ax.tick_params(colors="#8b949e")
        ax.spines[["top", "right", "left", "bottom"]].set_color("#30363d")

    # 수익률 곡선
    ax1.set_title(
        f"성장주 스크리닝 백테스팅 ({start[:7]} ~ {end[:7]})",
        color="#e6edf3", fontsize=14, pad=12
    )

    norm_eq = eq_df["value"] / eq_df["value"].iloc[0] * 100
    ax1.plot(norm_eq.index, norm_eq.values, color="#3fb950", linewidth=2, label="스크리닝 전략")

    if kospi is not None and not kospi.empty:
        kospi_aligned = kospi.reindex(eq_df.index, method="ffill").dropna()
        if not kospi_aligned.empty:
            norm_kospi = kospi_aligned / kospi_aligned.iloc[0] * 100
            ax1.plot(norm_kospi.index, norm_kospi.values, color="#58a6ff",
                     linewidth=1.5, linestyle="--", label="KOSPI", alpha=0.8)

    ax1.axhline(100, color="#30363d", linewidth=0.8, linestyle=":")
    ax1.set_ylabel("누적 수익률 (기준=100)", color="#8b949e")
    ax1.legend(facecolor="#21262d", labelcolor="#e6edf3", framealpha=0.8)
    ax1.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x:.0f}"))

    # 포지션 수
    ax2.fill_between(
        eq_df.index, eq_df["positions"],
        color="#e3b341", alpha=0.6, label="보유 종목 수"
    )
    ax2.set_ylabel("보유 종목", color="#8b949e")
    ax2.set_ylim(0, MAX_POSITIONS + 1)
    ax2.legend(facecolor="#21262d", labelcolor="#e6edf3", framealpha=0.8)

    plt.tight_layout()
    path = REPORTS_DIR / f"backtest_{start[:4]}_{end[:4]}.png"
    plt.savefig(path, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close()
    print(f"[bt] 그래프 저장: {path}")


def _save_report(results: dict, start: str, end: str) -> None:
    """마크다운 백테스팅 리포트 저장."""
    stats = results["stats"]
    tl_df = results["trade_log"]

    def arrow(v): return "▲" if v >= 0 else "▼"

    lines = [
        f"# 📈 백테스팅 리포트 ({start} ~ {end})\n",
        f"## 📊 성과 요약\n",
        f"| 지표 | 값 |",
        f"|------|----|",
        f"| 총 수익률 | **{stats['total_return']:+.2f}%** |",
    ]
    if stats.get("alpha") is not None:
        lines += [
            f"| KOSPI 수익률 | {stats['kospi_return']:+.2f}% |",
            f"| 초과수익 (알파) | **{stats['alpha']:+.2f}%** |",
        ]
    lines += [
        f"| 최대 낙폭 (MDD) | {stats['mdd']:.2f}% |",
        f"| 승률 | {stats['win_rate']:.1f}% |",
        f"| 평균 수익률/건 | {stats['avg_pnl']:+.2f}% |",
        f"| 평균 보유일 | {stats['avg_hold_days']:.1f}일 |",
        f"| 총 거래 수 | {stats['total_trades']}건 |",
        f"| 최종 자산 | {stats['final']:,.0f}원 |",
        "",
        "## 📅 연도별 수익률\n",
        "| 연도 | 수익률 |",
        "|------|--------|",
    ]
    for yr, ret in stats.get("yearly", {}).items():
        lines.append(f"| {yr} | {arrow(ret)}{abs(ret):.2f}% |")

    lines += [
        "",
        "## 🏆 가장 많이 선택된 종목 TOP 10\n",
        "| 종목코드 | 선택 횟수 |",
        "|---------|----------|",
    ]
    for ticker, cnt in list(stats.get("top_tickers", {}).items())[:10]:
        lines.append(f"| {ticker} | {cnt}회 |")

    if not tl_df.empty:
        lines += [
            "",
            "## 📋 최근 거래 내역 (20건)\n",
            "| 종목 | 매수일 | 매도일 | 매수가 | 매도가 | 수익률 | 보유일 |",
            "|------|--------|--------|--------|--------|--------|--------|",
        ]
        for _, row in tl_df.tail(20).iterrows():
            lines.append(
                f"| {row['ticker']} | {row['buy_date']} | {row['sell_date']} "
                f"| {row['buy_price']:,} | {row['sell_price']:,} "
                f"| {arrow(row['pnl_pct'])}{abs(row['pnl_pct']):.2f}% "
                f"| {row['hold_days']}일 |"
            )

    lines += [
        "",
        "---",
        "_스크리닝 조건: 5MA>20MA + 52주고가 -20%이내 + 거래량 20일평균 150%_",
        "_매매 규칙: 조건 통과 당일 종가 매수 → 20거래일 후 종가 매도_",
        "_수수료: 편도 0.015%, 최대 10종목 균등 분배_",
    ]

    path = REPORTS_DIR / f"backtest_{start[:4]}_{end[:4]}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[bt] 리포트 저장: {path}")
